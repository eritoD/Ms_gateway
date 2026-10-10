# Servicio de Actividades

Implementado en [Ms_Activities](../../../Ms_Activities/README.md), como servicio independiente con rutas, servicios, repositorios y PostgreSQL propio.

Compose levanta `ms_activities:8003`; el gateway publica `GET/POST /api/v1/activities`, `GET /api/v1/activities/{id}`, `PATCH /api/v1/activities/{id}` (modificar) y `DELETE /api/v1/activities/{id}` (eliminar: la actividad queda cancelada), ambos solo para el organizador, y las postulaciones: `POST /api/v1/activities/{id}/applications` (queda pendiente), `GET /api/v1/activities/{id}/applications/me` (y `DELETE` para cancelarla) `GET /api/v1/activities/{id}/applications` y `POST /api/v1/activities/{id}/applications/{application_id}/accept|reject` (solo organizador; aceptar descuenta un cupo). Requiere `ACTIVITIES_DATABASE_URL` y el JWT compartido con Users.

También publica `GET /api/v1/activities/history`, `GET /api/v1/activities/{id}/participants` y las calificaciones: `PUT /api/v1/ratings/{user_id}`, `GET /api/v1/ratings/me` y `GET /api/v1/ratings/given`, que resuelve Ms_Activities (consulta a Ms_Matching para validar matches).

Permite publicar con cupos opcionales, listar, ver detalles, modificar y eliminar (cancelar) actividades propias, postular y que el organizador acepte o rechace postulaciones.
