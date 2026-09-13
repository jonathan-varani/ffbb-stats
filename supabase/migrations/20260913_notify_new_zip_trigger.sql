-- Déclenche l'Edge Function notify-new-zip à chaque nouvel objet dans Storage.
-- Remplace le mécanisme "Database Webhook" du Studio par un trigger pg_net direct :
-- ce projet n'avait pas encore le schéma legacy supabase_functions/http_request.

create extension if not exists pg_net with schema extensions;

create or replace function public.notify_new_zip()
returns trigger
language plpgsql
security definer
set search_path = public
as $fn$
begin
  perform net.http_post(
    url := 'https://xbaoyelmtzpxwqdxkync.supabase.co/functions/v1/notify-new-zip',
    headers := jsonb_build_object(
      'Content-Type', 'application/json',
      -- Clé anon (publique) : requise uniquement pour passer la vérification JWT
      -- de l'Edge Function, pas pour l'autorisation métier.
      -- Remplacer <ANON_KEY> par la clé anon du projet
      -- (Project Settings > API, ou `supabase projects api-keys --project-ref <ref>`).
      'Authorization', 'Bearer <ANON_KEY>'
    ),
    body := jsonb_build_object(
      'type', 'INSERT',
      'table', 'objects',
      'schema', 'storage',
      'record', to_jsonb(NEW)
    )
  );
  return NEW;
end;
$fn$;

drop trigger if exists notify_new_zip on storage.objects;
create trigger notify_new_zip
after insert on storage.objects
for each row
execute function public.notify_new_zip();
