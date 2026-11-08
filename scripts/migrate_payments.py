"""One-time, verified financial-data cutover. Run with both services stopped.

Connection strings come from PAYMENT_MIGRATION_SOURCE_URL and
PAYMENT_MIGRATION_TARGET_URL; they are never printed. A successful import is
recorded in the destination. Retiring the source is a separate, explicit step.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

TABLES = ("plan_suscripcion", "transaccion_pago", "suscripcion")
IMPORT_ID = "extract-financial-domain-v1"


def _json(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    raise TypeError(type(value).__name__)


def fingerprint(rows: list[dict]) -> str:
    content = json.dumps(rows, default=_json, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _rows(conn, table: str, columns=None) -> list[dict]:
    selected = sql.SQL("*") if columns is None else sql.SQL(",").join(map(sql.Identifier, columns))
    return conn.execute(sql.SQL("SELECT {} FROM public.{} ORDER BY id").format(
        selected, sql.Identifier(table))).fetchall()


def _existing(conn) -> set[str]:
    return {row["tablename"] for row in conn.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename = ANY(%s)", (list(TABLES),))}


def migrate(source_url: str, target_url: str, *, retire_source: bool = False) -> dict:
    if not source_url or not target_url:
        raise ValueError("Both migration connection strings are required")
    with psycopg.connect(source_url, row_factory=dict_row) as source, psycopg.connect(
        target_url, row_factory=dict_row
    ) as target:
        identity = "SELECT current_database() AS db, inet_server_addr()::text AS host, inet_server_port() AS port"
        if source.execute(identity).fetchone() == target.execute(identity).fetchone():
            raise ValueError("Source and target must be different databases")
        # Finish the implicit transactions before taking the explicit cutover locks.
        source.commit()
        target.commit()
        if _existing(target) != set(TABLES):
            raise ValueError("Apply ms-pago schema migrations in the destination first")
        target.commit()
        with source.transaction():
            source.execute("SET LOCAL lock_timeout = '15s'")
            source.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (IMPORT_ID,))
            existing = _existing(source)
            with target.transaction():
                target.execute("""
                    CREATE TABLE IF NOT EXISTS public.payment_import_receipt (
                        id text PRIMARY KEY,
                        source_database text NOT NULL,
                        fingerprints jsonb NOT NULL,
                        row_counts jsonb NOT NULL,
                        verified_at timestamptz NOT NULL DEFAULT now(),
                        retired_at timestamptz
                    )
                """)
                receipt = target.execute("SELECT * FROM payment_import_receipt WHERE id=%s FOR UPDATE",
                                         (IMPORT_ID,)).fetchone()
                if not existing:
                    if receipt is None:
                        raise ValueError("Source tables missing without a verified import receipt")
                    return {"status": "already-retired", "rows": receipt["row_counts"]}
                if existing != set(TABLES):
                    raise ValueError("The source has only part of the financial schema; refusing migration")
                source.execute(sql.SQL("LOCK TABLE {} IN ACCESS EXCLUSIVE MODE").format(
                    sql.SQL(",").join(sql.Identifier("public", table) for table in TABLES)))
                payload = {table: _rows(source, table) for table in TABLES}
                digests = {table: fingerprint(rows) for table, rows in payload.items()}
                counts = {table: len(rows) for table, rows in payload.items()}
                source_db = source.execute("SELECT current_database() AS db").fetchone()["db"]
                if receipt is not None:
                    if receipt["source_database"] != source_db or receipt["fingerprints"] != digests:
                        raise ValueError("Source changed since the verified import; refusing retirement or overwrite")
                    # Destination may have received new payments since import. Never overwrite it.
                    for table, rows in payload.items():
                        copied_ids = {row["id"] for row in target.execute(
                            sql.SQL("SELECT id FROM public.{}").format(sql.Identifier(table)))}
                        if any(row["id"] not in copied_ids for row in rows):
                            raise ValueError("Imported rows are missing from the destination")
                else:
                    target.execute(sql.SQL("LOCK TABLE {} IN ACCESS EXCLUSIVE MODE").format(
                        sql.SQL(",").join(sql.Identifier("public", table) for table in TABLES)))
                    if any(_rows(target, table) for table in TABLES):
                        raise ValueError("Destination is not empty and has no verified import; refusing overwrite")
                    for table, rows in payload.items():
                        if not rows:
                            continue
                        columns = list(rows[0])
                        statement = sql.SQL("INSERT INTO public.{} ({}) OVERRIDING SYSTEM VALUE VALUES ({})").format(
                            sql.Identifier(table), sql.SQL(",").join(map(sql.Identifier, columns)),
                            sql.SQL(",").join(sql.Placeholder() for _ in columns))
                        # JSONB values need adaptation; the remaining values keep their PostgreSQL types.
                        with target.cursor() as cursor:
                            cursor.executemany(statement, [tuple(
                                Jsonb(row[column]) if isinstance(row[column], (dict, list)) else row[column]
                                for column in columns) for row in rows])
                        if fingerprint(_rows(target, table, columns)) != digests[table]:
                            raise ValueError(f"Row verification failed for {table}; import rolled back")
                    sequence = target.execute(
                        "SELECT pg_get_serial_sequence('public.plan_suscripcion','id') AS name").fetchone()["name"]
                    if sequence:
                        target.execute("SELECT setval(%s::regclass, COALESCE((SELECT max(id) FROM plan_suscripcion),1), "
                                       "EXISTS(SELECT 1 FROM plan_suscripcion))", (sequence,))
                    target.execute("INSERT INTO payment_import_receipt(id,source_database,fingerprints,row_counts) "
                                   "VALUES (%s,%s,%s,%s)", (IMPORT_ID, source_db, Jsonb(digests), Jsonb(counts)))
            # Destination commits before any source table is removed. A crash is safely resumable.
            if retire_source:
                # RESTRICT deliberately refuses unexpected dependencies. No CASCADE or unrelated tables.
                source.execute(sql.SQL("DROP TABLE {} RESTRICT").format(
                    sql.SQL(",").join(sql.Identifier("public", table) for table in reversed(TABLES))))
        if retire_source:
            with target.transaction():
                target.execute("UPDATE payment_import_receipt SET retired_at=now() WHERE id=%s", (IMPORT_ID,))
        return {"status": "retired" if retire_source else "verified", "rows": counts}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retire-source", action="store_true", help="Remove the verified legacy financial tables")
    args = parser.parse_args()
    result = migrate(os.environ.get("PAYMENT_MIGRATION_SOURCE_URL", ""),
                     os.environ.get("PAYMENT_MIGRATION_TARGET_URL", ""), retire_source=args.retire_source)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
