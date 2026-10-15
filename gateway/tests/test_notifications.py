"""Notification integration tests use a newly created disposable database only.

Set NOTIFICATIONS_TEST_ADMIN_URL to a PostgreSQL connection with CREATEDB.
No push requests leave this process: Expo is always an httpx MockTransport.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import jwt
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg_pool import ConnectionPool

from app.api.routes.notifications import RegisterDevice
from app.core.config import Settings, get_settings
from app.main import create_app
from app.repositories.notifications import NotificationRepository
from app.services.notifications import EXPO_URL, NotificationWorker

SECRET = 'notifications-tests-secret-with-more-than-32-bytes'
A, B, C = uuid4(), uuid4(), uuid4()
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def system():
    url = os.environ.get('NOTIFICATIONS_TEST_ADMIN_URL')
    if not url:
        pytest.skip('Set NOTIFICATIONS_TEST_ADMIN_URL for disposable PostgreSQL tests')
    name = 'notifications_test_' + uuid4().hex
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    pool = ConnectionPool(make_conninfo(url, dbname=name), min_size=1, max_size=4, open=True)
    try:
        repo = NotificationRepository(pool)
        repo.migrate()
        with pool.connection() as conn:
            for service in ('ms-machine', 'Ms_activities'):
                paths = sorted((ROOT / service / 'db/migrations').glob('*.sql'))
                assert paths, f'Missing migrations for {service}'
                for path in paths:
                    conn.execute(path.read_text(encoding='utf-8'))
        settings = Settings(jwt_secret=SECRET, push_enabled=True)
        worker = NotificationWorker(settings)
        worker.repository = repo
        app = create_app()
        app.dependency_overrides[get_settings] = lambda: settings
        with TestClient(app) as client:
            app.state.notifications = repo
            yield client, repo, worker, pool
    finally:
        pool.close()
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))


def headers(user):
    now = datetime.now(timezone.utc)
    return {'Authorization': 'Bearer ' + jwt.encode({'sub': str(user), 'iat': now,
        'exp': now + timedelta(minutes=10), 'iss': 'sportmatch-auth', 'aud': 'sportmatch-mobile',
        'token_type': 'access'}, SECRET, algorithm='HS256')}


def device():
    return RegisterDevice(device_id=uuid4(), revoke_secret=uuid4(), token=f'ExpoPushToken[{uuid4().hex}]', consent=True)


def request(pool):
    rid = uuid4()
    with pool.connection() as conn:
        conn.execute('INSERT INTO matching_api.requests(id,sender_id,recipient_id) VALUES (%s,%s,%s)', (rid,A,B))
    return rid


def activity(pool):
    aid = uuid4()
    with pool.connection() as conn:
        conn.execute('''INSERT INTO activities_api.activities(id,organizer_id,client_activity_id,title,sport_code,starts_at,location)
            VALUES (%s,%s,%s,'Running juntos','running',CURRENT_TIMESTAMP + interval '1 day','Parque central')''', (aid,A,uuid4()))
    return aid


def ingest(repo, pool):
    repo.ingest(pool, 'matching_api')
    repo.ingest(pool, 'activities_api')


def rows(pool, query, params=None):
    with pool.connection() as conn:
        return conn.execute(query, params).fetchall()


def test_auth_consent_ownership_and_revocation(system):
    client, repo, _, pool = system
    d = device().model_dump(mode='json')
    assert client.put('/api/v1/notifications/devices', json=d).status_code == 401
    assert client.put('/api/v1/notifications/devices', json={**d, 'consent': False}, headers=headers(A)).status_code == 422
    assert client.put('/api/v1/notifications/devices', json=d, headers=headers(A)).status_code == 204
    assert client.put('/api/v1/notifications/devices', json={**d,'revoke_secret':str(uuid4())}, headers=headers(C)).status_code == 403
    request(pool)
    ingest(repo,pool)
    a = client.get('/api/v1/notifications', headers=headers(A)).json()
    b = client.get('/api/v1/notifications', headers=headers(B)).json()
    assert a['items'] == [] and b['unread'] == 1
    nid = b['items'][0]['id']
    client.post('/api/v1/notifications/read', json={'ids':[nid]}, headers=headers(A))
    assert repo.inbox(B,None,50)['unread'] == 1
    client.post('/api/v1/notifications/read', json={'ids':[nid]}, headers=headers(B))
    assert repo.inbox(B,None,50)['unread'] == 0
    # Capability revocation works without an unexpired login, but wrong secrets do nothing.
    client.post('/api/v1/notifications/devices/revoke', json={**d,'revoke_secret':str(uuid4())})
    assert rows(pool,'SELECT enabled FROM notifications.devices')[0][0]
    client.post('/api/v1/notifications/devices/revoke', json=d)
    assert not rows(pool,'SELECT enabled FROM notifications.devices')[0][0]


def test_transactional_events_idempotency_and_correct_recipients(system):
    _, repo, _, pool = system
    rid = request(pool)
    with pool.connection() as conn:
        conn.execute('INSERT INTO matching_api.requests(id,sender_id,recipient_id) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING', (uuid4(),A,B))
        conn.execute("UPDATE matching_api.requests SET status='accepted',decided_at=CURRENT_TIMESTAMP WHERE id=%s", (rid,))
        conn.execute("UPDATE matching_api.requests SET status='accepted' WHERE id=%s", (rid,))
        mid=uuid4()
        for _ in range(2):
            conn.execute("INSERT INTO matching_api.messages(match_id,sender_id,client_message_id,text) VALUES (%s,%s,%s,'Hola') ON CONFLICT DO NOTHING",(rid,A,mid))
    with pytest.raises(RuntimeError), pool.connection() as conn:
        conn.execute('INSERT INTO matching_api.requests(id,sender_id,recipient_id) VALUES (%s,%s,%s)',(uuid4(),B,C))
        raise RuntimeError('rollback')
    ingest(repo,pool)
    # Simulate a crash after destination commit but before source acknowledgment.
    with pool.connection() as conn:
        conn.execute('UPDATE matching_api.notification_outbox SET published_at=NULL')
    ingest(repo,pool)
    assert rows(pool,'SELECT user_id,kind FROM notifications.inbox ORDER BY created_at,source_id') == [(B,'solicitud'),(A,'match'),(B,'mensaje')]


def test_activity_events_exclude_actor_and_ignore_noop_updates(system):
    _, repo, _, pool = system
    aid = activity(pool)
    bid, cid = uuid4(),uuid4()
    with pool.connection() as conn:
        conn.execute('INSERT INTO activities_api.activity_applications(id,activity_id,applicant_id) VALUES (%s,%s,%s),(%s,%s,%s)',(bid,aid,B,cid,aid,C))
        conn.execute("UPDATE activities_api.activity_applications SET status='accepted' WHERE id=%s",(bid,))
        conn.execute("UPDATE activities_api.activity_applications SET status='rejected' WHERE id=%s",(cid,))
        conn.execute('UPDATE activities_api.activities SET accepted_count=1 WHERE id=%s',(aid,))
        conn.execute("UPDATE activities_api.activities SET location='Otra cancha' WHERE id=%s",(aid,))
        conn.execute("UPDATE activities_api.activities SET location='Otra cancha' WHERE id=%s",(aid,))
        conn.execute('UPDATE activities_api.activities SET cancelled_at=CURRENT_TIMESTAMP WHERE id=%s',(aid,))
        rate=uuid4()
        conn.execute("INSERT INTO activities_api.ratings(id,rater_id,rated_id,score) VALUES (%s,%s,%s,5)",(rate,A,B))
        conn.execute('UPDATE activities_api.ratings SET score=5 WHERE id=%s',(rate,))
    ingest(repo,pool)
    assert [r['title'] for r in repo.inbox(A,None,50)['items']] == ['Nueva postulación','Nueva postulación']
    b = repo.inbox(B,None,50)['items']
    assert {r['title'] for r in b} == {'Postulación aceptada','Cambios en tu actividad','Actividad cancelada','Recibiste una calificación'}
    assert len(b) == 4 and len(repo.inbox(C,None,50)['items']) == 1


def test_expo_tickets_receipts_and_no_delivery_without_consent(system):
    _, repo, worker, pool = system
    d = device()
    repo.register(B,d)
    request(pool)
    ingest(repo,pool)
    sent=[]
    def transport(req):
        sent.append(json.loads(req.content))
        if req.url.path.endswith('/send'):
            return httpx.Response(200,json={'data':[{'status':'ok','id':'ticket-1'}]})
        return httpx.Response(200,json={'data':{'ticket-1':{'status':'ok'}}})
    with httpx.Client(base_url=EXPO_URL,transport=httpx.MockTransport(transport)) as client:
        worker.deliver(client)
        worker.deliver(client)
        assert len(sent)==1 and sent[0][0]['data']['userId']==str(B)
        assert rows(pool,'SELECT status FROM notifications.deliveries')==[('receipt',)]
        with pool.connection() as conn:
            conn.execute('UPDATE notifications.deliveries SET next_attempt=CURRENT_TIMESTAMP')
        worker.deliver(client)
        assert rows(pool,'SELECT status FROM notifications.deliveries')==[('sent',)]
    assert repo.inbox(B,None,50)['unread']==1


@pytest.mark.parametrize('error', ['DeviceNotRegistered','MessageRateExceeded','network'])
def test_provider_failures_and_retry(system,error):
    _,repo,worker,pool=system
    d=device(); repo.register(B,d)
    request(pool); ingest(repo,pool)
    def transport(req):
        if error=='network': raise httpx.ConnectError('offline',request=req)
        return httpx.Response(200,json={'data':[{'status':'error','details':{'error':error}}]})
    with httpx.Client(base_url=EXPO_URL,transport=httpx.MockTransport(transport)) as client:
        worker.deliver(client)
    state=rows(pool,'SELECT status,attempts,next_attempt>CURRENT_TIMESTAMP FROM notifications.deliveries')[0]
    assert state==(('failed' if error=='DeviceNotRegistered' else 'pending'),1,True)
    assert rows(pool,'SELECT enabled FROM notifications.devices')[0][0] == (error!='DeviceNotRegistered')


def test_disabled_device_and_account_switch_never_receive_old_queue(system):
    _,repo,worker,pool=system
    d=device(); repo.register(B,d)
    request(pool); ingest(repo,pool)
    repo.revoke(d.device_id,d.revoke_secret)
    sends=[]
    with httpx.Client(base_url=EXPO_URL,transport=httpx.MockTransport(lambda req: sends.append(req))) as client:
        worker.deliver(client)
        assert sends==[]
        repo.register(C,d)
        worker.deliver(client)
        assert sends==[]
    assert len(repo.inbox(B,None,50)['items'])==1


def test_pagination_and_marking_visible_do_not_read_new_arrivals(system):
    _,repo,_,pool=system
    request(pool); ingest(repo,pool)
    first=repo.inbox(B,None,1)
    with pool.connection() as conn:
        conn.execute('INSERT INTO matching_api.requests(id,sender_id,recipient_id) VALUES (%s,%s,%s)',(uuid4(),C,B))
    ingest(repo,pool)
    repo.mark_read(B,[first['items'][0]['id']])
    page=repo.inbox(B,None,1)
    assert page['unread']==1 and page['next_cursor']
    assert len(repo.inbox(B,page['next_cursor'],1)['items'])==1


def test_clubs_outbox_reaches_the_inbox_of_the_right_person(system):
    _, repo, _, pool = system
    with pool.connection() as conn:
        conn.execute('CREATE SCHEMA clubs_api')
        conn.execute('''CREATE TABLE clubs_api.notification_outbox (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            user_id uuid NOT NULL, kind text NOT NULL, title text NOT NULL, body text NOT NULL, data jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP, published_at timestamptz)''')
        conn.execute("INSERT INTO clubs_api.notification_outbox(user_id,kind,title,body,data) VALUES (%s,'postulacion','Revisión','Te preseleccionaron','{}')", (B,))
    for _ in range(2):  # a replay must not duplicate
        repo.ingest(pool, 'clubs_api')
    assert rows(pool, "SELECT user_id,kind,source FROM notifications.inbox WHERE source='clubs_api'") == [(B, 'postulacion', 'clubs_api')]
    assert rows(pool, 'SELECT count(*) FROM clubs_api.notification_outbox WHERE published_at IS NULL') == [(0,)]


def test_the_worker_reads_clubs_only_when_configured(monkeypatch):
    import app.services.notifications as module

    class Fake(NotificationWorker):
        def pool(self, url):
            return url

        def run(self):  # no network or database in this test
            pass

    monkeypatch.setattr(module, 'NotificationRepository', lambda pool: type('R', (), {'migrate': lambda self: None})())
    for url, expected in ((None, ['matching_api', 'activities_api']),
                          ('postgresql://c', ['matching_api', 'activities_api', 'clubs_api'])):
        worker = Fake(Settings(jwt_secret=SECRET, notifications_database_url='postgresql://n', matching_database_url='postgresql://m',
                               activities_database_url='postgresql://a', clubs_database_url=url))
        worker.start()
        worker.thread.join()
        assert [schema for _, schema in worker.sources] == expected
