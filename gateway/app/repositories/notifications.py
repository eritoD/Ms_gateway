"""Durable inbox and device subscriptions. Ownership always comes from the JWT."""
import hashlib
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


class NotificationRepository:
    def __init__(self, pool):
        self.pool = pool

    def migrate(self):
        with self.pool.connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('notifications-schema', 0))")
            conn.execute(Path(__file__).with_name('notifications.sql').read_text(encoding='utf-8'))

    def register(self, user_id, device):
        digest = hashlib.sha256(str(device.revoke_secret).encode()).hexdigest()
        with self.pool.connection() as conn:
            # Serialize token rotations and account changes, including first registration.
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('notifications-devices', 0))")
            existing = conn.execute('SELECT revoke_hash FROM notifications.devices WHERE id=%s FOR UPDATE', (device.device_id,)).fetchone()
            if existing and existing[0] != digest:
                raise HTTPException(403, 'Dispositivo no disponible.')
            conn.execute('DELETE FROM notifications.devices WHERE token=%s AND id<>%s', (device.token, device.device_id))
            # Rebinding/rotating invalidates queued deliveries for the previous registration.
            conn.execute('''DELETE FROM notifications.deliveries WHERE device_id=%s AND EXISTS
                (SELECT 1 FROM notifications.devices WHERE id=%s AND (user_id<>%s OR token<>%s OR NOT enabled))''',
                (device.device_id, device.device_id, user_id, device.token))
            conn.execute('''INSERT INTO notifications.devices(id,user_id,token,revoke_hash)
                VALUES (%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET
                consent_at=CASE WHEN devices.user_id<>EXCLUDED.user_id OR NOT devices.enabled
                    THEN CURRENT_TIMESTAMP ELSE devices.consent_at END,
                user_id=EXCLUDED.user_id, token=EXCLUDED.token, enabled=true, updated_at=CURRENT_TIMESTAMP''',
                (device.device_id, user_id, device.token, digest))

    def revoke(self, device_id, secret):
        # A random revocation capability allows logout even after JWT expiration.
        digest = hashlib.sha256(str(secret).encode()).hexdigest()
        with self.pool.connection() as conn:
            conn.execute('UPDATE notifications.devices SET enabled=false WHERE id=%s AND revoke_hash=%s', (device_id, digest))

    def inbox(self, user_id, before, limit):
        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute('''SELECT id,kind,title,body,data,created_at,read_at FROM notifications.inbox
                WHERE user_id=%s AND (%s::uuid IS NULL OR (created_at,id) <
                    (SELECT created_at,id FROM notifications.inbox WHERE id=%s AND user_id=%s))
                ORDER BY created_at DESC,id DESC LIMIT %s''', (user_id, before, before, user_id, limit+1))
            rows = cur.fetchall()
            cur.execute('SELECT count(*) AS total FROM notifications.inbox WHERE user_id=%s AND read_at IS NULL', (user_id,))
            return {'items': rows[:limit], 'unread': cur.fetchone()['total'],
                    'next_cursor': str(rows[limit-1]['id']) if len(rows)>limit else None}

    def mark_read(self, user_id, ids):
        with self.pool.connection() as conn:
            conn.execute('''UPDATE notifications.inbox SET read_at=CURRENT_TIMESTAMP
                WHERE user_id=%s AND read_at IS NULL AND id=ANY(%s::uuid[])''', (user_id, ids))

    def ingest(self, source_pool, schema):
        # Destination commits before acknowledging source. A crash between commits
        # replays safely through UNIQUE(source, source_id); no cursor can skip a transaction.
        table = sql.Identifier(schema, 'notification_outbox')
        with source_pool.connection() as source, source.cursor(row_factory=dict_row) as cur:
            cur.execute(sql.SQL('SELECT * FROM {} WHERE published_at IS NULL ORDER BY id LIMIT 100 FOR UPDATE SKIP LOCKED').format(table))
            rows = cur.fetchall()
            with self.pool.connection() as dest:
                muted_table = dest.execute("SELECT to_regclass('public.silencio_usuario')").fetchone()[0] is not None
                for row in rows:
                    if muted_table and self._muted_message(cur, dest, schema, row):
                        continue  # acknowledged below, never shown nor pushed
                    dest.execute('''INSERT INTO notifications.inbox(id,source,source_id,user_id,kind,title,body,data,created_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(source,source_id) DO NOTHING''',
                        (uuid4(), schema, row['id'], row['user_id'], row['kind'], row['title'], row['body'], Jsonb(row['data']), row['created_at']))
            if rows:
                cur.execute(sql.SQL('UPDATE {} SET published_at=CURRENT_TIMESTAMP WHERE id=ANY(%s)').format(table), ([r['id'] for r in rows],))
            cur.execute(sql.SQL("DELETE FROM {} WHERE published_at < CURRENT_TIMESTAMP - interval '7 days'").format(table))

    @staticmethod
    def _muted_message(source_cur, dest, schema, row):
        # HU-51: a chat message from someone the recipient muted raises no alert. The event
        # only carries the match, so the sender is the other person of that match.
        if schema != 'matching_api' or row['kind'] != 'mensaje':
            return False
        try:
            match_id = UUID(str((row['data'] or {}).get('matchId')))
        except ValueError:
            return False
        source_cur.execute('''SELECT CASE WHEN sender_id=%s THEN recipient_id ELSE sender_id END AS other
            FROM matching_api.requests WHERE id=%s AND %s IN (sender_id, recipient_id)''',
            (row['user_id'], match_id, row['user_id']))
        other = source_cur.fetchone()
        if other is None:
            return False
        return dest.execute('SELECT 1 FROM public.silencio_usuario WHERE silenciador_id=%s AND silenciado_id=%s',
            (row['user_id'], other['other'])).fetchone() is not None
