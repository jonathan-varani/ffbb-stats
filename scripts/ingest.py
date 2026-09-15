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
from datetime import datetime, timezone

from supabase import create_client
from parse_recap import parse_recap_pdf
from parse_resume import parse_resume_pdf


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

def extract_stats_pdf(zip_path, dest_dir):
    """Cherche le PDF exploitable dans le ZIP e-Marque.

    Ancien format régional : 'Recapitulatif_*.pdf' (texte natif, parse_recap.py).
    Nouveau format Fédérale : 'resume_*.pdf' (vectoriel, parse_resume.py).
    Renvoie (chemin_local, format) avec format in {'recap', 'resume'}.
    """
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        recap = [n for n in names if re.match(r'^R[ée]capitulatif_', os.path.basename(n), re.IGNORECASE)]
        resume = [n for n in names if re.match(r'^resume_', os.path.basename(n), re.IGNORECASE)]

        member, fmt = (recap[0], 'recap') if recap else (resume[0], 'resume') if resume else (None, None)
        if not member:
            raise ValueError(
                f"aucun PDF 'Recapitulatif_*' ni 'resume_*' trouvé dans {zip_path}"
            )
        out_path = os.path.join(dest_dir, os.path.basename(member))
        with zf.open(member) as src, open(out_path, 'wb') as dst:
            dst.write(src.read())
        return out_path, fmt


def download_from_storage(sb, storage_path, dest_dir):
    bucket = os.environ.get('SUPABASE_BUCKET', 'ffbb-archive')
    data = sb.storage.from_(bucket).download(storage_path)
    out_path = os.path.join(dest_dir, os.path.basename(storage_path))
    with open(out_path, 'wb') as f:
        f.write(data)
    return out_path


# ─────────────────────────────────────────────
# Classement du ZIP après traitement (archives/ ou quarantaine/)
# ─────────────────────────────────────────────

def _move_object(sb, bucket, from_path, to_path):
    """Déplace un objet dans le bucket. Best-effort : ne lève pas si la
    destination existe déjà (retente avec un suffixe) ou si le déplacement
    échoue, pour ne jamais masquer le résultat réel de l'ingestion."""
    try:
        sb.storage.from_(bucket).move(from_path, to_path)
        return to_path
    except Exception as e:
        alt = f"{to_path}.{int(datetime.now(timezone.utc).timestamp())}"
        try:
            sb.storage.from_(bucket).move(from_path, alt)
            return alt
        except Exception as e2:
            print(f"⚠ Impossible de déplacer {from_path} : {e2}")
            return None


def archive_zip(sb, storage_path):
    bucket = os.environ.get('SUPABASE_BUCKET', 'ffbb-archive')
    filename = os.path.basename(storage_path)
    date_integration = datetime.now(timezone.utc).date().isoformat()
    dest = f"archives/{date_integration}/{filename}"
    moved = _move_object(sb, bucket, storage_path, dest)
    if moved:
        print(f"Archivé : {moved}")


def quarantine_zip(sb, storage_path, reason):
    bucket = os.environ.get('SUPABASE_BUCKET', 'ffbb-archive')
    filename = os.path.basename(storage_path)
    dest = f"quarantaine/{filename}"
    moved = _move_object(sb, bucket, storage_path, dest)
    print(f"KO : {reason}")
    if moved:
        print(f"Mis en quarantaine : {moved}")


# ─────────────────────────────────────────────
# Upserts (chacun renvoie l'id de la ligne)
# ─────────────────────────────────────────────

def upsert_equipe(sb, nom):
    # RPC upsert_equipe : consulte equipes_alias (cas EQUIPE A/B non
    # devinables), sinon upsert sur nom_normalise (déduplication des
    # variantes d'espacement du suffixe d'équipe, ex. "- 1" vs "-1").
    r = sb.rpc('upsert_equipe', {'p_nom': nom}).execute()
    return r.data


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
    num_rencontre = match.get('num_rencontre')
    if num_rencontre:
        # Format Fédérale : le num_rencontre est la clé d'idempotence fiable
        # (le nom du ZIP ne contient plus de date).
        payload['num_rencontre'] = num_rencontre
        payload['extraction_validee'] = match.get('extraction_validee', True)
        on_conflict = 'num_rencontre'
    else:
        on_conflict = 'date,equipe_domicile_id,equipe_visiteur_id'
    r = sb.table('matchs').upsert(payload, on_conflict=on_conflict).execute()
    return r.data[0]['id']


def upsert_joueur(sb, nom, prenom, equipe_id):
    # RPC upsert_joueur : upsert sur (nom_normalise, prenom_normalise,
    # equipe_id) pour dedupliquer les variantes d'accents/casse
    # (ex. "FRANÇOIS" vs "FRANCOIS").
    r = sb.rpc('upsert_joueur', {
        'p_nom': nom, 'p_prenom': prenom, 'p_equipe_id': equipe_id,
    }).execute()
    return r.data


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
    if 'titulaire' in j:
        payload['titulaire'] = j['titulaire']
    if j.get('tps_jeu') is not None:
        payload['tps_jeu'] = j['tps_jeu']
    sb.table('stats_joueurs').upsert(payload, on_conflict='match_id,joueur_id').execute()


# ─────────────────────────────────────────────
# Pipeline principal
# ─────────────────────────────────────────────

def ingest(zip_path=None, archive_path=None, storage_path=None):
    sb = get_client()
    erreurs = []

    try:
        with tempfile.TemporaryDirectory() as tmp:
            if storage_path:
                zip_path = download_from_storage(sb, storage_path, tmp)
                archive_path = archive_path or storage_path
            pdf_path, fmt = extract_stats_pdf(zip_path, tmp)
            if fmt == 'recap':
                data = parse_recap_pdf(pdf_path)
            else:
                data = parse_resume_pdf(pdf_path)
                validation = data.get('validation', {})
                if not validation.get('ok', True):
                    print(f"⚠ Extraction non validée par les checksums : {validation.get('erreurs')}")
                data['match']['extraction_validee'] = validation.get('ok', True)

        match, joueurs = data['match'], data['joueurs']

        equipe_dom_id = upsert_equipe(sb, match['equipe_domicile'])
        equipe_vis_id = upsert_equipe(sb, match['equipe_visiteur'])

        match_id = upsert_match(
            sb, match, equipe_dom_id, equipe_vis_id,
            archive_path or os.path.basename(zip_path)
        )
    except Exception as e:
        # Échec avant même d'avoir un match exploitable : ZIP illisible,
        # PDF absent/corrompu, format inattendu... → quarantaine.
        if storage_path:
            quarantine_zip(sb, storage_path, str(e))
        raise

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

    # Le match a été intégré (même avec des erreurs joueur ponctuelles) : archivé.
    if storage_path:
        archive_zip(sb, storage_path)

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
