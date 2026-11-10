"""HU-55: ms-pago's expiry notices are copied into the inbox and only then confirmed. ms-pago is always an
httpx MockTransport; the inbox is a fake, or a disposable database with NOTIFICATIONS_TEST_ADMIN_URL."""
import json
from uuid import uuid4

import httpx
import pytest

from app.core.config import Settings
from app.services import payment_notices
from app.services.notifications import NotificationWorker
from app.services.payment_notices import PaymentNotices
from tests.test_notifications import SECRET, headers, rows, system  # noqa: F401 - pytest fixture

TOKEN = 'internal-secret-for-tests-with-32-bytes-or-more'
USER = uuid4()


def notice(notice_id=1, **changes):
    return {'id': notice_id, 'user_id': str(USER), 'kind': 'suscripcion', 'title': 'Tu plan vence pronto',
            'body': 'Tu plan Pro vence en 3 días.', 'created_at': '2030-01-10T12:00:00+00:00',
            'data': {'screen': 'pago', 'planId': '1', 'ruta': '/pago?plan=1'}, **changes}


class FakeMsPago:
    def __init__(self, pending):
        self.pending = list(pending)
        self.calls = []

    def __call__(self, request):
        self.calls.append((request.method, request.url.path, request.headers.get('X-Internal-Service-Token')))
        if request.url.path == payment_notices.PENDING_PATH:
            return httpx.Response(200, json={'items': self.pending})
        ids = json.loads(request.content)['ids']
        self.pending = [n for n in self.pending if n['id'] not in ids]
        return httpx.Response(200, json={'confirmados': len(ids)})


class FakeInbox:
    def __init__(self, fail=False):
        self.items = {}
        self.fail = fail

    def ingest_items(self, source, items):
        if self.fail:
            raise RuntimeError('inbox down')
        for item in items:
            self.items.setdefault((source, item['id']), item)


def source(ms_pago, **kwargs):
    client = httpx.Client(base_url='http://ms_pago:8005', transport=httpx.MockTransport(ms_pago))
    return PaymentNotices(client, TOKEN, **kwargs)


def test_copies_into_the_inbox_and_then_confirms_with_the_service_secret():
    ms_pago, inbox = FakeMsPago([notice(1), notice(2)]), FakeInbox()
    assert source(ms_pago).ingest(inbox) == 2
    assert set(inbox.items) == {('ms_pago', 1), ('ms_pago', 2)}
    assert [c[:2] for c in ms_pago.calls] == [('GET', payment_notices.PENDING_PATH), ('POST', payment_notices.CONFIRM_PATH)]
    assert {c[2] for c in ms_pago.calls} == {TOKEN}
    assert ms_pago.pending == []
    assert source(ms_pago).ingest(inbox) == 0  # nothing pending: no confirmation call
    assert ms_pago.calls[-1][:2] == ('GET', payment_notices.PENDING_PATH)


def test_if_the_inbox_fails_nothing_is_confirmed_and_it_is_read_again():
    ms_pago = FakeMsPago([notice(1)])
    with pytest.raises(RuntimeError):
        source(ms_pago).ingest(FakeInbox(fail=True))
    assert [c[0] for c in ms_pago.calls] == ['GET'] and len(ms_pago.pending) == 1
    inbox = FakeInbox()
    assert source(ms_pago).ingest(inbox) == 1 and ms_pago.pending == []


def test_malformed_notices_are_neither_copied_nor_confirmed():
    bad = [notice(2, user_id='nadie'), notice(3, data='x'), notice(4, created_at='ayer'), notice('5')]
    ms_pago, inbox = FakeMsPago([notice(1), *bad]), FakeInbox()
    assert source(ms_pago).ingest(inbox) == 1
    assert set(inbox.items) == {('ms_pago', 1)} and len(ms_pago.pending) == 4


def test_ms_pago_errors_raise_so_the_worker_retries_later():
    failing = source(lambda request: httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        failing.ingest(FakeInbox())


def test_it_asks_once_a_minute_unless_a_full_batch_came():
    now = [1000.0]
    ms_pago = FakeMsPago([notice(1)])
    notices = source(ms_pago, clock=lambda: now[0])
    assert notices.due()
    notices.ingest(FakeInbox())
    assert not notices.due()
    now[0] += payment_notices.POLL_SECONDS
    assert notices.due()
    ms_pago.pending = [notice(i) for i in range(1, payment_notices.BATCH + 1)]
    notices.ingest(FakeInbox())
    assert notices.due()


def test_the_worker_reads_ms_pago_only_with_its_url_and_the_service_secret(monkeypatch):
    import app.services.notifications as module

    class Fake(NotificationWorker):
        def pool(self, url):
            return url

        def run(self):  # no network or database in this test
            pass

    monkeypatch.setattr(module, 'NotificationRepository', lambda pool: type('R', (), {'migrate': lambda self: None})())
    base = dict(jwt_secret=SECRET, notifications_database_url='postgresql://n')
    for extra, expected in (({}, False), ({'payments_service_url': 'http://ms_pago:8005'}, False),
                            ({'internal_service_token': TOKEN}, False),
                            ({'payments_service_url': 'http://ms_pago:8005', 'internal_service_token': TOKEN}, True)):
        worker = Fake(Settings(**base, **extra))
        worker.start()
        worker.thread.join()
        assert (worker.payments is not None) == expected
        if worker.payments is not None:
            assert worker.payments.headers == {'X-Internal-Service-Token': TOKEN}
            assert str(worker.payments.client.base_url).startswith('http://ms_pago:8005')
            worker.payments.client.close()


def test_payment_notices_reach_the_inbox_once_and_open_the_payment(system):  # noqa: F811
    client, repo, _, pool = system
    ms_pago = FakeMsPago([notice(7)])
    notices = source(ms_pago)
    notices.ingest(repo)
    repo.ingest_items('ms_pago', [notice(7)])  # a replay (crash before confirming) does not duplicate
    assert rows(pool, "SELECT user_id,kind,source,source_id FROM notifications.inbox WHERE source='ms_pago'") == [
        (USER, 'suscripcion', 'ms_pago', 7)]
    body = client.get('/api/v1/notifications', headers=headers(USER)).json()
    assert body['unread'] == 1
    assert body['items'][0]['data'] == {'screen': 'pago', 'planId': '1', 'ruta': '/pago?plan=1'}
