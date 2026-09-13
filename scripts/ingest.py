"""
ingest.py — Ingestion d'une archive ZIP e-Marque FFBB dans Supabase.

Usage :
  python ingest.py <chemin_vers_match.zip> [--archive-path <chemin_storage>]
  python ingest.py --storage-path <chemin_dans_le_bucket>

Variables d'environnement requises :
  SUPABASE_URL          ex. https://xbaoyelmtzpxwqdxkync.supabase.co
  SUPABASE_SERVICE_KEY  clé service_role (bypasse RLS, ne jamais l'exposer côté client)
  SUPABASE_BUCKET       optionnel, défaut 'ffbb-archive'
"""

import os
import re
import sys
import zipfile
import argparse
import tempfile

from supabase import create_client
from parse_recap import parse_recap_pdf


# ─────────────────────────────────────────────
# Connexion Supabase
# ─────────────────────────────────────────────

def get_client():
    url = os.environ.get('SUPABASE_URL')
    key = os.environ.get('SUPABASE_SERVICE_KEY')
    if not url or not key:
        sys.exit("Erreur : variables SUPABASE_URL et SUPABASE_SERVICE_KEY requises.")
    return create_client(url, key)


# ─────────────────────────────────────────────
# Extraction du PDF Récapitulatif depuis le ZIP
# ─────────────────────────────────────────────

def extract_recap_pdf(zip_path, dest_dir):
    with zipfile.ZipFile(zip_path) as zf:
        candidates = [
            n for n in zf.namelist()
            if re.match(r'^R[ée]capitulatif_', os.path.basename(n), re.IGNORECASE)
        ]
        if not candidates:
            sys.exit(f"Erreur : aucun PDF 'Recapitulatif_*' trouvé dans {zip_path}")
        member = candidates[0]
        out_path = os.path.join(dest_dir, os.path.basename(member))
        with zf.open(member) as src, open(out_path, 'wb') as dst:
            dst.write(src.read())
        return out_path


def download_from_storage(sb, storage_path, dest_dir):
    bucket = os.environ.get('SUPABASE_BUCKET', 'ffbb-archive')
    data = sb.storage.from_(bucket).download(storage_path)
    out_path = os.path.join(dest_dir, os.path.basename(storage_path))
    with open(out_path, 'wb') as f:
        f.write(data)
    return out_path


# ─────────────────────────────────────────────
# Upserts (chacun renvoie l'id de la ligne)
# ─────────────────────────────────────────────

def upsert_equipe(sb, nom):
    r = sb.table('equipes').upsert({'nom': nom}, on_conflict='nom').execute()
    return r.data[0]['id']


def upsert_match(sb, match, equipe_dom_id, equipe_vis_id, archive_path):
    payload = {
        'date': match['date'],
        'lieu': match['lieu'],
        'championnat': match['championnat'],
        'poule': match['poule'],
        'equipe_domicile_id': equipe_dom_id,
        'equipe_visiteur_id': equipe_vis_id,
        'score_domicile': match['score_domicile'],
        'score_visiteur': match['score_visiteur'],
        'arbitre1': match['arbitre1'],
        'arbitre2': match['arbitre2'],
        'archive_path': archive_path,
    }
    r = sb.table('matchs').upsert(
        payload, on_conflict='date,equipe_domicile_id,equipe_visiteur_id'
    ).execute()
    return r.data[0]['id']


def upsert_joueur(sb, nom, prenom, equipe_id):
    payload = {'nom': nom, 'prenom': prenom, 'equipe_id': equipe_id}
    r = sb.table('joueurs').upsert(payload, on_conflict='nom,prenom,equipe_id').execute()
    return r.data[0]['id']


def upsert_stats(sb, match_id, joueur_id, equipe_id, j):
    payload = {
        'match_id': match_id,
        'joueur_id': joueur_id,
        'equipe_id': equipe_id,
        'numero_maillot': j['numero_maillot'],
        'lf_reussis': j['lf_reussis'],
        'deux_pts_reussis': j['deux_pts_reussis'],
        'trois_pts_reussis': j['trois_pts_reussis'],
        'rebonds_offensifs': j['rebonds_offensifs'],
        'rebonds_defensifs': j['rebonds_defensifs'],
        'passes_decisives': j['passes_decisives'],
        'interceptions': j['interceptions'],
        'contres': j['contres'],
        'balles_perdues': j['balles_perdues'],
        'fautes_personnelles': j['fautes_personnelles'],
        # points_total est une colonne GENERATED côté DB : ne pas l'envoyer.
    }
    sb.table('stats_joueurs').upsert(payload, on_conflict='match_id,joueur_id').execute()


# ─────────────────────────────────────────────
# Pipeline principal
# ─────────────────────────────────────────────

def ingest(zip_path=None, archive_path=None, storage_path=None):
    sb = get_client()
    erreurs = []

    with tempfile.TemporaryDirectory() as tmp:
        if storage_path:
            zip_path = download_from_storage(sb, storage_path, tmp)
            archive_path = archive_path or storage_path
        pdf_path = extract_recap_pdf(zip_path, tmp)
        data = parse_recap_pdf(pdf_path)

    match, joueurs = data['match'], data['joueurs']

    equipe_dom_id = upsert_equipe(sb, match['equipe_domicile'])
    equipe_vis_id = upsert_equipe(sb, match['equipe_visiteur'])

    match_id = upsert_match(
        sb, match, equipe_dom_id, equipe_vis_id,
        archive_path or os.path.basename(zip_path)
    )

    equipe_id_by_nom = {
        match['equipe_domicile']: equipe_dom_id,
        match['equipe_visiteur']: equipe_vis_id,
    }

    nb_ok = 0
    for j in joueurs:
        try:
            equipe_id = equipe_id_by_nom[j['equipe']]
            joueur_id = upsert_joueur(sb, j['nom'], j['prenom'], equipe_id)
            upsert_stats(sb, match_id, joueur_id, equipe_id, j)
            nb_ok += 1
        except Exception as e:
            erreurs.append(f"{j.get('nom','?')} {j.get('prenom','?')} : {e}")

    # ── Résumé ──────────────────────────────────────────────────────
    print(f"Match : {match['equipe_domicile']} {match['score_domicile']} - "
          f"{match['score_visiteur']} {match['equipe_visiteur']} ({match['date']})")
    print(f"Match ID       : {match_id}")
    print(f"Joueurs ingérés : {nb_ok}/{len(joueurs)}")
    if erreurs:
        print(f"Erreurs ({len(erreurs)}) :")
        for e in erreurs:
            print(f"  - {e}")

    return {'match_id': match_id, 'joueurs_ok': nb_ok, 'joueurs_total': len(joueurs), 'erreurs': erreurs}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Ingère un ZIP e-Marque dans Supabase.")
    parser.add_argument('zip_path', nargs='?', help="Chemin vers un fichier ZIP e-Marque local")
    parser.add_argument('--archive-path', help="Chemin de référence à stocker dans matchs.archive_path")
    parser.add_argument('--storage-path', help="Chemin dans le bucket Supabase Storage à télécharger et ingérer")
    args = parser.parse_args()

    if not args.zip_path and not args.storage_path:
        parser.error("fournir soit un chemin ZIP local, soit --storage-path")

    result = ingest(args.zip_path, args.archive_path, args.storage_path)
    sys.exit(1 if result['erreurs'] else 0)
