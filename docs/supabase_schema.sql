-- =============================================================================
-- WasteWise AI — Supabase PostgreSQL Schema
-- =============================================================================
-- Safe to run multiple times. Every statement uses IF NOT EXISTS.
-- Run this in your Supabase SQL Editor (Database → SQL Editor → New query).
-- =============================================================================


-- ---------------------------------------------------------------------------
-- 1. REGIONS
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS regions (
    name                    TEXT PRIMARY KEY,
    type                    TEXT NOT NULL DEFAULT 'General Area',
    foot_traffic_baseline   INTEGER NOT NULL DEFAULT 500,
    weekend_multiplier      NUMERIC(4,2) NOT NULL DEFAULT 1.10,
    holiday_multiplier      NUMERIC(4,2) NOT NULL DEFAULT 1.00,
    rain_impact             NUMERIC(4,2) NOT NULL DEFAULT -0.20,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ---------------------------------------------------------------------------
-- 2. RESTAURANTS
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS restaurants (
    id                              TEXT PRIMARY KEY,
    name                            TEXT NOT NULL,
    region                          TEXT REFERENCES regions(name) ON DELETE SET NULL,
    type                            TEXT NOT NULL DEFAULT 'street_stall',
    owner_name                      TEXT NOT NULL DEFAULT 'Owner',
    email                           TEXT,
    telegram_chat_id                BIGINT,
    telegram_username               TEXT,
    chain_id                        TEXT,
    privacy_accepted                BOOLEAN NOT NULL DEFAULT TRUE,
    registered_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    specialty_weather               TEXT NOT NULL DEFAULT 'neutral',
    closing_time                    TEXT NOT NULL DEFAULT '21:00',
    discount_pct                    INTEGER NOT NULL DEFAULT 30,
    marketplace_enabled             BOOLEAN NOT NULL DEFAULT TRUE,
    preferred_language              TEXT NOT NULL DEFAULT 'english',
    state                           TEXT,
    currency                        TEXT NOT NULL DEFAULT 'INR',
    latitude                        NUMERIC(10,7),
    longitude                       NUMERIC(10,7),
    bom                             JSONB NOT NULL DEFAULT '{}',
    recent_feedback_memory          JSONB NOT NULL DEFAULT '[]',
    q_tables                        JSONB NOT NULL DEFAULT '{}',
    bayesian_beliefs                JSONB NOT NULL DEFAULT '{}',
    sustainability_waste_prevented_kg NUMERIC(12,3) NOT NULL DEFAULT 0,
    sustainability_co2_saved_kg     NUMERIC(12,3) NOT NULL DEFAULT 0,
    gamification                    JSONB NOT NULL DEFAULT '{"current_streak":0,"longest_streak":0,"last_log_date":null,"total_logs":0,"accuracy_milestones":[]}',
    _anonymised                     BOOLEAN NOT NULL DEFAULT FALSE,
    _anonymised_at                  TIMESTAMPTZ,
    updated_at                      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_restaurants_region ON restaurants(region);
CREATE INDEX IF NOT EXISTS idx_restaurants_email ON restaurants(email);
CREATE INDEX IF NOT EXISTS idx_restaurants_telegram_username ON restaurants(telegram_username);
CREATE INDEX IF NOT EXISTS idx_restaurants_chain_id ON restaurants(chain_id);


-- ---------------------------------------------------------------------------
-- 3. RESTAURANT MENU
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS restaurant_menu (
    restaurant_id       TEXT NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
    item                TEXT NOT NULL,
    base_daily_demand   INTEGER NOT NULL DEFAULT 50,
    profit_margin_rm    NUMERIC(10,2) NOT NULL DEFAULT 2.50,
    price_rm            NUMERIC(10,2) NOT NULL DEFAULT 5.00,
    halal_certified     BOOLEAN NOT NULL DEFAULT TRUE,
    allergens           JSONB NOT NULL DEFAULT '[]',
    description         TEXT NOT NULL DEFAULT '',
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (restaurant_id, item)
);

CREATE INDEX IF NOT EXISTS idx_restaurant_menu_restaurant ON restaurant_menu(restaurant_id);


-- ---------------------------------------------------------------------------
-- 4. DAILY RECORDS
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS daily_records (
    restaurant_id           TEXT NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
    date                    DATE NOT NULL,
    total_revenue_rm        NUMERIC(14,2) NOT NULL DEFAULT 0,
    total_waste_qty         INTEGER NOT NULL DEFAULT 0,
    weather                 TEXT,
    foot_traffic            TEXT,
    forecast_text           TEXT,
    forecast_generated_at   TIMESTAMPTZ,
    PRIMARY KEY (restaurant_id, date)
);

CREATE INDEX IF NOT EXISTS idx_daily_records_restaurant ON daily_records(restaurant_id);
CREATE INDEX IF NOT EXISTS idx_daily_records_date ON daily_records(date DESC);


-- ---------------------------------------------------------------------------
-- 5. DAILY ITEMS SOLD
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS daily_items_sold (
    restaurant_id   TEXT NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
    date            DATE NOT NULL,
    item            TEXT NOT NULL,
    qty_sold        INTEGER NOT NULL DEFAULT 0,
    revenue_rm      NUMERIC(12,2) NOT NULL DEFAULT 0,
    PRIMARY KEY (restaurant_id, date, item)
);

CREATE INDEX IF NOT EXISTS idx_daily_items_sold_restaurant ON daily_items_sold(restaurant_id);


-- ---------------------------------------------------------------------------
-- 6. ACTIVE EVENTS
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS active_events (
    id              BIGSERIAL PRIMARY KEY,
    restaurant_id   TEXT NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
    description     TEXT NOT NULL,
    headcount       INTEGER NOT NULL DEFAULT 0,
    days            INTEGER NOT NULL DEFAULT 1,
    event_date      DATE NOT NULL,
    expires_at      DATE NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_active_events_restaurant ON active_events(restaurant_id);
CREATE INDEX IF NOT EXISTS idx_active_events_expires ON active_events(expires_at);


-- ---------------------------------------------------------------------------
-- 7. CLOSING STOCK
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS closing_stock (
    id                      BIGSERIAL PRIMARY KEY,
    restaurant_id           TEXT NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
    stock_date              DATE NOT NULL,
    stock_time              TEXT,
    item                    TEXT NOT NULL,
    qty_available           INTEGER NOT NULL DEFAULT 0,
    original_price_rm       NUMERIC(10,2) NOT NULL DEFAULT 0,
    discounted_price_rm     NUMERIC(10,2) NOT NULL DEFAULT 0,
    discount_pct            INTEGER NOT NULL DEFAULT 30
);

CREATE INDEX IF NOT EXISTS idx_closing_stock_restaurant ON closing_stock(restaurant_id);
CREATE INDEX IF NOT EXISTS idx_closing_stock_date ON closing_stock(stock_date DESC);


-- ---------------------------------------------------------------------------
-- 8. MARKETPLACE ORDERS
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS marketplace_orders (
    order_id                TEXT PRIMARY KEY,
    restaurant_id           TEXT NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
    order_date              DATE NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    customer_name           TEXT NOT NULL,
    phone                   TEXT NOT NULL,
    items                   JSONB NOT NULL DEFAULT '[]',
    total_rm                NUMERIC(12,2) NOT NULL DEFAULT 0,
    shopkeeper_earnings_rm  NUMERIC(12,2) NOT NULL DEFAULT 0,
    platform_fee_rm         NUMERIC(12,2) NOT NULL DEFAULT 0,
    pickup_deadline         TIMESTAMPTZ,
    pickup_notes            TEXT NOT NULL DEFAULT '',
    reminder_sent           BOOLEAN NOT NULL DEFAULT FALSE,
    status                  TEXT NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending','accepted','completed','cancelled','missed'))
);

CREATE INDEX IF NOT EXISTS idx_marketplace_orders_restaurant ON marketplace_orders(restaurant_id);
CREATE INDEX IF NOT EXISTS idx_marketplace_orders_date ON marketplace_orders(order_date DESC);
CREATE INDEX IF NOT EXISTS idx_marketplace_orders_status ON marketplace_orders(status);


-- ---------------------------------------------------------------------------
-- 9. ACCOUNTS
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS accounts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           TEXT UNIQUE NOT NULL,
    restaurant_id   TEXT REFERENCES restaurants(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_accounts_email ON accounts(email);
CREATE INDEX IF NOT EXISTS idx_accounts_restaurant_id ON accounts(restaurant_id);


-- ---------------------------------------------------------------------------
-- 10. SESSIONS
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sessions (
    session_id          TEXT PRIMARY KEY,
    account_id          UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    type                TEXT NOT NULL DEFAULT 'web'
                        CHECK (type IN ('web','telegram')),
    chat_id             BIGINT,
    telegram_username   TEXT,
    label               TEXT NOT NULL DEFAULT '',
    is_primary          BOOLEAN NOT NULL DEFAULT FALSE,
    linked_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_active         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at          TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_sessions_account ON sessions(account_id);
CREATE INDEX IF NOT EXISTS idx_sessions_chat_id ON sessions(chat_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);


-- ---------------------------------------------------------------------------
-- 11. PENDING OTPs
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pending_otps (
    id          BIGSERIAL PRIMARY KEY,
    email       TEXT NOT NULL,
    purpose     TEXT NOT NULL,
    code_hash   TEXT NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pending_otps_email ON pending_otps(email);
CREATE INDEX IF NOT EXISTS idx_pending_otps_expires ON pending_otps(expires_at);


-- ---------------------------------------------------------------------------
-- 12. PENDING REGISTRATIONS
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pending_registrations (
    email               TEXT PRIMARY KEY,
    telegram_username   TEXT NOT NULL DEFAULT '',
    restaurant_data     JSONB NOT NULL DEFAULT '{}',
    code_hash           TEXT NOT NULL DEFAULT '',
    expires_at          TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pending_reg_expires ON pending_registrations(expires_at);


-- ---------------------------------------------------------------------------
-- 13. PENDING APPROVALS
-- (requires Primary Telegram confirmation for destructive actions)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pending_approvals (
    approval_id     TEXT PRIMARY KEY,
    restaurant_id   TEXT NOT NULL,
    action          TEXT NOT NULL,
    payload         JSONB NOT NULL DEFAULT '{}',
    requested_by    TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pending_approvals_restaurant ON pending_approvals(restaurant_id);
CREATE INDEX IF NOT EXISTS idx_pending_approvals_expires ON pending_approvals(expires_at);


-- ---------------------------------------------------------------------------
-- 14. CHAINS
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chains (
    id              TEXT PRIMARY KEY,
    chain_name      TEXT,
    owner_email     TEXT,
    branches        JSONB NOT NULL DEFAULT '[]',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ---------------------------------------------------------------------------
-- 15. AUDIT LOG
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
    id              BIGSERIAL PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actor_email     TEXT,
    restaurant_id   TEXT,
    action          TEXT NOT NULL,
    endpoint        TEXT,
    payload_hash    TEXT,
    ip_address      TEXT,
    success         BOOLEAN NOT NULL DEFAULT TRUE,
    detail          TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_log_restaurant ON audit_log(restaurant_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_ts ON audit_log(ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_actor ON audit_log(actor_email);


-- ---------------------------------------------------------------------------
-- 16. SCHEMA MIGRATIONS TRACKER
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    description TEXT
);


-- ---------------------------------------------------------------------------
-- 17. exec_sql RPC helper (used by the migration runner)
-- Required by services/migrations.py to run ALTER TABLE statements
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION exec_sql(query TEXT)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    EXECUTE query;
END;
$$;


-- ---------------------------------------------------------------------------
-- Row Level Security (RLS) — enable per table, add policies as needed
-- These stubs enable RLS without locking anyone out (service_role bypasses all)
-- ---------------------------------------------------------------------------
ALTER TABLE restaurants ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketplace_orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;

-- The backend uses service_role key which bypasses RLS.
-- Add user-facing RLS policies here if you ever add Supabase Auth JWT flows.


-- ---------------------------------------------------------------------------
-- Seed initial schema migration record
-- ---------------------------------------------------------------------------
INSERT INTO schema_migrations (version, description)
VALUES ('0000', 'Initial schema created from supabase_schema.sql')
ON CONFLICT (version) DO NOTHING;
