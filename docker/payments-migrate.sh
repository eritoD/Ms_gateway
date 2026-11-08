#!/bin/sh
# Esquema propio de ms-pago. No copia ni consulta datos de otros servicios.
set -eu
: "${PAYMENTS_DB_PASSWORD:?Configura PAYMENTS_DB_PASSWORD en .env}"
export PGHOST=postgres PGUSER=postgres PGPASSWORD="${POSTGRES_PASSWORD}"
psql -v ON_ERROR_STOP=1 -d postgres -v pass="$PAYMENTS_DB_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE sportmatch_payments LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD %L', :'pass')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sportmatch_payments') \gexec
SELECT format('ALTER ROLE sportmatch_payments PASSWORD %L', :'pass') \gexec
SELECT 'CREATE DATABASE sportmatch_payments OWNER sportmatch_payments'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'sportmatch_payments') \gexec
SQL

for file in /payments-db/migrations/*.sql; do
    echo "Aplicando $file"
    psql -v ON_ERROR_STOP=1 -q -d sportmatch_payments -f "$file"
done

psql -v ON_ERROR_STOP=1 -q -d sportmatch_payments <<'SQL'
-- El inicializador usa postgres; el servicio también debe poder repetir sus
-- migraciones con su rol propio mediante python -m app.database.migrate.
SELECT format('ALTER TABLE public.%I OWNER TO sportmatch_payments', tablename)
FROM pg_tables WHERE schemaname = 'public'
AND tablename IN ('plan_suscripcion', 'transaccion_pago', 'suscripcion', 'suscripcion_periodo') \gexec
GRANT CONNECT ON DATABASE sportmatch_payments TO sportmatch_payments;
GRANT USAGE ON SCHEMA public TO sportmatch_payments;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO sportmatch_payments;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO sportmatch_payments;
SQL
# El catálogo real se migra con scripts/migrate_payments.py antes del cambio de servicio.
# No se siembran planes automáticamente: se preservan sus IDs y precios existentes.
