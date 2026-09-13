-- ============================================
-- FFBB Stats — Schéma PostgreSQL (Supabase)
-- ============================================

-- 1. Équipes
CREATE TABLE IF NOT EXISTS equipes (
  id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  nom         TEXT NOT NULL UNIQUE,
  created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Matchs
CREATE TABLE IF NOT EXISTS matchs (
  id                   UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  date                 DATE NOT NULL,
  lieu                 TEXT,
  championnat          TEXT,
  poule                TEXT,
  equipe_domicile_id   UUID REFERENCES equipes(id),
  equipe_visiteur_id   UUID REFERENCES equipes(id),
  score_domicile       INT,
  score_visiteur       INT,
  arbitre1             TEXT,
  arbitre2             TEXT,
  archive_path         TEXT,  -- chemin dans Supabase Storage
  created_at           TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(date, equipe_domicile_id, equipe_visiteur_id)
);

-- 3. Joueurs
CREATE TABLE IF NOT EXISTS joueurs (
  id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  nom         TEXT NOT NULL,
  prenom      TEXT,
  equipe_id   UUID REFERENCES equipes(id),
  created_at  TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(nom, prenom, equipe_id)
);

-- 4. Stats par joueur par match
CREATE TABLE IF NOT EXISTS stats_joueurs (
  id                    UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  match_id              UUID REFERENCES matchs(id) ON DELETE CASCADE,
  joueur_id             UUID REFERENCES joueurs(id),
  equipe_id             UUID REFERENCES equipes(id),
  numero_maillot        INT,
  titulaire             BOOLEAN DEFAULT FALSE,
  -- Points (réussites uniquement — e-marque ne fournit pas les tentatives)
  lf_reussis            INT DEFAULT 0,
  deux_pts_reussis      INT DEFAULT 0,
  trois_pts_reussis     INT DEFAULT 0,
  points_total          INT GENERATED ALWAYS AS (
                          lf_reussis + (deux_pts_reussis * 2) + (trois_pts_reussis * 3)
                        ) STORED,
  -- Rebonds
  rebonds_offensifs     INT DEFAULT 0,
  rebonds_defensifs     INT DEFAULT 0,
  rebonds_total         INT GENERATED ALWAYS AS (rebonds_offensifs + rebonds_defensifs) STORED,
  -- Actions
  passes_decisives      INT DEFAULT 0,
  interceptions         INT DEFAULT 0,
  contres               INT DEFAULT 0,
  balles_perdues        INT DEFAULT 0,
  fautes_personnelles   INT DEFAULT 0,
  created_at            TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(match_id, joueur_id)
);

-- Index pour les requêtes fréquentes
CREATE INDEX IF NOT EXISTS idx_stats_match   ON stats_joueurs(match_id);
CREATE INDEX IF NOT EXISTS idx_stats_joueur  ON stats_joueurs(joueur_id);
CREATE INDEX IF NOT EXISTS idx_matchs_date   ON matchs(date);
CREATE INDEX IF NOT EXISTS idx_matchs_champ  ON matchs(championnat);

-- Vue pratique : stats complètes avec noms
CREATE OR REPLACE VIEW v_stats AS
SELECT
  m.date,
  m.championnat,
  m.poule,
  ed.nom   AS equipe_domicile,
  ev.nom   AS equipe_visiteur,
  m.score_domicile,
  m.score_visiteur,
  eq.nom   AS equipe_joueur,
  j.nom    AS joueur_nom,
  j.prenom AS joueur_prenom,
  s.numero_maillot,
  s.titulaire,
  s.points_total,
  s.lf_reussis,
  s.deux_pts_reussis,
  s.trois_pts_reussis,
  s.rebonds_total,
  s.rebonds_offensifs,
  s.rebonds_defensifs,
  s.passes_decisives,
  s.interceptions,
  s.contres,
  s.balles_perdues,
  s.fautes_personnelles
FROM stats_joueurs s
JOIN matchs  m  ON s.match_id  = m.id
JOIN joueurs j  ON s.joueur_id = j.id
JOIN equipes eq ON s.equipe_id = eq.id
JOIN equipes ed ON m.equipe_domicile_id = ed.id
JOIN equipes ev ON m.equipe_visiteur_id = ev.id;
