"""The cutover is exercised only in fresh, disposable databases."""
import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

spec = importlib.util.spec_from_file_location("payment_cutover", Path(__file__).parents[1] / "scripts/migrate_payments.py")
cutover = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cutover)

SCHEMA = """
CREATE TABLE plan_suscripcion(id int GENERATED ALWAYS AS IDENTITY PRIMARY KEY, nombre text, beneficios jsonb);
CREATE TABLE transaccion_pago(id uuid PRIMARY KEY, plan_id int REFERENCES plan_suscripcion(id), token_ws text);
CREATE TABLE suscripcion(id uuid PRIMARY KEY, plan_id int REFERENCES plan_suscripcion(id),
                        ultima_transaccion_id uuid REFERENCES transaccion_pago(id));
CREATE TABLE club(id int PRIMARY KEY, nombre text);
INSERT INTO club VALUES (1,'Conservar');
"""


@pytest.fixture
def databases():
    admin_url = os.environ.get("PAYMENT_MIGRATION_TEST_ADMIN_URL")
    if not admin_url:
        pytest.skip("PAYMENT_MIGRATION_TEST_ADMIN_URL is required for isolated migration tests")
    prefix = "payment_cutover_test_" + uuid4().hex
    names = [prefix + "_src", prefix + "_dst"]
    settings = conninfo_to_dict(admin_url)
    urls = [make_conninfo(**{**settings, "dbname": name}) for name in names]
    with psycopg.connect(admin_url, autocommit=True) as admin:
        try:
            for name, url in zip(names, urls):
                admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
                with psycopg.connect(url) as conn:
                    conn.execute(SCHEMA)
            tid = uuid4()
            with psycopg.connect(urls[0]) as conn:
                conn.execute("INSERT INTO plan_suscripcion OVERRIDING SYSTEM VALUE VALUES (42,'Original','[\"beneficio\"]')")
                conn.execute("INSERT INTO transaccion_pago VALUES (%s,42,'token-historico')", (tid,))
                conn.execute("INSERT INTO suscripcion VALUES (%s,42,%s)", (uuid4(), tid))
            yield urls
        finally:
            for name in names:
                admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


def test_import_preserves_ids_json_and_sequence_and_is_idempotent(databases):
    source, target = databases
    first = cutover.migrate(source, target)
    assert first == {"status": "verified", "rows": {name: 1 for name in cutover.TABLES}}
    assert cutover.migrate(source, target) == first
    with psycopg.connect(target) as conn:
        assert conn.execute("SELECT id,nombre,beneficios FROM plan_suscripcion").fetchone() == (42, "Original", ["beneficio"])
        assert conn.execute("INSERT INTO plan_suscripcion(nombre) VALUES ('Nuevo') RETURNING id").fetchone()[0] == 43
        assert conn.execute("SELECT token_ws FROM transaccion_pago").fetchone()[0] == "token-historico"
    # A retry must never overwrite newer data in the destination.
    cutover.migrate(source, target)
    with psycopg.connect(target) as conn:
        assert conn.execute("SELECT count(*) FROM plan_suscripcion").fetchone()[0] == 2


def test_retirement_requires_verified_data_and_keeps_nonfinancial_tables(databases):
    source, target = databases
    cutover.migrate(source, target)
    assert cutover.migrate(source, target, retire_source=True)["status"] == "retired"
    assert cutover.migrate(source, target, retire_source=True)["status"] == "already-retired"
    with psycopg.connect(source) as conn:
        assert conn.execute("SELECT nombre FROM club").fetchone()[0] == "Conservar"
        assert conn.execute("SELECT to_regclass('plan_suscripcion')").fetchone()[0] is None
    with psycopg.connect(target) as conn:
        assert conn.execute("SELECT count(*) FROM transaccion_pago").fetchone()[0] == 1


def test_changed_source_cannot_be_retired(databases):
    source, target = databases
    cutover.migrate(source, target)
    with psycopg.connect(source) as conn:
        conn.execute("UPDATE plan_suscripcion SET nombre='Cambio posterior'")
    with pytest.raises(ValueError, match="Source changed"):
        cutover.migrate(source, target, retire_source=True)
    with psycopg.connect(source) as conn:
        assert conn.execute("SELECT count(*) FROM plan_suscripcion").fetchone()[0] == 1


def test_nonempty_target_is_never_overwritten(databases):
    source, target = databases
    with psycopg.connect(target) as conn:
        conn.execute("INSERT INTO plan_suscripcion(nombre) VALUES ('No sobrescribir')")
    with pytest.raises(ValueError, match="not empty"):
        cutover.migrate(source, target, retire_source=True)
    with psycopg.connect(target) as conn:
        assert conn.execute("SELECT nombre FROM plan_suscripcion").fetchone()[0] == "No sobrescribir"


def test_same_database_is_refused(databases):
    with pytest.raises(ValueError, match="different databases"):
        cutover.migrate(databases[0], databases[0], retire_source=True)


def test_partial_source_schema_is_refused(databases):
    with psycopg.connect(databases[0]) as conn:
        conn.execute("DROP TABLE suscripcion")
    with pytest.raises(ValueError, match="part of the financial schema"):
        cutover.migrate(*databases, retire_source=True)
