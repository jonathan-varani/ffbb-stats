-- ============================================
-- FFBB Stats — Migration format `resume_` (e-Marque Fédérale)
-- Le nouveau PDF fournit 2 données que l'ancien n'avait pas :
-- le temps de jeu et le statut de titulaire.
-- ============================================

-- 1. Numéro de rencontre : clé naturelle fiable pour l'idempotence
--    (le nouveau nom de ZIP ne contient plus la date)
ALTER TABLE matchs
  ADD COLUMN IF NOT EXISTS num_rencontre TEXT;

-- Pas de clause WHERE : un index unique standard ignore déjà les NULL
-- (plusieurs matchs sans num_rencontre peuvent coexister), et ça permet
-- à l'upsert PostgREST de cibler cet index via on_conflict=num_rencontre.
CREATE UNIQUE INDEX IF NOT EXISTS idx_matchs_num_rencontre
  ON matchs(num_rencontre);

-- 2. Temps de jeu par joueur (ex. "25:01")
ALTER TABLE stats_joueurs
  ADD COLUMN IF NOT EXISTS tps_jeu TEXT;

-- 3. Suivi de la qualité d'extraction
--    false = checksums en échec, données à revoir dans Studio
ALTER TABLE matchs
  ADD COLUMN IF NOT EXISTS extraction_validee BOOLEAN DEFAULT TRUE;

-- 4. Vue : matchs dont l'extraction est douteuse
CREATE OR REPLACE VIEW v_a_verifier AS
SELECT m.id, m.num_rencontre, m.date, m.championnat,
       ed.nom AS domicile, ev.nom AS visiteur,
       m.score_domicile, m.score_visiteur
FROM matchs m
JOIN equipes ed ON m.equipe_domicile_id = ed.id
JOIN equipes ev ON m.equipe_visiteur_id = ev.id
WHERE m.extraction_validee IS FALSE
ORDER BY m.date DESC;
