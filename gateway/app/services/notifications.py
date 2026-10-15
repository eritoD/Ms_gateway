"""Outbox ingestion and Expo delivery, independent of mobile app execution."""
import logging
import threading

import httpx
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.repositories.notifications import NotificationRepository

log = logging.getLogger(__name__)
EXPO_URL = 'https://exp.host/--/api/v2/push/'


def retry_delay(attempts):
    return min(3600, 2 ** min(attempts, 11))


class NotificationWorker:
    def __init__(self, settings):
        self.settings = settings
        self.stop = threading.Event()
        self.pools = []

    def pool(self, url):
        pool = ConnectionPool(url, min_size=1, max_size=4, kwargs={'connect_timeout': 5}, open=False)
        self.pools.append(pool)
        pool.open(wait=True, timeout=15)
        return pool

    def start(self):
        try:
            self.repository = NotificationRepository(self.pool(self.settings.notifications_database_url))
            self.repository.migrate()
            self.sources = [(self.pool(url), schema) for url, schema in (
                (self.settings.matching_database_url, 'matching_api'),
                (self.settings.activities_database_url, 'activities_api'),
                (self.settings.clubs_database_url, 'clubs_api')) if url]
            self.thread = threading.Thread(target=self.run, name='notifications', daemon=True)
            self.thread.start()
        except Exception:
            for pool in self.pools:
                pool.close()
            raise

    def close(self):
        self.stop.set()
        self.thread.join()
        for pool in self.pools:
            pool.close()

    def run(self):
        headers = {}
        if self.settings.expo_access_token:
            headers['Authorization'] = 'Bearer ' + self.settings.expo_access_token.get_secret_value()
        with httpx.Client(base_url=EXPO_URL, headers=headers, timeout=10, trust_env=False) as client:
            while not self.stop.is_set():
                for pool, schema in self.sources:
                    try:
                        self.repository.ingest(pool, schema)
                    except Exception:
                        # Never log payloads, credentials or push tokens.
                        log.warning('Notification ingestion failed for %s; will retry', schema)
                if self.settings.push_enabled:
                    try:
                        self.deliver(client)
                    except Exception:
                        log.warning('Notification delivery cycle failed; will retry')
                self.stop.wait(2)

    def deliver(self, client):
        with self.repository.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            # One dispatcher across gateway replicas; rollback releases the lock.
            cur.execute("SELECT pg_try_advisory_xact_lock(hashtextextended('notification-dispatch', 0)) AS locked")
            if not cur.fetchone()['locked']:
                return
            cur.execute('''INSERT INTO notifications.deliveries(notification_id,device_id)
                SELECT n.id,d.id FROM notifications.inbox n JOIN notifications.devices d ON n.user_id=d.user_id
                WHERE d.enabled AND d.updated_at > CURRENT_TIMESTAMP - interval '90 days'
                AND n.created_at >= d.consent_at AND n.created_at > CURRENT_TIMESTAMP - interval '24 hours'
                AND n.read_at IS NULL ON CONFLICT DO NOTHING''')
            cur.execute('''UPDATE notifications.deliveries q SET status='expired'
                FROM notifications.inbox n WHERE q.notification_id=n.id AND q.status IN ('pending','receipt')
                AND n.created_at < CURRENT_TIMESTAMP - interval '24 hours' ''')
            cur.execute('''UPDATE notifications.deliveries q SET status='read'
                FROM notifications.inbox n WHERE q.notification_id=n.id AND q.status='pending' AND n.read_at IS NOT NULL''')
            cur.execute('''SELECT q.*,n.user_id,n.title,n.body,n.data,d.token FROM notifications.deliveries q
                JOIN notifications.inbox n ON n.id=q.notification_id
                JOIN notifications.devices d ON d.id=q.device_id AND d.user_id=n.user_id
                WHERE q.status IN ('pending','receipt') AND q.next_attempt<=CURRENT_TIMESTAMP
                AND d.enabled AND n.created_at >= d.consent_at
                AND d.updated_at > CURRENT_TIMESTAMP - interval '90 days'
                ORDER BY q.next_attempt LIMIT 100 FOR UPDATE OF q,d SKIP LOCKED''')
            rows = cur.fetchall()
            # At most two network requests per cycle, even during a provider outage.
            for status in ('pending', 'receipt'):
                group = [row for row in rows if row['status'] == status]
                if group and not self.stop.is_set():
                    self.deliver_batch(cur, client, group, status)

    def deliver_batch(self, cur, client, rows, status):
        try:
            if status == 'pending':
                payload = [{'to': row['token'], 'title': row['title'], 'body': row['body'],
                    'sound': 'default', 'channelId': 'sportmatch', 'priority': 'high', 'ttl': 3600,
                    'data': {**row['data'], 'notificationId': str(row['notification_id']), 'userId': str(row['user_id'])}}
                    for row in rows]
                response = client.post('send', json=payload)
                response.raise_for_status()
                results = response.json()['data']
                if not isinstance(results, list) or len(results) != len(rows):
                    raise ValueError('Invalid ticket batch')
            else:
                response = client.post('getReceipts', json={'ids': [row['ticket_id'] for row in rows]})
                response.raise_for_status()
                receipts = response.json()['data']
                results = [receipts.get(row['ticket_id']) for row in rows]
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            for row in rows:
                self.save_result(cur, row, None, 'provider_unavailable')
            return
        for row, result in zip(rows, results):
            self.save_result(cur, row, result)

    def save_result(self, cur, row, result, error=None):
        status, ticket = row['status'], row['ticket_id']
        attempts = row['attempts'] + 1
        delay = retry_delay(attempts)
        try:
            if status == 'pending' and result:
                if result['status'] == 'ok':
                    ticket, status, delay = result['id'], 'receipt', 900
                else:
                    error = result.get('details', {}).get('error', 'UnknownExpoError')
            elif status == 'receipt':
                delay = 900
                if result:
                    if result['status'] == 'ok':
                        status = 'sent'
                    else:
                        error = result.get('details', {}).get('error', 'UnknownExpoError')
                        status, ticket = 'pending', None
            if error == 'DeviceNotRegistered':
                cur.execute('UPDATE notifications.devices SET enabled=false WHERE id=%s', (row['device_id'],))
                status = 'failed'
            elif error == 'MessageTooBig':
                status = 'failed'
            elif error:
                # Credential/rate/provider failures remain retryable until expiry.
                log.warning('Expo delivery error: %s', error if error in ('InvalidCredentials', 'MessageRateExceeded', 'MismatchSenderId') else 'provider_error')
        except (ValueError, KeyError, TypeError, AttributeError):
            error = 'provider_unavailable'
        cur.execute('''UPDATE notifications.deliveries SET status=%s,ticket_id=%s,last_error=%s,attempts=%s,
            next_attempt=CURRENT_TIMESTAMP + %s * interval '1 second' WHERE notification_id=%s AND device_id=%s''',
            (status, ticket, error, attempts, delay, row['notification_id'], row['device_id']))
