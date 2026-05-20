CREATE TABLE IF NOT EXISTS drafts (
    draft_id        TEXT PRIMARY KEY,
    option_snapshot TEXT NOT NULL,
    fingerprint     TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    consumed_at     TEXT,
    intent_snapshot TEXT            -- JSON; nullable for backwards compat with P1 fixtures
);

CREATE TABLE IF NOT EXISTS bookings (
    booking_id   TEXT PRIMARY KEY,
    draft_id     TEXT NOT NULL REFERENCES drafts(draft_id),
    provider     TEXT NOT NULL,
    status       TEXT NOT NULL,
    summary      TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    cancelled_at TEXT,
    cancellation TEXT
);
CREATE INDEX IF NOT EXISTS bookings_status_idx ON bookings(status);

CREATE TABLE IF NOT EXISTS booking_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id TEXT NOT NULL REFERENCES bookings(booking_id),
    ts         TEXT NOT NULL,
    kind       TEXT NOT NULL,
    payload    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS booking_events_booking_idx ON booking_events(booking_id, ts);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    key             TEXT PRIMARY KEY,
    scope           TEXT NOT NULL,
    booking_id      TEXT,
    response_blob   TEXT,           -- nullable; NULL means claimed but not yet finalised
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_confirmations (
    subject_id   TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL,
    request_json TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL
);
