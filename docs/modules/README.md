# Documentación por módulo

Un archivo por dominio cuando se definan sus reglas de negocio.

Cuentas está implementado. Su contrato HTTP está en `docs/api/accounts.md`.
Farms v1 está implementado con listado, creación, consulta, edición, baja lógica,
membresías, roles y protección del último administrador; su contrato está en
`docs/api/farms.md`. El aprovisionamiento y la protección del entorno de
validación están en `docs/modules/farms-validation-environment.md`.

El backend de invitaciones a fincas (FARMS-108) está implementado y su diseño se
mantiene en `docs/modules/farms-108-invitaciones.md`. El frontend de aceptación y
la administración visual de invitaciones, así como la validación manual, siguen
pendientes. El resto de dominios sigue sin lógica funcional.

La base de los códigos de invitación (F10.3.1: generación, normalización,
huella HMAC y campo `code_hash`), su integración en los servicios (F10.3.2) y
su entrega por correo (F10.3.3-A; aún sin endpoints para usarlo) están en
`docs/modules/farms-invitation-codes.md`.

La arquitectura y los contratos técnicos de IoT (IoT-01, solo diseño, sin
código) están en `docs/modules/iot-arquitectura.md`, `iot-flujo-medicion.md`,
`iot-contrato-lora.md`, `iot-contrato-mqtt.md`, `iot-seguridad.md` e
`iot-persistencia-entrega.md`; sus decisiones y pendientes, en
`docs/decisions/DEC-13-arquitectura-iot.md`. El ciclo de vida de las
credenciales de gateway (IoT-03.1) está en
`docs/modules/iot-credenciales-gateway.md` y el de las credenciales de coordinador
(IoT-03.2) en `docs/modules/iot-credenciales-coordinador.md`. SensorSource y
la procedencia física de los sensores están documentados en
`docs/modules/iot-sensor-sources.md`.
La protección del historial de mediciones, correcciones y sus límites de base
de datos está en `docs/modules/iot-historial-correcciones.md`.

Las decisiones arquitectónicas aprobadas se indexan en
`docs/decisions/README.md`.

La guía operativa del orquestador desechable de validación está en
`docs/modules/orquestador-desechable-guia-operativa.md`.

La preparación del despliegue en Render (API, consumidor MQTT y worker durable),
sus variables y bloqueos de infraestructura están en
`docs/modules/iot-despliegue-render.md`.
El ciclo de vida de entregas fallidas, los comandos `mqtt_deliveries`, el
consumer endurecido y la política de fechas están en
`docs/modules/iot-hardening-pipeline.md`.
La validación VALIDATION-02 (reversión de migraciones, SIGTERM, cobertura
reproducible y revisión de CI) está en `docs/modules/iot-validacion-02.md`.
La identidad del broker MQTT (BUILD-FLEPSI-02, modo record), sus claves,
eventos y rotación están en `docs/modules/iot-identidad-broker.md`.
