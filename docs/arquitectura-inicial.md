> Estado actualizado: gateway y `../Ms_Users` ya están integrados con `sportmach_users`. Consulta la guía principal `sportmatch-backend/README.md`. Este documento conserva el planteamiento inicial.

# Arquitectura inicial

SportMatch adopta un monorepo que, con el tiempo, contendrá varios microservicios independientes. En el paso actual solo el gateway existe como aplicación.

```text
Aplicación móvil
       ↓
     Gateway
       ↓
Usuarios · Matching · Actividades · Clubes
```

Las flechas hacia los microservicios representan la relación futura. El gateway actual no los invoca ni simula sus respuestas.

## Responsabilidades

La aplicación móvil consumirá un punto de entrada estable. El gateway recibirá las solicitudes, aplicará responsabilidades transversales que se definan posteriormente y enviará cada operación al servicio dueño del dominio. Cada microservicio será responsable de sus datos y su lógica, y no importará código interno de otro servicio.

La comprobación `/health/ready` actual solo certifica que la configuración del gateway cargó correctamente. Cuando existan dependencias reales, su significado y sus comprobaciones deberán revisarse.

## Conceptos diferentes

- **Repositorio:** conjunto versionable de archivos. Este monorepo conserva gateway, documentación y futuros servicios en un mismo lugar.
- **Servicio:** aplicación con una responsabilidad de negocio concreta y una interfaz propia.
- **Contenedor:** paquete ejecutable aislado de un servicio, construido a partir de su Dockerfile.
- **Despliegue:** instancia de un servicio ejecutada en un entorno. Puede tener configuración, escala y ciclo de publicación propios.

Compartir un repositorio no convierte los servicios en una sola aplicación. Cada microservicio podrá tener dependencias, pruebas, contenedor y despliegue independientes.
