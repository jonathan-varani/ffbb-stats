"""
parse_resume.py — Parser du PDF `resume_*.pdf` e-Marque FFBB (format Fédérale).

Ce format est VECTORIEL sans couche texte : pdfplumber ne renvoie rien.
On rend la page en image haute résolution puis on la lit via Claude (vision),
et on valide chaque valeur par checksums arithmétiques.

Prérequis :
    pip install pymupdf pillow anthropic
    export ANTHROPIC_API_KEY=sk-ant-...

Usage :
    python parse_resume.py <resume.pdf>            # JSON sur stdout
    python parse_resume.py <resume.pdf> --debug    # + images de debug
"""

import os
import io
import re
import sys
import json
import base64

import pymupdf
from PIL import Image
from anthropic import Anthropic

# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────

MODEL = os.environ.get("FFBB_MODEL", "claude-sonnet-4-5")
# Alternative économique : claude-haiku-4-5 (~3x moins cher)

DPI = 200

# Le template FFBB est fixe (12 lignes joueurs par équipe),
# donc ces bandes de découpe sont stables d'un match à l'autre.
BANDES = {
    "entete":    (0.00, 0.20),
    "locaux":    (0.17, 0.55),
    "visiteurs": (0.51, 0.95),
}

PROMPT = """Tu lis une feuille de match de basket FFBB (application e-Marque).

Image 1 = en-tête du match.
Image 2 = tableau de l'équipe LOCAUX.
Image 3 = tableau de l'équipe VISITEURS.

Renvoie UNIQUEMENT un objet JSON, sans texte autour, sans bloc markdown :

{
  "num_rencontre": "103",
  "date": "20/09/25",
  "heure": "20:00",
  "lieu": "SAINTE-MARIE-AUX-CHENES",
  "championnat": "NM3",
  "poule": "G",
  "arbitre1": "...",
  "arbitre2": "...",
  "equipe_locaux": "nom complet de l'Équipe A",
  "equipe_visiteurs": "nom complet de l'Équipe B",
  "locaux": {
    "total_equipe": {"points":71,"tirs":25,"trois_pts":6,"deux_int":17,"deux_ext":2,"lf":15,"fautes":20},
    "joueurs": [
      {"maillot":0,"nom":"MAMERI","prenom":"Yaël","titulaire":true,
       "tps_jeu":"25:01","points":13,"tirs":5,"trois_pts":2,
       "deux_int":2,"deux_ext":1,"lf":1,"fautes":3}
    ]
  },
  "visiteurs": { ... même structure ... }
}

RÈGLES IMPORTANTES :
- Les colonnes sont, dans l'ordre : N° Maillot | NOM Prénom | 5 de départ | Tps de jeu |
  Nb Pts Marqués | Nb Tirs Réussis | 3 Pts Réussis | 2 Int Réussis | 2 Ext Réussis |
  LF Réussis | Ftes Com.
- "titulaire" = true si la case "5 de départ" contient une croix (X), sinon false.
- Le nom est écrit "NOM, Prénom" : sépare sur la virgule.
- IGNORE les lignes joueur entièrement vides.
- N'inclus PAS les lignes Total Banc / Total 5 de Départ / Mi-temps / Prolongation :
  seulement "Total Équipe".
- Ne devine JAMAIS une valeur. Si une cellule est illisible, mets null.
- Recopie les chiffres exactement tels qu'affichés, sans les recalculer."""


# ─────────────────────────────────────────────
# Rendu & découpe
# ─────────────────────────────────────────────

def render_bandes(pdf_path, debug=False):
    """Rend la page 1 et la découpe en 3 bandes (entête, locaux, visiteurs)."""
    doc = pymupdf.open(pdf_path)
    pix = doc[0].get_pixmap(dpi=DPI)
    page = Image.open(io.BytesIO(pix.tobytes("png")))
    doc.close()

    w, h = page.size
    out = {}
    for nom, (haut, bas) in BANDES.items():
        crop = page.crop((0, int(h * haut), w, int(h * bas)))
        out[nom] = crop
        if debug:
            crop.save(f"debug_{nom}.png")
    return out


def to_block(img):
    """Encode une image PIL en bloc image pour l'API Anthropic."""
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.standard_b64encode(buf.getvalue()).decode(),
        },
    }


# ─────────────────────────────────────────────
# Extraction via Claude
# ─────────────────────────────────────────────

def extraire(bandes, corrections=None):
    """Envoie les 3 bandes à Claude et récupère le JSON."""
    client = Anthropic()

    contenu = [
        {"type": "text", "text": "Image 1 — en-tête :"},
        to_block(bandes["entete"]),
        {"type": "text", "text": "Image 2 — LOCAUX :"},
        to_block(bandes["locaux"]),
        {"type": "text", "text": "Image 3 — VISITEURS :"},
        to_block(bandes["visiteurs"]),
        {"type": "text", "text": PROMPT},
    ]

    if corrections:
        contenu.append({
            "type": "text",
            "text": (
                "Ta lecture précédente comportait des incohérences arithmétiques.\n"
                "Relis attentivement les cellules concernées :\n- "
                + "\n- ".join(corrections)
            ),
        })

    rep = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        messages=[{"role": "user", "content": contenu}],
    )

    txt = rep.content[0].text.strip()
    txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt)
    return json.loads(txt)


# ─────────────────────────────────────────────
# Validation par checksums
# ─────────────────────────────────────────────

def points_theoriques(j):
    """pts = 3×(3pts) + 2×(2int + 2ext) + LF"""
    return 3 * j["trois_pts"] + 2 * (j["deux_int"] + j["deux_ext"]) + j["lf"]


def valider(data):
    """Vérifie la cohérence arithmétique. Renvoie la liste des anomalies."""
    erreurs = []
    colonnes = ["points", "tirs", "trois_pts", "deux_int", "deux_ext", "lf", "fautes"]

    for cote in ("locaux", "visiteurs"):
        bloc = data.get(cote, {})
        joueurs = bloc.get("joueurs", [])
        total = bloc.get("total_equipe", {})

        for j in joueurs:
            ident = f"{cote} #{j.get('maillot')} {j.get('nom')}"

            # Cellule illisible
            manquants = [c for c in colonnes if j.get(c) is None]
            if manquants:
                erreurs.append(f"{ident} : valeur illisible ({', '.join(manquants)})")
                continue

            # Checksum 1 : les points doivent découler des réussites
            attendu = points_theoriques(j)
            if j["points"] != attendu:
                erreurs.append(
                    f"{ident} : points={j['points']} mais "
                    f"3×{j['trois_pts']} + 2×({j['deux_int']}+{j['deux_ext']}) "
                    f"+ {j['lf']} = {attendu}"
                )

            # Checksum 2 : nb de tirs réussis = somme des paniers
            tirs = j["trois_pts"] + j["deux_int"] + j["deux_ext"]
            if j["tirs"] != tirs:
                erreurs.append(f"{ident} : tirs={j['tirs']} au lieu de {tirs}")

        # Checksum 3 : somme des joueurs = total équipe
        if total and joueurs and not any(j.get(c) is None for j in joueurs for c in colonnes):
            for col in colonnes:
                somme = sum(j[col] for j in joueurs)
                if total.get(col) is not None and somme != total[col]:
                    erreurs.append(
                        f"{cote} : total {col} = {total[col]} "
                        f"mais somme des joueurs = {somme}"
                    )

    return erreurs


# ─────────────────────────────────────────────
# Normalisation vers le schéma Supabase
# ─────────────────────────────────────────────

def normaliser(data):
    """Convertit vers la structure attendue par ingest.py."""
    from datetime import datetime

    try:
        date_iso = datetime.strptime(data["date"], "%d/%m/%y").date().isoformat()
    except (ValueError, KeyError):
        date_iso = data.get("date", "")

    loc = data["locaux"]["total_equipe"]
    vis = data["visiteurs"]["total_equipe"]

    match = {
        "num_rencontre":   data.get("num_rencontre", ""),
        "date":            date_iso,
        "lieu":            data.get("lieu", ""),
        "championnat":     data.get("championnat", ""),
        "poule":           data.get("poule", ""),
        "arbitre1":        data.get("arbitre1") or "",
        "arbitre2":        data.get("arbitre2") or "",
        "equipe_domicile": data.get("equipe_locaux", ""),
        "equipe_visiteur": data.get("equipe_visiteurs", ""),
        "score_domicile":  loc.get("points", 0),
        "score_visiteur":  vis.get("points", 0),
    }

    joueurs = []
    for cote, cle_equipe in (("locaux", "equipe_locaux"),
                             ("visiteurs", "equipe_visiteurs")):
        for j in data[cote]["joueurs"]:
            joueurs.append({
                "equipe":              data.get(cle_equipe, ""),
                "numero_maillot":      j["maillot"],
                "nom":                 j["nom"],
                "prenom":              j["prenom"],
                "titulaire":           j.get("titulaire", False),
                "tps_jeu":             j.get("tps_jeu"),
                "points_total":        j["points"],
                "trois_pts_reussis":   j["trois_pts"],
                # le schéma fusionne intérieur + extérieur
                "deux_pts_reussis":    j["deux_int"] + j["deux_ext"],
                "lf_reussis":          j["lf"],
                "fautes_personnelles": j["fautes"],
                # non fournis par le résumé e-Marque
                "rebonds_offensifs":   0,
                "rebonds_defensifs":   0,
                "passes_decisives":    0,
                "interceptions":       0,
                "contres":             0,
                "balles_perdues":      0,
            })

    return {"match": match, "joueurs": joueurs}


# ─────────────────────────────────────────────
# Point d'entrée
# ─────────────────────────────────────────────

def parse_resume_pdf(pdf_path, debug=False, max_essais=2):
    bandes = render_bandes(pdf_path, debug=debug)

    corrections = None
    for essai in range(1, max_essais + 1):
        data = extraire(bandes, corrections)
        erreurs = valider(data)

        if not erreurs:
            resultat = normaliser(data)
            resultat["validation"] = {"ok": True, "essais": essai}
            return resultat

        print(f"[essai {essai}/{max_essais}] {len(erreurs)} anomalie(s)",
              file=sys.stderr)
        for e in erreurs:
            print(f"  ! {e}", file=sys.stderr)
        corrections = erreurs

    # Échec après retries : on renvoie quand même, mais signalé
    resultat = normaliser(data)
    resultat["validation"] = {"ok": False, "essais": max_essais, "erreurs": erreurs}
    return resultat


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print("Usage : python parse_resume.py <resume.pdf> [--debug]")
        sys.exit(1)

    res = parse_resume_pdf(args[0], debug="--debug" in sys.argv)
    print(json.dumps(res, ensure_ascii=False, indent=2))

    if not res["validation"]["ok"]:
        print("\n⚠  Validation échouée — données à vérifier avant ingestion.",
              file=sys.stderr)
        sys.exit(2)
