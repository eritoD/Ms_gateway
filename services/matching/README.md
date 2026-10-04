# Servicio de Matching

Responsabilidad general: encontrar y gestionar conexiones deportivas compatibles entre usuarios.

Funciones:

- Solicitudes de match y sus estados: implementado.
- Mensajería entre matches aceptados: implementado.
- Búsqueda geográfica y cálculo avanzado de compatibilidad: pendientes.

**Estado actual:** Implementado como microservicio independiente en
[`../../../Ms_Matching`](../../../Ms_Matching/README.md), con carpetas de API,
servicios, repositorios, esquemas, configuración y base de datos.

La API `/api/v1/matching` gestiona solicitudes, aceptación/rechazo, matches y chat.
Compose lo ejecuta como `ms_matching:8002`; el gateway es la entrada pública.
