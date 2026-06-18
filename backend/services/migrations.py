from __future__ import annotations
import os
import datetime
import logging

log = logging.getLogger("migrations")


def m0001_create_schema_migrations_table(sb) -> None:
    sb.rpc(
        "exec_sql",
        {"query": """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version     TEXT PRIMARY KEY,
            applied_at  TIMESTAMPTZ DEFAULT NOW(),
            description TEXT
        );
    """},
    ).execute()


def m0002_add_anonymised_flag_to_restaurants(sb) -> None:
    sb.rpc(
        "exec_sql",
        {"query": """
        ALTER TABLE restaurants
            ADD COLUMN IF NOT EXISTS _anonymised BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS _anonymised_at TIMESTAMPTZ;
    """},
    ).execute()


def m0003_add_email_to_restaurants(sb) -> None:
    sb.rpc(
        "exec_sql",
        {"query": """
        ALTER TABLE restaurants
            ADD COLUMN IF NOT EXISTS email TEXT;
        CREATE INDEX IF NOT EXISTS idx_restaurants_email ON restaurants(email);
    """},
    ).execute()


def m0004_create_audit_log_table(sb) -> None:
    sb.rpc(
        "exec_sql",
        {"query": """
        CREATE TABLE IF NOT EXISTS audit_log (
            id           BIGSERIAL PRIMARY KEY,
            ts           TIMESTAMPTZ DEFAULT NOW(),
            actor_email  TEXT,
            restaurant_id TEXT,
            action       TEXT NOT NULL,
            endpoint     TEXT,
            payload_hash TEXT,
            ip_address   TEXT,
            success      BOOLEAN DEFAULT TRUE,
            detail       TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_audit_log_restaurant ON audit_log(restaurant_id);
        CREATE INDEX IF NOT EXISTS idx_audit_log_ts ON audit_log(ts DESC);
    """},
    ).execute()


def m0005_add_chain_name_to_chains(sb) -> None:
    sb.rpc(
        "exec_sql",
        {"query": """
        ALTER TABLE chains
            ADD COLUMN IF NOT EXISTS chain_name TEXT;
    """},
    ).execute()


_MIGRATIONS = [
    ("0001", "Create schema_migrations table", m0001_create_schema_migrations_table),
    ("0002", "Add anonymised flag to restaurants", m0002_add_anonymised_flag_to_restaurants),
    ("0003", "Add email index to restaurants", m0003_add_email_to_restaurants),
    ("0004", "Create audit_log table", m0004_create_audit_log_table),
    ("0005", "Add chain_name column to chains", m0005_add_chain_name_to_chains),
]


def run_pending_migrations() -> None:
    try:
        from services.supabase_db import _sb

        if not _sb:
            log.info("Migrations skipped — Supabase not connected")
            return

        try:
            m0001_create_schema_migrations_table(_sb)
        except Exception:
            pass

        try:
            resp = _sb.table("schema_migrations").select("version").execute()
            applied = {row["version"] for row in (resp.data or [])}
        except Exception:
            applied = set()

        for version, description, fn in _MIGRATIONS:
            if version in applied:
                continue
            try:
                log.info("Applying migration %s: %s", version, description)
                fn(_sb)
                _sb.table("schema_migrations").insert(
                    {
                        "version": version,
                        "description": description,
                        "applied_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    }
                ).execute()
                log.info("✅ Migration %s applied", version)
            except Exception as e:
                log.error("❌ Migration %s failed: %s", version, e)

    except Exception as e:
        log.error("Migration runner error: %s", e)
