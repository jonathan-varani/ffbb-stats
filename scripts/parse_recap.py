"""
parse_recap.py — Parser du PDF Récapitulatif e-Marque FFBB
Usage : python parse_recap.py <chemin_vers_recap.pdf>
Retourne un dict JSON avec match, equipes, joueurs et stats.
"""

import re
import sys
import json
import pdfplumber
from datetime import datetime


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def to_int(val):
    """Convertit une cellule en entier (0 si vide/None)."""
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


def clean_doubled(text):
    """Supprime les lettres doublées (artefact PDF e-marque : 'RReenncc' → 'Renc')."""
    return re.sub(r'(.)\1', r'\1', text)


def split_nom_prenom(full_name):
    """
    Le PDF stocke 'NOM PRENOM' en majuscules.
    On prend le premier mot comme NOM, le reste comme PRENOM.
    Ex : 'D ELLENA GREGORY' → nom='D ELLENA', prenom='GREGORY'
    Heuristique : le prénom est le dernier mot.
    """
    parts = full_name.strip().split()
    if len(parts) == 1:
        return parts[0], ''
    prenom = parts[-1]
    nom = ' '.join(parts[:-1])
    return nom, prenom


# ─────────────────────────────────────────────
# Parser principal
# ─────────────────────────────────────────────

def parse_recap_pdf(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        raw_text = page.extract_text() or ''
        tables   = page.extract_tables()

    # ── 1. Infos du match ──────────────────────────────────────────

    # Numéro de rencontre + date + heure + lieu
    # Le texte a des lettres doublées dans cette zone, on nettoie
    m = re.search(
        r'N[°o]\s*(\d+)\s+Date\s+(\d{2}/\d{2}/\d{2})\s+Heure\s+([\d:]+)\s+Lieu\s+(.+)',
        clean_doubled(raw_text)
    )
    num_rencontre = m.group(1) if m else ''
    date_str      = m.group(2) if m else ''
    lieu          = m.group(4).strip() if m else ''
    try:
        date = datetime.strptime(date_str, '%d/%m/%y').date().isoformat()
    except ValueError:
        date = date_str

    # Championnat + Poule (Table 1, cellule 0 : "CHAMPIONNAT REG :\nGES\nPNM")
    championnat, poule = '', ''
    if tables and tables[0]:
        cell = tables[0][0][0] or ''
        lines = [l.strip() for l in cell.split('\n') if l.strip()]
        # lines = ['CHAMPIONNAT REG :', 'GES', 'PNM']
        if len(lines) >= 3:
            championnat = lines[1]
            poule       = lines[2]

    # Arbitres
    m_arb = re.search(r'1er arbitre\s+(.+?)\s+2e arbitre\s+(.+)', clean_doubled(raw_text))
    arbitre1 = m_arb.group(1).strip() if m_arb else ''
    arbitre2 = m_arb.group(2).strip() if m_arb else ''

    # Scores — ligne "Équipe A <nom> <score>"
    score_dom, score_vis = 0, 0
    m_a = re.search(r'Équipe A\s+.+?\s+(\d+)', raw_text)
    m_b = re.search(r'Équipe B\s+.+?\s+(\d+)', raw_text)
    if m_a: score_dom = int(m_a.group(1))
    if m_b: score_vis = int(m_b.group(1))

    # ── 2. Noms des équipes ────────────────────────────────────────
    # Cherche le nom complet AVANT chaque table de joueurs dans le texte brut
    # Format : "\nNOM EQUIPE\nN° 5 de Tps..."
    team_names = re.findall(r'\n([A-ZÀÂÉÈÊËÎÏÔÙÛÜÇ][A-ZÀÂÉÈÊËÎÏÔÙÛÜÇ\s\-]+?)\nN°\s+5 de', raw_text)
    equipe_dom = team_names[0].strip() if len(team_names) > 0 else 'EQUIPE A'
    equipe_vis = team_names[1].strip() if len(team_names) > 1 else 'EQUIPE B'

    match = {
        'num_rencontre'   : num_rencontre,
        'date'            : date,
        'lieu'            : lieu,
        'championnat'     : championnat,
        'poule'           : poule,
        'arbitre1'        : arbitre1,
        'arbitre2'        : arbitre2,
        'equipe_domicile' : equipe_dom,
        'equipe_visiteur' : equipe_vis,
        'score_domicile'  : score_dom,
        'score_visiteur'  : score_vis,
    }

    # ── 3. Stats joueurs ───────────────────────────────────────────
    # tables[1] = joueurs équipe dom, tables[2] = joueurs équipe vis
    joueurs = []

    equipes_tables = [
        (equipe_dom, tables[1] if len(tables) > 1 else []),
        (equipe_vis, tables[2] if len(tables) > 2 else []),
    ]

    for equipe_nom, table in equipes_tables:
        for row in table:
            # Ligne de joueur : col[0] est un numéro de maillot
            if not row or not row[0]:
                continue
            try:
                num_maillot = int(row[0])
            except (ValueError, TypeError):
                continue  # ligne Total, Entraîneur, entête

            nom_complet = row[1] or ''
            if not nom_complet.strip():
                continue

            nom, prenom = split_nom_prenom(nom_complet)

            # Colonnes stats (indices fixes dans la table e-marque)
            # [5]=pts [6]=tirs_total [7]=3pts [8]=2ext [9]=2int [10]=LF [11]=fautes
            pts          = to_int(row[5])
            trois_pts    = to_int(row[7])
            deux_ext     = to_int(row[8])
            deux_int     = to_int(row[9])
            lf           = to_int(row[10])
            fautes       = to_int(row[11])

            deux_pts = deux_ext + deux_int  # on fusionne ext+int

            joueurs.append({
                'equipe'              : equipe_nom,
                'numero_maillot'      : num_maillot,
                'nom'                 : nom,
                'prenom'              : prenom,
                'points_total'        : pts,
                'trois_pts_reussis'   : trois_pts,
                'deux_pts_reussis'    : deux_pts,
                'lf_reussis'          : lf,
                'fautes_personnelles' : fautes,
                # Rebonds, passes, interceptions, contres, balles perdues
                # → non disponibles dans le Récapitulatif (voir Historique PDF)
                'rebonds_offensifs'   : 0,
                'rebonds_defensifs'   : 0,
                'passes_decisives'    : 0,
                'interceptions'       : 0,
                'contres'             : 0,
                'balles_perdues'      : 0,
            })

    return {
        'match'   : match,
        'joueurs' : joueurs,
    }


# ─────────────────────────────────────────────
# Point d'entrée
# ─────────────────────────────────────────────

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage : python parse_recap.py <recap.pdf>")
        sys.exit(1)

    result = parse_recap_pdf(sys.argv[1])
    print(json.dumps(result, ensure_ascii=False, indent=2))
