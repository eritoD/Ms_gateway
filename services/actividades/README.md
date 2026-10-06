# Servicio de Actividades

Implementado en [Ms_Activities](../../../Ms_Activities/README.md), como servicio independiente con rutas, servicios, repositorios y PostgreSQL propio.

Compose levanta `ms_activities:8003`; el gateway publica `GET/POST /api/v1/activities` y `GET /api/v1/activities/{id}`. Requiere `ACTIVITIES_DATABASE_URL` y el JWT compartido con Users.

Esta entrega permite publicar, listar y ver detalles. Inscripciones y cupos no están implementados.
