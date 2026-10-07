-- Telegram Escrow Marketplace Bot — schema (SQLite)
-- Money is ALWAYS stored as integer USD cents. Timestamps are UTC ISO strings.

CREATE TABLE IF NOT EXISTS users (
    tg_id        INTEGER PRIMARY KEY,
    username     TEXT,
    first_name   TEXT,
    is_banned    INTEGER NOT NULL DEFAULT 0,
    warn_count   INTEGER NOT NULL DEFAULT 0,
    deals_count  INTEGER NOT NULL DEFAULT 0,
    rating_sum   INTEGER NOT NULL DEFAULT 0,
    rating_count INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS listings (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    seller_id          INTEGER NOT NULL REFERENCES users(tg_id),
    category           TEXT NOT NULL,
    subcategory        TEXT NOT NULL,
    title              TEXT NOT NULL DEFAULT '',
    price_cents        INTEGER NOT NULL DEFAULT 0,
    followers          INTEGER NOT NULL DEFAULT 0,
    country            TEXT NOT NULL DEFAULT '',
    account_age        TEXT NOT NULL DEFAULT '',
    description        TEXT NOT NULL DEFAULT '',
    -- structured verification (seller-declared, never auto-verified)
    ver_email_access   TEXT NOT NULL DEFAULT '',   -- 'yes' | 'no'
    ver_violations     TEXT NOT NULL DEFAULT '',   -- 'yes' | 'no'
    ver_violations_dtl TEXT NOT NULL DEFAULT '',
    ver_monetized      TEXT NOT NULL DEFAULT '',   -- 'yes' | 'no'
    ver_proof_video    TEXT NOT NULL DEFAULT '',   -- telegram file_id
    ver_proof_shots    TEXT NOT NULL DEFAULT '[]', -- JSON list of file_ids
    ver_checklist      TEXT NOT NULL DEFAULT '{}', -- JSON: item -> state
    engagement_flag    INTEGER NOT NULL DEFAULT 0, -- heuristic mismatch flag
    status             TEXT NOT NULL DEFAULT 'DRAFT',
    -- DRAFT -> PENDING -> LIVE -> (SOLD | REJECTED | BANNED)
    reject_reason      TEXT NOT NULL DEFAULT '',
    created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    approved_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_listings_status ON listings(status);
CREATE INDEX IF NOT EXISTS idx_listings_cat ON listings(category, status);

CREATE TABLE IF NOT EXISTS credentials (
    listing_id INTEGER PRIMARY KEY REFERENCES listings(id) ON DELETE CASCADE,
    vault_blob TEXT NOT NULL,  -- Fernet-encrypted
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS payouts (
    user_id   INTEGER PRIMARY KEY REFERENCES users(tg_id),
    method    TEXT NOT NULL DEFAULT '',  -- 'binance' | 'easypaisa' | 'jazzcash' | 'bank'
    details   TEXT NOT NULL DEFAULT '',  -- JSON: uid/email/number/iban...
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS deals (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id       INTEGER NOT NULL REFERENCES listings(id),
    buyer_id         INTEGER NOT NULL REFERENCES users(tg_id),
    seller_id        INTEGER NOT NULL REFERENCES users(tg_id),
    price_cents      INTEGER NOT NULL,
    commission_cents INTEGER NOT NULL,
    -- NEGOTIATING -> AWAITING_PAYMENT -> PAID_HELD -> (RELEASED | REFUNDED | DISPUTED)
    -- NEGOTIATING -> CANCELLED ; AWAITING_PAYMENT -> CANCELLED ; DISPUTED -> RELEASED|REFUNDED
    status           TEXT NOT NULL DEFAULT 'NEGOTIATING',
    payment_method   TEXT NOT NULL DEFAULT '',   -- 'binance' | 'bybit' | 'manual'
    payment_ref      TEXT NOT NULL DEFAULT '',   -- Pay ID / screenshot file_id
    -- differentiated commission (per payment method)
    payable_pkr      INTEGER NOT NULL DEFAULT 0, -- PKR the buyer must send (local methods)
    commission_pkr   INTEGER NOT NULL DEFAULT 0, -- local commission snapshot (PKR)
    -- auto payment verification (Binance/Bybit deposit polling)
    expected_usdt    REAL NOT NULL DEFAULT 0,    -- unique USDT amount incl. random cents
    pay_poll_started_at TEXT,                    -- when auto-polling started (UTC ISO)
    auto_verified    INTEGER NOT NULL DEFAULT 0, -- 1 = payment auto-confirmed by bot
    paid_at          TEXT,
    delivered_at     TEXT,
    delivery_due_at  TEXT,                        -- 12h deadline (manual-delivery listings)
    handover_until   TEXT,                        -- 8h window after delivery
    hold_until       TEXT,                        -- 24h hold after delivery
    confirm_receipt  INTEGER NOT NULL DEFAULT 0,
    disputed_at      TEXT,
    dispute_reason   TEXT NOT NULL DEFAULT '',
    released_at      TEXT,
    refunded_at      TEXT,
    refund_reason    TEXT NOT NULL DEFAULT '',
    refund_actor     TEXT NOT NULL DEFAULT '',   -- 'bot' | 'admin'
    chat_open        INTEGER NOT NULL DEFAULT 1, -- 0 = locked/read-only
    offense_count    INTEGER NOT NULL DEFAULT 0, -- masking offenses in this deal
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_deals_buyer ON deals(buyer_id, status);
CREATE INDEX IF NOT EXISTS idx_deals_seller ON deals(seller_id, status);
CREATE INDEX IF NOT EXISTS idx_deals_listing ON deals(listing_id);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id     INTEGER NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    sender_role TEXT NOT NULL,   -- 'buyer' | 'seller' | 'system'
    text        TEXT NOT NULL,   -- original text
    masked_text TEXT NOT NULL,   -- what the other side actually saw
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_messages_deal ON messages(deal_id);

CREATE TABLE IF NOT EXISTS ledger (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id   INTEGER REFERENCES deals(id),
    kind      TEXT NOT NULL,  -- 'hold' | 'release' | 'refund' | 'commission'
    amount_cents INTEGER NOT NULL,
    actor     TEXT NOT NULL,  -- 'bot' | 'admin'
    note      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_ledger_deal ON ledger(deal_id);

CREATE TABLE IF NOT EXISTS ratings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id    INTEGER NOT NULL REFERENCES deals(id),
    rater_id   INTEGER NOT NULL REFERENCES users(tg_id),
    ratee_id   INTEGER NOT NULL REFERENCES users(tg_id),
    stars      INTEGER NOT NULL,
    comment    TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(deal_id, rater_id)
);

CREATE TABLE IF NOT EXISTS audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    actor      TEXT NOT NULL,   -- 'bot' | 'admin:<tg_id>' | 'user:<tg_id>'
    action     TEXT NOT NULL,
    entity     TEXT NOT NULL,
    entity_id  INTEGER,
    detail     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit(entity, entity_id);

CREATE TABLE IF NOT EXISTS jobs (
    deal_id   INTEGER NOT NULL,
    kind      TEXT NOT NULL,   -- 'delivery_deadline' | 'handover_lock' | 'hold_release'
    run_at    TEXT NOT NULL,
    aps_job_id TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (deal_id, kind)
);
