"""HU-55: ms-pago's expiry notices reach the inbox (/notifications) and push like the other services' events.

ms-pago keeps its own outbox (aviso_pendiente) in the payments database, which the gateway does not connect
to: it reads it over HTTP with the service-to-service secret (X-Internal-Service-Token). Each notice is
copied into notifications.inbox (UNIQUE(source, source_id), so a replay never duplicates it) and only then
confirmed to ms-pago: a crash in between just reads it again. The delivery cycle then pushes it like any
other notification.
"""
import logging
import time
from datetime import datetime
from uuid import UUID

import httpx

log = logging.getLogger(__name__)

SOURCE = 'ms_pago'
PENDING_PATH = '/internal/v1/avisos/pendientes'
CONFIRM_PATH = '/internal/v1/avisos/confirmar'
# Notices are written at most a few times a day; there is no need to ask every worker cycle (2 s).
POLL_SECONDS = 60.0
BATCH = 100


def _valid(item):
    try:
        UUID(str(item['user_id']))
        datetime.fromisoformat(item['created_at'])
        return (isinstance(item['id'], int) and not isinstance(item['id'], bool)
                and all(isinstance(item[key], str) and item[key] for key in ('kind', 'title', 'body', 'created_at'))
                and isinstance(item['data'], dict))
    except (KeyError, TypeError, ValueError):
        return False


class PaymentNotices:
    def __init__(self, client: httpx.Client, token: str, *, poll_seconds: float = POLL_SECONDS,
                 clock=time.monotonic):
        self.client = client
        self.headers = {'X-Internal-Service-Token': token}
        self.poll_seconds = poll_seconds
        self.clock = clock
        self.next_poll = 0.0

    def due(self):
        return self.clock() >= self.next_poll

    def ingest(self, repository):
        """One read-copy-confirm round; returns how many notices were confirmed."""
        self.next_poll = self.clock() + self.poll_seconds
        response = self.client.get(PENDING_PATH, params={'limit': BATCH}, headers=self.headers)
        response.raise_for_status()
        items = response.json()['items']
        if not isinstance(items, list):
            raise ValueError('Invalid notices page')
        valid = [item for item in items if isinstance(item, dict) and _valid(item)]
        if len(valid) != len(items):
            # Never log payloads; the malformed ones stay pending in ms-pago.
            log.warning('Ignoring %d malformed payment notices', len(items) - len(valid))
        if not valid:
            return 0
        repository.ingest_items(SOURCE, valid)
        confirm = self.client.post(CONFIRM_PATH, json={'ids': [item['id'] for item in valid]}, headers=self.headers)
        confirm.raise_for_status()
        if len(valid) == BATCH:
            self.next_poll = 0.0  # more may be waiting
        return len(valid)
