// notify-new-zip — Edge Function déclenchée par un Database Webhook Supabase
// sur storage.objects (INSERT). Filtre les ZIP du bucket ffbb-archive et
// déclenche le workflow GitHub Actions "ingest.yml" via repository_dispatch.
//
// Secrets requis (supabase secrets set) :
//   GITHUB_TOKEN  — PAT avec la permission "Contents: write" (ou scope repo classique)
//   GITHUB_REPO   — ex. jonathan-varani/ffbb-stats

// deno-lint-ignore-file no-explicit-any
Deno.serve(async (req: Request) => {
  const payload = await req.json().catch(() => null);
  const record = payload?.record;

  if (!record || payload.type !== 'INSERT') {
    return new Response('ignored (not an insert)', { status: 200 });
  }
  if (record.bucket_id !== 'ffbb-archive' || !/\.zip$/i.test(record.name || '')) {
    return new Response('ignored (not a zip in ffbb-archive)', { status: 200 });
  }

  const githubToken = Deno.env.get('GITHUB_TOKEN');
  const githubRepo = Deno.env.get('GITHUB_REPO');
  if (!githubToken || !githubRepo) {
    console.error('GITHUB_TOKEN / GITHUB_REPO manquants');
    return new Response('server misconfigured', { status: 500 });
  }

  const res = await fetch(`https://api.github.com/repos/${githubRepo}/dispatches`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${githubToken}`,
      Accept: 'application/vnd.github+json',
      'Content-Type': 'application/json',
      'X-GitHub-Api-Version': '2022-11-28',
    },
    body: JSON.stringify({
      event_type: 'new_zip',
      client_payload: { path: record.name },
    }),
  });

  if (!res.ok) {
    const body = await res.text();
    console.error('GitHub dispatch failed', res.status, body);
    return new Response(`github dispatch failed: ${res.status}`, { status: 502 });
  }

  return new Response(`dispatched ingest for ${record.name}`, { status: 200 });
});
