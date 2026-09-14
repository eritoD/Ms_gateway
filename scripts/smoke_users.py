"""Ejercita la integración real y limpia únicamente los usuarios creados por esta prueba.

Ejecutar con el entorno virtual de Ms_Users. No imprime tokens ni contraseñas.
"""
from pathlib import Path
from uuid import uuid4
import json
import os

import httpx
import psycopg
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
USERS = ROOT.parent / 'Ms_Users'
config = dotenv_values(USERS / '.env')
dsn = os.environ.get('USERS_DATABASE_URL') or config['USERS_DATABASE_URL']
base_url = os.environ.get('GATEWAY_URL', 'http://127.0.0.1:8000')
run_id = uuid4().hex
emails = [f'integration-{run_id}-{i}@example.com' for i in range(2)]
password = 'Integration-only-' + uuid4().hex
checks = []

def expect(response, status):
    assert response.status_code == status, f'{response.request.method} {response.request.url.path}: esperado {status}, recibido {response.status_code}'
    return response

with psycopg.connect(dsn, autocommit=True) as db, httpx.Client(base_url=base_url, timeout=20, trust_env=False) as client:
    assert db.execute('SELECT current_database()').fetchone()[0] == 'sportmach_users'
    baseline = db.execute('SELECT count(*) FROM usuario').fetchone()[0]
    try:
        expect(client.get('/health/ready'), 200)
        checks.append('Gateway, Ms_Users y PostgreSQL disponibles')
        users = []
        for email in emails:
            registration = expect(client.post('/api/v1/users/auth/register', json={
                'email': email, 'password': password, 'nombre': 'Integracion', 'apellido_paterno': 'Temporal',
            }), 201).json()
            users.append(registration)
            assert registration['user']['role'] == 'player'
        first, second = users
        user_id = first['user']['user_id']
        headers = {'Authorization': 'Bearer ' + first['access_token']}
        checks.append('Registro persistente con rol player y RUT opcional')
        expect(client.post('/api/v1/users/auth/register', json={'email': emails[0], 'password': password, 'nombre': 'Duplicado', 'apellido_paterno': 'Temporal'}), 409)
        login = expect(client.post('/api/v1/users/auth/login', json={'email': emails[0].upper(), 'password': password}), 200).json()
        assert login['user']['user_id'] == user_id
        expect(client.post('/api/v1/users/auth/login', json={'email': emails[0], 'password': 'incorrecta'}), 401)
        me = expect(client.get('/api/v1/security/me', headers=headers), 200).json()
        assert me['subject'] == user_id and me['roles'] == ['player']
        checks.append('Login, email normalizado y JWT aceptado por ambos servicios')
        prefix = '/api/v1/users/' + user_id
        expect(client.get(prefix + '/profile'), 401)
        expect(client.get('/api/v1/users/' + second['user']['user_id'] + '/profile', headers=headers), 403)
        checks.append('Sin token: 401; perfil de otro usuario: 403')
        expect(client.put(prefix + '/profile', headers=headers, json={'nombre': 'Persistido', 'apellido_paterno': 'Temporal', 'biografia': 'Prueba de integracion temporal'}), 200)
        assert expect(client.get(prefix + '/profile', headers=headers), 200).json()['nombre'] == 'Persistido'
        assert db.execute('SELECT nombre FROM usuario WHERE id=%s', (user_id,)).fetchone()[0] == 'Persistido'
        preferences = {'deportes': [{'deporte_codigo': 'tenis', 'nivel': 3}], 'disponibilidad': [{'dia_semana': 'lunes', 'hora_inicio': '18:00', 'hora_fin': '20:00'}], 'rango_distancia_km': '15'}
        expect(client.put(prefix + '/preferences', headers=headers, json=preferences), 200)
        assert expect(client.get(prefix + '/preferences', headers=headers), 200).json()['deportes'] == preferences['deportes']
        assert db.execute('SELECT count(*) FROM usuario_deporte WHERE usuario_id=%s', (user_id,)).fetchone()[0] == 1
        checks.append('Perfil y preferencias persistidos en sportmach_users')
        consent = expect(client.post(prefix + '/consents', headers=headers, json={'type': 'test', 'purpose': 'Prueba temporal', 'document_version': 'v1', 'method': 'integration-test'}), 201).json()
        revoked = expect(client.delete(prefix + '/consents/' + consent['id'], headers=headers), 200).json()
        assert revoked['status'] == 'revoked'
        assert len(expect(client.get(prefix + '/consents', headers=headers), 200).json()) == 1
        assert expect(client.get(prefix + '/exports', headers=headers), 200).json()['profile']['nombre'] == 'Persistido'
        assert expect(client.get(prefix + '/roles', headers=headers), 200).json() == {'role': 'player'}
        checks.append('Consentimientos, roles y exportacion a traves del gateway')
        db.execute("INSERT INTO notificacion (usuario_id,titulo,mensaje) VALUES (%s,'Prueba temporal','Referencia para comprobar desactivacion')", (user_id,))
        expect(client.delete(prefix, headers=headers), 204)
        assert db.execute('SELECT is_active FROM usuario WHERE id=%s', (user_id,)).fetchone()[0] is False
        expect(client.get(prefix + '/profile', headers=headers), 401)
        expect(client.post('/api/v1/users/auth/login', json={'email': emails[0], 'password': password}), 401)
        checks.append('Desactivacion compatible con referencias e invalidacion de acceso')
        # El usuario original del seed también debe funcionar con sus hashes existentes.
        seeded = expect(client.post('/api/v1/users/auth/login', json={'email': 'usuario02@sportmach.example.com', 'password': 'SportmachDemo2026!'}), 200).json()
        assert seeded['user']['user_id'] == '10000000-0000-4000-8000-000000000002'
        expect(client.get('/api/v1/users/' + seeded['user']['user_id'] + '/profile', headers={'Authorization': 'Bearer ' + seeded['access_token']}), 200)
        checks.append('Usuarios y contraseñas del seed original compatibles')
    finally:
        # Se eliminan exclusivamente fixtures con emails únicos de esta ejecución.
        with db.transaction():
            ids = [row[0] for row in db.execute('SELECT id FROM usuario WHERE email = ANY(%s)', (emails,))]
            if ids:
                db.execute('DELETE FROM notificacion WHERE usuario_id = ANY(%s)', (ids,))
                db.execute('DELETE FROM audit_events WHERE actor_user_id = ANY(%s)', (ids,))
                db.execute('DELETE FROM usuario WHERE id = ANY(%s)', (ids,))
        assert db.execute('SELECT count(*) FROM usuario').fetchone()[0] == baseline
    report = {'status': 'OK', 'gateway': base_url, 'database': 'sportmach_users', 'checks': checks, 'temporary_users_removed': True, 'original_user_count': baseline}
    output = ROOT / 'docs/integration-verification.json'
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))
