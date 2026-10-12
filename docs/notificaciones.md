# Notificaciones de SportMatch

Implementado el 10 de octubre de 2026. El frontend, Matching, Actividades y el gateway deben actualizarse juntos.

## Comportamiento

La bandeja muestra eventos reales del servidor y conserva su estado de lectura. No requiere autorizar push para consultar los avisos dentro de la app. El teléfono solo se registra después de tocar **Activar notificaciones** y conceder permiso de Android/iOS. La opción vive en la pantalla Notificaciones y aplica a ese dispositivo.

Eventos incluidos:

| Evento | Destinatario | Apertura |
| --- | --- | --- |
| Solicitud de conexión | Quien recibe la solicitud | Matches |
| Solicitud aceptada | Quien la envió | Chat del match |
| Mensaje | La otra persona del match | Chat |
| Postulación a actividad | Organizador | Actividad |
| Postulación aceptada o rechazada | Postulante | Actividad |
| Cambio o cancelación de actividad | Postulantes pendientes y aceptados, excepto organizador | Actividad |
| Calificación nueva o modificada | Deportista calificado | Perfil |

No se envía el texto privado del chat en la pantalla bloqueada. Las operaciones repetidas sin cambios no generan otro evento. No se generan avisos retrospectivos por los datos anteriores a la migración.

## Persistencia y entrega

Las migraciones `003_notification_outbox.sql` de Matching y `006_notification_outbox.sql` de Actividades crean triggers y una tabla de salida. El evento y el cambio de negocio comparten transacción: ambos confirman o ambos se deshacen. No hay llamadas de red en los triggers.

El gateway inicia un worker que consulta esas tablas cada dos segundos. Primero guarda el evento en `notifications.inbox`, dentro de la base configurada por `NOTIFICATIONS_DATABASE_URL`, y luego acusa recibo al origen. La clave `(source, source_id)` evita duplicados al recuperarse de una caída. El diseño no depende de que el celular esté abierto.

Cada usuario puede registrar varios teléfonos. Las entregas tienen estado propio por dispositivo; usan Expo Push Service, consultan sus recibos después de 15 minutos y desactivan tokens que devuelven `DeviceNotRegistered`. Los fallos transitorios se reintentan con espera creciente, durante un máximo de 24 horas desde el evento. Se envían lotes de hasta 100 mensajes. Un bloqueo de PostgreSQL evita dos despachadores simultáneos si hay varias réplicas del gateway. Las filas publicadas del origen se limpian después de siete días; la bandeja se conserva. No se envían pendientes ya leídas, anteriores al consentimiento ni a dispositivos sin actualizar durante 90 días.

La entrega externa es **al menos una vez**, no exactamente una vez: una caída entre la aceptación de Expo y el guardado del ticket puede repetir una alerta. Un recibo exitoso indica aceptación por FCM/APNs, no que la persona la vio. Conexión, ajustes del sistema y modos de ahorro pueden retrasar o impedir la presentación. La bandeja permanece disponible; con la app activa se actualiza al recibir push y cada 15 segundos como respaldo.

Al desactivar o cerrar sesión se revoca el registro mediante un secreto aleatorio por instalación (almacenado como hash en el servidor), incluso si el JWT expiró. Si no se puede confirmar la revocación, la app conserva la sesión y permite reintentar con conexión. Una alerta que ya fue entregada al proveedor puede estar en tránsito; no puede retirarse remotamente. La app limpia las alertas locales al desvincular y valida la cuenta del payload antes de abrir destinos. Nunca navega a URLs arbitrarias del payload.

## Configuración del backend

El Compose ya configura las tres conexiones del worker usando las bases existentes; `notifications` es un esquema separado en la base de Usuarios. Para otro despliegue:

```dotenv
NOTIFICATIONS_DATABASE_URL=postgresql://.../sportmach_users
MATCHING_DATABASE_URL=postgresql://.../sportmach_matching
ACTIVITIES_DATABASE_URL=postgresql://.../sportmach_activities
PUSH_ENABLED=true
EXPO_ACCESS_TOKEN=
```

`PUSH_ENABLED=false` es el valor inicial: permite bandeja e ingestión, pero rechaza activaciones del celular y no contacta Expo. Habilitarlo cuando el proyecto y las credenciales móviles estén preparados. Si se activa la seguridad mejorada de Expo Push, configurar `EXPO_ACCESS_TOKEN` **solo en el backend**. No subir credenciales a Git ni usar variables `EXPO_PUBLIC_*` para secretos.

Las migraciones se ejecutan al iniciar cada servicio y son idempotentes. En este equipo:

```powershell
docker compose -f compose.yaml -f compose.windows.yaml up --build -d gateway ms_matching ms_activities
```

Para un despliegue con usuarios de base restringidos, el worker necesita leer/actualizar/eliminar las tablas de salida y administrar su propio esquema de notificaciones. No es necesario leer perfiles o mensajes para enviar push. Conviene separar esas credenciales de las de negocio fuera del entorno local.

## Configuración móvil pendiente del proyecto

Los identificadores y las credenciales reales no estaban en el repositorio. No se crearon proyectos externos ni se inventaron identificadores.

1. Vincular la app al proyecto Expo/EAS de SportMatch. Configurar `EXPO_PUBLIC_EAS_PROJECT_ID` o `extra.eas.projectId`, el `EXPO_ANDROID_PACKAGE` y el `EXPO_IOS_BUNDLE_IDENTIFIER` reales. `app.config.ts` admite estas variables y respeta valores existentes en `app.json`.
2. Android: registrar ese paquete en Firebase; configurar el archivo cliente `google-services.json` mediante `GOOGLE_SERVICES_JSON` y las credenciales **FCM v1** del servidor en EAS. El archivo de cuenta de servicio no va en el cliente ni en Git.
3. iOS: configurar el identificador y la clave APNs con la cuenta Apple Developer, y aprovisionar el dispositivo para builds internos.
4. Definir `EXPO_PUBLIC_API_URL` con un gateway HTTPS accesible desde el celular. Proveer las variables también al entorno EAS de compilación; no basta con tenerlas solo en `.env.local`.
5. Compilar e instalar: `npx eas-cli build --profile preview --platform android` (APK interno), o el equivalente para iOS. `eas.json` incluye el perfil. Puede requerir iniciar sesión y aprovisionar credenciales con la cuenta propietaria. **Expo Go no sirve para validar este flujo de push**; la bandeja sí funciona allí.
6. Habilitar `PUSH_ENABLED=true` en el backend y recrear el gateway.

Documentación oficial consultada: [Expo Notifications SDK 54](https://docs.expo.dev/versions/v54.0.0/sdk/notifications/), [configurar push](https://docs.expo.dev/push-notifications/push-notifications-setup/), [tickets, recibos y reintentos](https://docs.expo.dev/push-notifications/sending-notifications/).

## Prueba de aceptación en dos celulares

Con cuentas A y B y las compilaciones instaladas:

1. B rechaza el permiso. A envía solicitud: B la ve en la bandeja, sin alerta del sistema.
2. B habilita el permiso y pulsa Activar. Repetir con una nueva solicitud válida y confirmar alerta con la app en segundo plano y cerrada normalmente.
3. B acepta: A recibe el match. Un mensaje abre el chat correcto al tocar la alerta.
4. Postular, aceptar y luego cambiar hora/lugar o cancelar una actividad. Comprobar destinatarios y apertura del detalle.
5. Desactivar alertas: nuevos eventos quedan en la bandeja sin push. Revocar permiso desde ajustes y volver a abrir la app: el registro se desactiva.
6. Cerrar sesión y entrar con otra cuenta: no abrir avisos de la anterior; la nueva cuenta debe activar sus alertas.
7. Cortar temporalmente la salida a Expo y restablecerla: comprobar reintentos y recibos sin perder el aviso en la bandeja.

Pruebas automatizadas: `npm run test:notifications` en frontend; `pytest tests/test_notifications.py` en gateway con `NOTIFICATIONS_TEST_ADMIN_URL` apuntando a un PostgreSQL de pruebas con permiso CREATEDB. Estas crean y eliminan bases desechables y simulan Expo; no mandan push reales.

## Verificación realizada

- Frontend: 52 pruebas aprobadas (11 de notificaciones, 14 de actividades y 27 de descubrimiento), TypeScript sin errores y lint sin errores. Persiste una advertencia previa de dependencias de efecto en `MatchModal.tsx`.
- Backend: 73 pruebas del gateway, 14 de Matching y 73 de Actividades aprobadas, con PostgreSQL real en esquemas o bases de prueba aislados. Las nueve pruebas nuevas del gateway cubren autenticación, consentimiento, destinatarios, rollback, deduplicación, paginación, tickets/recibos, reintentos y revocación.
- Exportación de bundles Android y web completada. El render estático sigue mostrando advertencias previas por grupos de rutas declarados sin layout propio; no impidieron exportar.
- Contenedores locales reconstruidos y saludables; `/health/ready` respondió 200, bandeja sin JWT respondió 401 y con JWT respondió 200. `PUSH_ENABLED=false` permanece configurado.
- La descarga de dependencias dentro de Docker encontró un problema de confianza TLS del entorno. La imagen local del gateway se construyó con wheels Linux descargados desde el host con TLS verificado y un Dockerfile temporal; no se desactivó la validación de certificados ni se modificó el Dockerfile del proyecto. Reconstrucciones futuras en esta red requieren configurar la CA de confianza del entorno de build.
- Pendiente: compilación móvil con credenciales reales, FCM/APNs y prueba de recepción en segundo plano/cerrada. Una exportación de JavaScript y las pruebas con Expo simulado no certifican entrega en un teléfono.
