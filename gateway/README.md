> Estado actualizado: gateway y `../Ms_Users` ya están integrados con `sportmach_users`. Consulta la guía principal `sportmatch-backend/README.md`. Este documento conserva el planteamiento inicial.

# SportMatch Gateway

Servicio de entrada del backend de SportMatch. Actualmente expone únicamente información propia y comprobaciones de vida y preparación; aún no enruta solicitudes a otros servicios.

La ruta `GET /prueba` devuelve una respuesta JSON sencilla para comprobar manualmente que el gateway está funcionando.

El paquete `app/services/token_service.py` valida access tokens JWT. Las dependencias HTTP reutilizables para roles y scopes viven en `app/api/dependencies/security.py`. Las rutas de diagnóstico protegidas están bajo `/api/v1/security`. Ningún secreto está incorporado al código o a la imagen.

## Organización Service–Repository

- `api/routes/`: recibe y responde HTTP; no implementa lógica.
- `api/dependencies/`: construye servicios y traduce autenticación a errores HTTP.
- `schemas/`: contratos Pydantic de entrada y salida.
- `services/`: comportamiento del gateway y validación JWT.
- `repositories/`: acceso persistente. Está deliberadamente sin implementaciones porque el gateway no posee datos.
- `core/`: configuración y middleware transversal.

Los futuros clientes hacia Usuarios, Actividades, Matching y Clubes deberán ubicarse en `clients/`, no en `repositories/`, porque representan APIs remotas y no datos propiedad del gateway.

## Desarrollo local

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Pruebas:

```bash
python -m pytest
```

La aplicación lee su configuración desde variables de entorno y usa valores seguros de desarrollo cuando no se proporcionan. No busca automáticamente un `.env`, por lo que el comportamiento no depende del directorio desde el cual se inicia el proceso.

La configuración JWT y las decisiones que deben permanecer en Usuarios/Auth o en cada microservicio se explican en `../docs/seguridad-gateway.md`.
