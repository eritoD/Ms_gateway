> Estado actualizado: gateway y `../Ms_Users` ya están integrados con `sportmach_users`. Consulta la guía principal `sportmatch-backend/README.md`. Este documento conserva el planteamiento inicial.

# Seguridad del gateway

El gateway valida la identidad presentada por la aplicación móvil y aplica autorización general antes de enrutar una solicitud. No registra usuarios, no comprueba contraseñas, no genera tokens y no almacena roles. Esas responsabilidades pertenecerán a Usuarios/Auth.

## Flujo futuro

```text
Inicio de sesión:
Móvil → Gateway → Usuarios/Auth → token de acceso y token de renovación

Solicitud protegida:
Móvil → Gateway valida JWT → Microservicio valida la regla sobre el recurso
```

El gateway puede rechazar una ruta que requiere el scope `clubs:write`. El servicio Clubes debe comprobar además si el usuario puede modificar el club específico solicitado. La autorización de negocio no se delega exclusivamente al gateway.

## Validación implementada

Un access token debe contener:

- `sub`: identificador del usuario.
- `iss`: `JWT_ISSUER` esperado.
- `aud`: `JWT_AUDIENCE` esperada.
- `iat`: momento de emisión.
- `exp`: vencimiento.
- `token_type`: debe ser `access`; un refresh token no se acepta.
- `roles`: lista opcional de roles generales.
- `scope` o `scopes`: permisos generales opcionales.

El algoritmo se obtiene exclusivamente de la configuración confiable del gateway, no del encabezado controlado por el token.

## Configuración

| Variable | Propósito |
| --- | --- |
| `AUTH_REQUIRED` | Hace obligatoria una clave válida durante el inicio. Usar `true` al conectar Usuarios/Auth. |
| `JWT_ALGORITHM` | `HS256` para desarrollo integrado o `RS256` con clave pública. |
| `JWT_ISSUER` | Emisor exacto permitido. |
| `JWT_AUDIENCE` | Aplicación o API destinataria. |
| `JWT_SECRET` | Secreto de al menos 32 bytes para HS256. Nunca se versiona. |
| `JWT_PUBLIC_KEY` | Clave pública PEM para RS256. El gateway nunca necesita la clave privada. |
| `JWT_LEEWAY_SECONDS` | Margen pequeño ante diferencias de reloj. |
| `ALLOWED_HOSTS` | Hosts HTTP aceptados por el gateway. |
| `CORS_ALLOWED_ORIGINS` | Orígenes web permitidos, separados por coma. No es necesario para una app móvil nativa. |
| `ENABLE_HSTS` | Añade HSTS; activar únicamente cuando el dominio use HTTPS. |

`.env.example` deja las claves vacías intencionalmente. Para producción se deben inyectar desde un gestor de secretos, no escribirlas en Compose ni en el repositorio.

## Rutas de diagnóstico

- `GET /api/v1/security/me`: requiere un access token válido y muestra `sub`, roles y scopes verificados.
- `GET /api/v1/security/admin-check`: requiere rol `admin` y scope `gateway:read`.

Estas rutas permiten comprobar la integración hasta que exista Usuarios/Auth. No reemplazan endpoints de perfiles ni contienen reglas de negocio.

## Protecciones HTTP implementadas

- Validación del encabezado `Host`.
- Lista explícita de orígenes CORS cuando se configura.
- Identificador `X-Request-ID` validado o generado por solicitud.
- Cabeceras `nosniff`, anti-framing, política de referer y permisos del navegador.
- HSTS opcional para despliegues HTTPS.

## Controles pendientes de infraestructura

Rate limiting distribuido, WAF, TLS público, balanceo, mTLS interno y almacenamiento centralizado de auditoría requieren infraestructura de despliegue. No conviene simularlos con memoria local porque el gateway deberá ejecutarse con múltiples réplicas.

Cuando exista Usuarios/Auth, se recomienda que firme con una clave privada asimétrica y publique claves mediante JWKS. El gateway conservará solamente claves públicas y deberá contemplar rotación, caché y timeouts. Hasta entonces, HS256 permite pruebas integradas locales usando un secreto efímero de desarrollo.
