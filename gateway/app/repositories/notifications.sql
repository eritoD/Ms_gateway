CREATE SCHEMA IF NOT EXISTS notifications;
CREATE TABLE IF NOT EXISTS notifications.inbox (
    id uuid PRIMARY KEY,
    source text NOT NULL,
    source_id bigint NOT NULL,
    user_id uuid NOT NULL,
    kind text NOT NULL,
    title text NOT NULL,
    body text NOT NULL,
    data jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    read_at timestamptz,
    UNIQUE(source, source_id)
);
CREATE INDEX IF NOT EXISTS inbox_user ON notifications.inbox(user_id, created_at DESC, id DESC);
CREATE TABLE IF NOT EXISTS notifications.devices (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL,
    token text UNIQUE NOT NULL,
    revoke_hash text NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    consent_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS notifications.deliveries (
    notification_id uuid REFERENCES notifications.inbox(id) ON DELETE CASCADE,
    device_id uuid REFERENCES notifications.devices(id) ON DELETE CASCADE,
    status text NOT NULL DEFAULT 'pending',
    attempts integer NOT NULL DEFAULT 0,
    next_attempt timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ticket_id text,
    last_error text,
    PRIMARY KEY(notification_id, device_id)
);
CREATE INDEX IF NOT EXISTS deliveries_pending ON notifications.deliveries(next_attempt) WHERE status IN ('pending', 'receipt');
