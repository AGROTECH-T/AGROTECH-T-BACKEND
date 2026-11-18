# IoT-03.1 — Ciclo de vida de las credenciales de gateway

- **Etapa:** IoT-03.1
- **Estado de implementación:** Servicios de emisión, verificación, rotación y
  revocación más API administrativa BUILD-IOT-02 implementados. Integración de
  credenciales y ACL en Mosquitto pendiente.
- **Decisión asociada:** [DEC-13 §12](../decisions/DEC-13-arquitectura-iot.md)
- **Fecha:** 2026-11-13

## 1. Propósito

Dar a cada gateway una credencial propia, verificable por el backend, que pueda
emitirse, rotarse y revocarse con auditoría, sin guardar nunca el secreto.

## 2. Especificación técnica

### 2.1 Componentes

| Componente | Archivo | Responsabilidad |
|---|---|---|
| Clave del servidor | `config/gateway_credential_key.py`, `config/platform.py`, `config/settings.py` | `GATEWAY_CREDENTIAL_HMAC_KEY`: obligatoria y validada al arrancar en producción (`DEBUG=False`); en desarrollo y pruebas falla de forma cerrada al usarse. Mínimo 32 caracteres, distinta de `SECRET_KEY`, sin alternativa |
| Funciones puras | `apps/iot/gateway_credentials.py` | Secreto, huella HMAC-SHA256 y verificación en tiempo constante |
| Modelo | `apps/iot/models.py` (`GatewayCredential`, `RevocationCause`) | Huella, versión de clave, estado y revocación |
| Servicios | `apps/iot/services/credentials.py` | Emitir, rotar, revocar, verificar e historial |
| Migraciones | `iot.0003_gatewaycredential`, `trazabilidad.0010` | Tabla y eventos de auditoría |

### 2.2 Secreto y huella

- Secreto: `gws_` + `secrets.token_urlsafe(32)` (256 bits; 47 caracteres).
- Huella: `HMAC-SHA256(clave, "agrotech:gw-cred:v1:{gateway_uid}:{secreto}")`
  en hexadecimal (64 caracteres). Ligada al `uid`: el secreto de un gateway no
  sirve para otro.
- Comparación con `hmac.compare_digest`.
- Se guarda solo la huella y `key_version` (hoy `1`). La clave nunca se guarda.

### 2.3 Modelo `GatewayCredential` (`iot_gateway_credentials`)

| Campo | Regla |
|---|---|
| `gateway` | FK con PROTECT. Sin `farm`: la finca se obtiene del gateway |
| `fingerprint` | Único; `CHECK` `^[0-9a-f]{64}$` |
| `key_version` | `CHECK >= 1` |
| `active_slot` | 1 vigente, NULL revocada; `UNIQUE (gateway, active_slot)`: una vigente por gateway, historial ilimitado (MariaDB admite varios NULL) |
| `issued_at` / `issued_by` | Emisión |
| `revoked_at` / `revoked_by` / `revocation_cause` | `rotated`, `reissued`, `compromised`, `gateway_deactivated`, `admin` |

`CHECK` de estado (con `IS NOT NULL` explícito): vigente sin datos de revocación
y sin causa; revocada con fecha y causa válida; `revoked_at >= issued_at`. Sin
caducidad ni `last_used_at`.

### 2.4 Operaciones

| Operación | Servicio | Reglas |
|---|---|---|
| Emitir | `issue_gateway_credential` | Administrador; finca y gateway activos; sin credencial vigente (si existe, 409: hay que rotar) |
| Rotar | `rotate_gateway_credential(cause=rotated|reissued)` | Revoca la vigente y emite otra **en la misma transacción**; sin coexistencia. Si algo falla, todo se revierte y la anterior sigue vigente |
| Revocar | `revoke_gateway_credential(cause=admin|compromised)` | La credencial no vuelve a activarse; para recuperar acceso se emite otra |
| Desactivar gateway | `devices.deactivate_gateway` | Revoca la vigente (`gateway_deactivated`) en la misma transacción |
| Reactivar gateway | `devices.reactivate_gateway` | No restaura la credencial: hay que emitir otra |
| Verificar | `verify_gateway_credential(uid, secreto)` | Devuelve `VerifiedGateway(gateway_id, gateway_uid, farm_id)` o `None` |
| Historial | `list_gateway_credentials` | Cualquier integrante activo; nunca expone secretos |

Orden de escritura: autorización (membresía y rol administrador) →
`require_mariadb()` → validación → operación y auditoría. Orden de bloqueo:
Farm → FarmMembership → Gateway → credenciales.

### 2.5 Verificación

- `uid` en forma canónica estricta y secreto bien formado.
- Busca la credencial vigente del gateway; usa la clave de su `key_version`.
- Rechaza (`None`) sin distinguir el motivo: `uid` inexistente, secreto
  incorrecto, credencial revocada, gateway inactivo o finca inactiva. Calcula
  siempre una huella para no revelar el motivo por el tiempo.
- La finca sale del gateway registrado, nunca del mensaje.
- No escribe nada. Una `key_version` desconocida no verifica y deja un aviso en
  el log con el identificador interno de la credencial (sin secreto).
- La falta de clave del servidor se propaga (`ImproperlyConfigured`).

### 2.6 Finca

Desactivar la finca **no revoca** credenciales: la verificación las rechaza
mientras la finca esté inactiva. Si la finca se reactiva, una credencial no
revocada vuelve a ser válida.

### 2.7 Auditoría

| Evento | Cuándo |
|---|---|
| `iot_gateway_credential_issued` | Emisión |
| `iot_gateway_credential_rotated` | Rotación o reemisión (`metadata.cause`) |
| `iot_gateway_credential_revoked` | Revocación administrativa |
| `iot_gateway_credential_compromised` | Revocación por compromiso |
| `iot_gateway_credential_cascade_revoked` | Revocación por desactivación del gateway |

Datos: `gwcred_id` (identificador interno de la credencial), `gateway_uid`,
`key_version`, `active`, `revocation_cause`; en la rotación,
`metadata.previous_gwcred_id`. Nunca el secreto, la huella ni la clave. Los
nombres evitan `credential` porque `record_audit_event` rechaza toda clave que
contenga un término sensible, y esa protección no se relaja. La desactivación
del gateway registra `metadata.revoked_gwcreds`.

### 2.8 Protección del secreto

- Se devuelve una sola vez en `IssuedGatewayCredential.secret` (`repr=False`).
- No se persiste, no se audita y no aparece en excepciones ni `repr`.
- `sensitive_variables` en las funciones que lo manejan, para los informes de
  error de Django. No es la única protección: tampoco se interpola en mensajes.
- `common/logging.py` no redacta: por eso el código nunca registra el secreto.

### 2.9 Rotación futura de la clave del servidor (no implementada)

Publicar la clave nueva con una `key_version` mayor, emitir con ella las
credenciales nuevas y seguir verificando las existentes con su versión hasta
reemitirlas. Así ninguna credencial se invalida al rotar la clave.

## 3. Explicación sencilla

Cada gateway recibe una "contraseña" larga y aleatoria que solo se ve una vez.
El backend no la guarda: guarda una huella de la que no puede reconstruirse.
Para comprobarla, vuelve a calcular la huella y la compara. Si se pierde, no se
puede recuperar: se emite otra y la anterior deja de servir en ese mismo
instante. Si el gateway se desactiva, su credencial se anula. Si la finca se
desactiva, la credencial queda en pausa y vuelve a funcionar si la finca se
reactiva.

## 4. Ejemplo práctico

1. El administrador emite la credencial de `GW-7K3M9QXT2A` y graba el secreto
   en el gateway.
2. Meses después, rota la credencial: el secreto viejo deja de verificar en el
   mismo momento y se graba el nuevo.
3. El gateway se avería y se desactiva: su credencial queda revocada con causa
   `gateway_deactivated`. Al repararlo y reactivarlo, se emite una credencial
   nueva.

## 5. Decisiones aprobadas (IoT-03.0)

1. La autenticación MQTT futura usará credenciales administradas por Mosquitto.
2. Rotación sin transición: la anterior se revoca de inmediato.
3. Desactivar un gateway revoca sus credenciales activas.
4. Desactivar la finca bloquea la autenticación sin revocar; al reactivarla,
   una credencial no revocada vuelve a ser válida.
5. `GATEWAY_CREDENTIAL_HMAC_KEY` independiente de `SECRET_KEY`, obligatoria y
   validada al arrancar en producción.
6. Secreto `gws_` + `secrets.token_urlsafe(32)`.
7. `last_used_at` aplazado a IoT-05.
8. Sin caducidad automática.
9. Las claves HMAC de coordinadores son independientes y van en IoT-03.2.

## 6. Aspectos pendientes

- **Integración con Mosquitto:** la huella HMAC de Django **no equivale** al hash
  de contraseña que necesita Mosquitto. La integración deberá resolver cómo
  aprovisionar la credencial en el broker y propagar las revocaciones sin
  persistir secretos en texto claro. No se ha diseñado ni implementado.
- `last_used_at` (IoT-05).
- Claves de coordinadores (IoT-03.2).
- API REST de administración (sin endpoints en esta etapa).
- Rotación operativa de la clave del servidor (§2.9).

## 7. Riesgos y consideraciones

- **Despliegue:** producción no arranca sin `GATEWAY_CREDENTIAL_HMAC_KEY`. Debe
  configurarse antes de desplegar esta versión.
- Perder la clave del servidor invalida todas las huellas: debe respaldarse.
- El secreto queda en la flash del gateway; su protección depende de P-IOT-23.
- Mientras no exista integración con el broker, revocar en Django no desconecta
  a un cliente MQTT ya conectado (no hay broker todavía).
