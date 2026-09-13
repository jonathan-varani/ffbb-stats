-- Remet à zéro les données FFBB Stats (structure des tables conservée).
-- Ordre : enfants d'abord pour éviter les erreurs de clé étrangère
-- (TRUNCATE ... CASCADE gère déjà les FK, l'ordre explicite est juste plus clair).

TRUNCATE TABLE
  stats_joueurs,
  joueurs,
  matchs,
  equipes
RESTART IDENTITY CASCADE;
