-- Classement des marqueurs — vues de filtres + fonction d'agrégation
-- À exécuter dans Supabase Studio > SQL Editor.

-- 1. Listes pour les filtres (championnats, poules par championnat)
create or replace view public.v_championnats as
select distinct championnat
from matchs
where championnat is not null and championnat <> ''
order by championnat;

create or replace view public.v_poules as
select distinct championnat, poule
from matchs
where poule is not null and poule <> ''
order by championnat, poule;

-- 2. Classement agrégé, filtrable par championnat / poule / équipe.
--    L'agrégation se fait côté base (pas côté client) pour rester scalable
--    quand plusieurs championnats et beaucoup de matchs s'accumuleront.
create or replace function public.classement_marqueurs(
  p_championnat text default null,
  p_poule       text default null,
  p_equipe      text default null
)
returns table (
  joueur_id            uuid,
  nom                  text,
  prenom               text,
  equipe_id            uuid,
  equipe_nom           text,
  matchs_joues         bigint,
  points_total         bigint,
  moyenne_points       numeric,
  deux_pts_reussis     bigint,
  trois_pts_reussis    bigint,
  lf_reussis           bigint,
  rebonds_total        bigint,
  passes_decisives     bigint,
  fautes_personnelles  bigint
)
language sql
stable
security definer
set search_path = public
as $$
  select
    j.id, j.nom, j.prenom,
    eq.id, eq.nom,
    count(*)::bigint                              as matchs_joues,
    sum(s.points_total)::bigint                   as points_total,
    round(avg(s.points_total)::numeric, 1)        as moyenne_points,
    sum(s.deux_pts_reussis)::bigint,
    sum(s.trois_pts_reussis)::bigint,
    sum(s.lf_reussis)::bigint,
    sum(s.rebonds_total)::bigint,
    sum(s.passes_decisives)::bigint,
    sum(s.fautes_personnelles)::bigint
  from stats_joueurs s
  join joueurs j on j.id = s.joueur_id
  join equipes eq on eq.id = s.equipe_id
  join matchs  m on m.id = s.match_id
  where (p_championnat is null or m.championnat = p_championnat)
    and (p_poule       is null or m.poule       = p_poule)
    and (p_equipe      is null or eq.nom        = p_equipe)
  group by j.id, j.nom, j.prenom, eq.id, eq.nom
  order by points_total desc;
$$;

-- 3. Droits de lecture pour la clé anon (utilisée par le front-end public).
--    Lecture seule : aucun droit d'écriture donné à anon.
grant select on public.v_championnats to anon;
grant select on public.v_poules to anon;
grant select on public.equipes to anon;
grant execute on function public.classement_marqueurs(text, text, text) to anon;
