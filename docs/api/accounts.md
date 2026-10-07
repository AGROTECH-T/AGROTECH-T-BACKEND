# API de cuentas

Base local: `http://localhost:8080/api/v1`

Las entradas y respuestas usan `application/json`. El error tiene la forma `{"error": "mensaje"}`.

Salud de infraestructura, fuera de esta versión: `GET /api/health/`.

Documentación interactiva: `/api/schema/` y `/api/docs/`.

## Registrar cuenta

`POST /accounts/`

```json
{
  "first_name": "Ana",
  "last_name": "Pérez",
  "identification": "12345678",
  "phone": "3001234567",
  "correo": "ana@example.com",
  "password": "Secure1!"
}
```

`phone` o `correo` pueden ir vacíos, pero al menos uno es obligatorio.

- `201`: `{"ok": true}`
- `400`: campos o contraseña inválidos
- `409`: identificación ya registrada

## Iniciar sesión

`POST /auth/login/`

```json
{
  "identification": "12345678",
  "password": "Secure1!"
}
```

- `200`: `{"token": "<JWT cifrado>"}`
- `401`: credenciales inválidas
- `429`: demasiados intentos

La respuesta incluye `Cache-Control: no-store`. El token es un JWE A256GCM con el id interno y metadatos de seguridad cifrados.

## Consultar cuenta autenticada

`GET /auth/me/`

Requiere `Authorization: Bearer <token>`. Un token rotado, revocado o vencido responde `401`.

## Cerrar sesión

`POST /auth/logout/`

Revoca la sesión actual y responde `{"ok": true}`.

## Solicitar recuperación

`POST /auth/password/recovery/`

```json
{
  "identification": "12345678",
  "method": "correo"
}
```

`method` acepta `correo` o `whatsapp`.

- `200`: `{"ok": true, "wait": 300}`
- `400`: cuenta o canal no disponible
- `429`: OTP vigente; también retorna `wait`
- `502`: proveedor de correo no disponible
- `503`: Twilio no configurado o no disponible

## Cambiar contraseña

`POST /auth/password/reset/`

```json
{
  "identification": "12345678",
  "code": "123456",
  "password": "NewSecure2!"
}
```

- `200`: `{"ok": true}`
- `400`: entrada inválida
- `401`: código inválido, vencido o con cinco intentos

Al cambiar la contraseña se elimina la sesión anterior.

## Renovar sesión

`POST /auth/session/renew/`

Requiere `Authorization: Bearer <token>`.

- `200`: `{"token": "<nuevo JWT cifrado>"}`
- `401`: token o sesión vencida

Cada renovación invalida el token anterior. Los endpoints sensibles tienen límite por IP y por identificación. Cinco contraseñas incorrectas bloquean la cuenta de forma temporal.

## Alias temporales

Se conservan rutas anteriores para clientes ya publicados:

- `/api/v1/auth/register/`
- `/api/v1/registro`
- `/api/v1/login`
- `/api/v1/recuperar`
- `/api/v1/recuperar/codigo`
- `/api/v1/sesion`

Las rutas oficiales de esta página son las que debe usar un frontend nuevo.
