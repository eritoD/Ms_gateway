#!/bin/sh
# Prepara la base de ms-club en el PostgreSQL local (compose.windows.yaml) y termina.
# Es idempotente: crea el rol y la base si faltan y vuelve a aplicar los scripts de
# ms-club/db, que ya se pueden ejecutar más de una vez.
set -eu

: "${CLUBS_DB_PASSWORD:?Configura CLUBS_DB_PASSWORD en .env}"
export PGHOST=postgres PGUSER=postgres PGPASSWORD="${POSTGRES_PASSWORD}"
DB=sportmatch_clubs
ROLE=sportmatch_clubs

# La contraseña va como variable de psql (:'pass'), nunca concatenada en el SQL.
psql -v ON_ERROR_STOP=1 -d postgres -v pass="$CLUBS_DB_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE sportmatch_clubs LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD %L', :'pass')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sportmatch_clubs') \gexec
SELECT format('ALTER ROLE sportmatch_clubs PASSWORD %L', :'pass') \gexec
SELECT 'CREATE DATABASE sportmatch_clubs'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'sportmatch_clubs') \gexec
SQL

run() { echo "-> $1"; psql -v ON_ERROR_STOP=1 -q -d "$DB" -f "$1"; }

run /clubs-db/00_base_schema.sql
# Lo mismo que db/00_create_database.sql, sin crear el rol (ya existe).
psql -v ON_ERROR_STOP=1 -q -d "$DB" <<SQL
GRANT CONNECT ON DATABASE $DB TO $ROLE;
GRANT USAGE ON SCHEMA public TO $ROLE;
GRANT SELECT ON club, deporte, club_deporte TO $ROLE;
SQL
for f in /clubs-db/migrations/*.sql; do run "$f"; done
run /clubs-db/seeds/demo_planes.sql
echo "Base $DB lista."
