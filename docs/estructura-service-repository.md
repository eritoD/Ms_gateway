# Convención Service–Repository

Esta es la estructura estándar elegida para organizar cada aplicación Python/FastAPI. Su objetivo es que las rutas HTTP, lógica, persistencia y contratos de datos no se mezclen.

```text
app/
├── api/
│   ├── dependencies/
│   ├── routes/
│   └── router.py
├── schemas/
├── services/
├── repositories/
├── models/
├── core/
└── main.py
```

## Flujo permitido

```text
Route → Service → Repository → Model/Database
```

- La ruta conoce HTTP, Pydantic y los servicios.
- El servicio implementa el caso de uso y no conoce `Request` o `Response` de FastAPI.
- El repositorio concentra consultas y transacciones de persistencia.
- El modelo representa una tabla o documento persistente.
- El schema representa el contrato HTTP y no una tabla.

Una ruta nunca utiliza directamente una sesión de SQLAlchemy. Un repositorio no decide reglas de negocio y un modelo SQLAlchemy no se devuelve directamente como contrato público.

## Regla para repositorios

No se crea un repositorio por cada clase. Solo se crea cuando el servicio posee o consulta una fuente persistente como PostgreSQL, Redis o almacenamiento de objetos.

El gateway es stateless y por eso conserva el paquete `repositories/` como parte de la plantilla, pero no contiene una implementación ficticia. Cuando el gateway se conecte a otro microservicio se utilizará un cliente HTTP bajo `clients/`; una API externa no se disfraza como repositorio.

## Gateway actual

```text
gateway/app/
├── api/
│   ├── dependencies/
│   │   ├── security.py
│   │   └── services.py
│   ├── routes/
│   │   ├── gateway.py
│   │   ├── health.py
│   │   └── security.py
│   └── router.py
├── schemas/
│   ├── gateway.py
│   ├── health.py
│   └── security.py
├── services/
│   ├── gateway_service.py
│   ├── health_service.py
│   └── token_service.py
├── repositories/
│   └── __init__.py
├── core/
│   ├── config.py
│   └── http_security.py
└── main.py
```

En los microservicios que tengan base de datos se agregan:

```text
models/
└── activity_model.py

repositories/
└── activity_repository.py
```

Ejemplo de Actividades:

```text
POST /activities
        ↓
activities.py               Route
        ↓
activity_service.py         Regla y caso de uso
        ↓
activity_repository.py      Consulta o persistencia
        ↓
activity_model.py           Tabla SQLAlchemy
        ↓
PostgreSQL
```

## Convenciones obligatorias

- Carpetas y archivos en `snake_case`.
- Clases de servicios terminan en `Service`.
- Clases de repositorios terminan en `Repository`.
- Schemas HTTP terminan en `Request` o `Response`.
- Modelos persistentes terminan en `Model`.
- Las rutas reciben servicios mediante dependencias.
- Los tests unitarios de servicios no requieren FastAPI ni PostgreSQL.
- Los tests de repositorios son de integración y sí utilizan una base aislada.
- Ningún microservicio importa código interno de otro microservicio.
