# API de fincas

Base local: `http://localhost:8080/api/v1`

Todas las rutas requieren la autenticación Bearer existente:

```http
Authorization: Bearer <token>
```

Los errores mantienen el contrato general del backend: `{"error": "mensaje"}`.

En `POST /farms/` el único campo permitido es `name`. Cualquier campo adicional,
incluidos `role`, `user` o un campo desconocido, se rechaza con `400`.

## Roles de membresía

El rol pertenece a la relación entre una cuenta y una finca; no es un rol global
de la cuenta.

| Valor interno | Nombre funcional |
|---|---|
| `administrador` | `ADMINISTRADOR` |
| `operador` | `OPERADOR` |
| `consulta` | `CONSULTA` |

La API devuelve el valor interno. El creador de una finca recibe automáticamente
`administrador` en esa finca.

Los roles se almacenan y se devuelven, pero todavía no otorgan permisos
diferenciados dentro de la finca.

## Campos públicos de una finca

| Campo | Descripción |
|---|---|
| `id` | Identificador de la finca |
| `name` | Nombre de la finca; máximo 100 caracteres y puede repetirse |
| `role` | Rol de la cuenta autenticada en esa finca |
| `created_at` | Fecha de creación |
| `updated_at` | Fecha de actualización |

## Listar fincas propias

`GET /farms/`

Devuelve únicamente las fincas donde la cuenta autenticada tiene membresía.

```json
[
  {
    "id": 1,
    "name": "La Esperanza",
    "role": "administrador",
    "created_at": "2026-10-23T06:48:00-05:00",
    "updated_at": "2026-10-23T06:48:00-05:00"
  }
]
```

- `200`: listado de fincas propias
- `401`: solicitud sin sesión válida

## Crear una finca

`POST /farms/`

```json
{
  "name": "La Esperanza"
}
```

El cliente solo puede enviar `name`. Los campos `role`, `user`, `user_id` o
cualquier otro campo desconocido se rechazan con `400`. El usuario creador se
obtiene de la sesión y la membresía inicial siempre es `administrador`. La finca
y la membresía se crean en una sola transacción.

### Validación del nombre

- Es obligatorio.
- Se rechaza si está vacío o compuesto únicamente por espacios.
- El máximo es de 100 caracteres; un nombre de exactamente 100 es válido.
- Los espacios internos se conservan; no se colapsan.

- `201`: finca creada
- `400`: nombre inválido o campo no permitido
- `401`: solicitud sin sesión válida

Respuesta `201`:

```json
{
  "id": 1,
  "name": "La Esperanza",
  "role": "administrador",
  "created_at": "2026-10-23T06:48:00-05:00",
  "updated_at": "2026-10-23T06:48:00-05:00"
}
```

## Consultar una finca

`GET /farms/{id}/`

- `200`: la cuenta pertenece a la finca
- `401`: solicitud sin sesión válida
- `404`: la finca no existe o la cuenta no pertenece a ella

El `404` se usa también para fincas ajenas, con el mismo cuerpo de error que para
una finca inexistente, de modo que no se revela su existencia.

## Limitaciones actuales

- Los roles se almacenan y se devuelven, pero no existe autorización diferenciada
  por rol.
- No existen endpoints para editar o eliminar fincas, invitar personas,
  administrar membresías, cambiar roles ni seleccionar una finca activa.
- La política definitiva de eliminación de cuentas y fincas queda pendiente.
