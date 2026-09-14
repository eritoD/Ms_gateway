# SportMatch Backend

Gateway FastAPI integrado con el microservicio hermano `../Ms_Users`. El cliente usa una sola entrada, `http://localhost:8000`, y el gateway reenvía las rutas `/api/v1/users/...`. `Ms_Users` persiste en **sportmach_users**, dentro del servidor PostgreSQL local registrado como `sportmach` en pgAdmin.

## Ejecutar la integración

Mantén `Ms_Users` y `sportmatch-backend` como carpetas hermanas. Con Docker Desktop y PostgreSQL local iniciados:

```sh
cd /Users/elcacas/Desktop/soportmach/sportmatch-backend
docker compose up --build -d
docker compose ps -a
```

`users_migrate` aplica la migración y termina con código 0. Eso es normal. Después arrancan `ms_users` y `gateway`, esperando a que sus dependencias estén listas. El microservicio escucha dentro de Docker en `8001`; solo el gateway publica `127.0.0.1:8000` en el Mac.

Para detener los servicios:

```sh
docker compose stop
```

La base sigue siendo el PostgreSQL local en el puerto 5432. Compose no crea otro servidor de base de datos ni un volumen PostgreSQL.

## Probar con la app en el teléfono (Expo Go)

Por defecto el gateway solo escucha en `127.0.0.1`, así que un teléfono no puede alcanzarlo. Para probar la app de `../Frontend-SportMatch-APP`:

```sh
cd /Users/elcacas/Desktop/soportmach/sportmatch-backend
./scripts/dev-lan.sh          # publica el gateway en la IP local del Mac

cd ../Frontend-SportMatch-APP
npx expo start                # escanea el QR con Expo Go
```

El teléfono debe estar en la misma red Wi-Fi que el Mac. El script detecta la IP, la agrega a `ALLOWED_HOSTS` y habilita CORS para `expo start --web`; no modifica `.env`. Si cambias de red, vuelve a ejecutarlo. La app calcula la URL del gateway a partir de la IP con la que Expo sirvió el QR (`http://IP-del-Mac:8000`). Con `expo start --tunnel` esa IP no sirve: define `EXPO_PUBLIC_API_URL` con una URL alcanzable del gateway.

Si el teléfono no conecta, prueba abrir `http://IP-del-Mac:8000/health/live` en el navegador del teléfono. Si no carga, revisa que el firewall de macOS permita conexiones entrantes a Docker.

## Variables y archivos locales

| Variable | Uso |
|---|---|
| `USERS_DATABASE_URL` | Cadena de conexión de `Ms_Users` a `sportmach_users`. |
| `JWT_SECRET` | Secreto compartido para firmar en `Ms_Users` y verificar en el gateway. |
| `JWT_ISSUER` | `sportmatch-auth` en ambos servicios. |
| `JWT_AUDIENCE` | `sportmatch-mobile` en ambos servicios. |
| `JWT_ACCESS_TOKEN_MINUTES` | Vigencia del token: 30 minutos. |
| `USERS_SERVICE_URL` | Dirección HTTP de `Ms_Users`; Compose usa `http://ms_users:8001`. |
| `USERS_TIMEOUT_SECONDS` | Tiempo máximo por operación HTTP hacia `Ms_Users`. |

Ya se preparó `.env` con las credenciales locales y un secreto JWT aleatorio. No lo subas a Git. `.env.example` sirve como plantilla sin credenciales reales.

Desde Docker se utiliza `host.docker.internal:5432` para llegar al PostgreSQL del Mac; el `.env` de `../Ms_Users` utiliza `127.0.0.1:5432` para ejecución Python local. Es la misma base y el mismo usuario de PostgreSQL. El gateway recibe la URL HTTP del microservicio, no la contraseña de la base.

## Probar en Swagger o Postman

Abre [Swagger](http://localhost:8000/docs). En la sección **Users**, ejecuta `POST /api/v1/users/auth/login` con este usuario ficticio existente:

```json
{
  "email": "usuario02@sportmach.example.com",
  "password": "SportmachDemo2026!"
}
```

Copia `access_token` y pégalo en **Authorize → JWTBearer**, sin agregar manualmente `Bearer`. Utiliza el `user.user_id` devuelto para consultar el perfil. El ID de ese usuario de prueba es `10000000-0000-4000-8000-000000000002`.

También puedes registrar una cuenta desde `POST /api/v1/users/auth/register`:

```json
{
  "email": "nuevo@example.com",
  "password": "UnaClaveDePrueba2026!",
  "nombre": "Ana",
  "apellido_paterno": "Prueba"
}
```

El registro y el login son públicos. Las demás rutas exigen un token válido; `Ms_Users` comprueba además que se trate del propietario del recurso o de un administrador. El gateway valida firma, emisor, audiencia, vencimiento y tipo de token. Los tokens anteriores que no incluían estos campos requieren iniciar sesión de nuevo.

| Ruta | Uso |
|---|---|
| `GET /health/live` | Estado del proceso gateway. |
| `GET /health/ready` | Verifica gateway → Ms_Users → base USERS. |
| `POST /api/v1/users/auth/register` | Registro. |
| `POST /api/v1/users/auth/login` | Login y token. |
| `GET/PUT /api/v1/users/{user_id}/profile` | Perfil. |
| `GET /api/v1/users/{user_id}/roles` | Rol actual. |
| `GET/PUT /api/v1/users/{user_id}/preferences` | Preferencias y deportes declarados. |
| `GET/POST /api/v1/users/{user_id}/consents` | Consentimientos. |
| `DELETE /api/v1/users/{user_id}/consents/{consent_id}` | Revocar consentimiento. |
| `GET /api/v1/users/{user_id}/exports` | Exportación de datos de la cuenta. |
| `DELETE /api/v1/users/{user_id}` | Desactivar cuenta e impedir acceso posterior. |
| `GET /api/v1/security/me` | Verificar la identidad del token en el gateway. |

La colección de `../Ms_Users/postman` puede usar `http://localhost:8000` como URL base. El gateway conserva cuerpos, códigos HTTP y errores del microservicio. Si este no está disponible devuelve `503`; si vence el tiempo de espera devuelve `504`.

## Compatibilidad de la base existente

Antes de aplicar la migración se creó un respaldo de USERS en `/Users/elcacas/sportmach-db/backups/users-before-gateway-20260914-180300.dump`.

La migración `../Ms_Users/db/migrations/002_sportmach_users_integration.sql`:

- Conserva los usuarios, IDs, roles y las seis tablas originales de USERS.
- Agrega las tablas que necesita la API: `preferencia_usuario`, `disponibilidad`, `usuario_deporte`, `consent` y `audit_events`.
- Agrega el rol `player` para nuevas cuentas; no renombra el rol `usuario` de los datos anteriores.
- Permite RUT opcional de hasta 20 caracteres, como admite el contrato de `Ms_Users`.
- Garantiza que no existan emails duplicados por diferencias de mayúsculas.

Las preferencias, deportes declarados y disponibilidad de USERS son independientes de las tablas existentes en `sportmach_matching`: **no hay sincronización automática entre ambas bases**. Esa comunicación deberá definirse al integrar el microservicio de matching.

La eliminación de cuentas es lógica (`is_active=false`). Esto evita romper referencias desde notificaciones y membresías, y rechaza nuevos logins y solicitudes con tokens antiguos en las rutas de usuarios. El diagnóstico `/api/v1/security/me` solo valida el JWT; no consulta el estado de la cuenta. No se implementa aquí un borrado físico de datos personales.

## Pruebas

```sh
cd gateway
.venv/bin/python -m pytest -q
cd ../../Ms_Users
.venv/bin/python -m pytest -q
```

Prueba completa contra los servicios ya iniciados y PostgreSQL real:

```sh
cd /Users/elcacas/Desktop/soportmach/sportmatch-backend
../Ms_Users/.venv/bin/python scripts/smoke_users.py
```

La prueba crea cuentas temporales, verifica persistencia y permisos y limpia exclusivamente esas cuentas al terminar. El resultado está en [docs/integration-verification.json](docs/integration-verification.json). Los 12 usuarios del seed se conservaron.

Para ejecutar sin Docker, utiliza entornos virtuales separados. En `Ms_Users`, instala `pip install -e '.[dev]'` y ejecuta `python -m uvicorn app.main:app --env-file .env --host 127.0.0.1 --port 8001`. En `gateway`, instala `pip install -r requirements.txt` y ejecuta `python -m uvicorn app.main:app --env-file ../.env --host 127.0.0.1 --port 8000`. Detén primero los contenedores para liberar los puertos.

Referencias técnicas: [cliente HTTP asíncrono de HTTPX](https://www.python-httpx.org/async/) y [validación de claims en PyJWT](https://pyjwt.readthedocs.io/en/stable/usage.html).
