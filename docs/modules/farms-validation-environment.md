# Entorno de validación de Farms — aprovisionamiento y protección

> Registro FARMS-002.4. Documenta cómo se prepara, protege y usa la base de
> validación aislada de Farms. Este documento describe el procedimiento; **no lo
> ejecuta**. Complementa `config/settings_test.py`, `conftest.py`,
> `.github/workflows/ci.yml` y `docs/modules/farms-v1-closure.md`.

## 1. Objetivo

Tener una base MariaDB dedicada y aislada para las pruebas de Farms, sin riesgo
de tocar las bases reales ni las protegidas, y sin que una variable de entorno
pueda redirigir las pruebas a un destino arbitrario.

## 2. Bases protegidas (nunca usables por las pruebas)

- `agrotech_t`
- `agrotech_db`
- `agrotech_farms_test`

Estas tres bases son rechazadas explícitamente por `validate_test_database`
(`config/settings_test.py`), tanto en `NAME` como en `TEST.NAME`.

## 3. Destino autorizado (lista blanca cerrada)

La única base aceptada por la lista blanca es:

- Base: `agrotech_farms_validation`
- Motor: MariaDB / InnoDB (requerido para los `SELECT ... FOR UPDATE`)
- Charset: `utf8mb4`
- Host autorizado: `127.0.0.1` o `localhost`
- Puerto autorizado: `3307`
- Usuario autorizado: `agrotech_validation_user` (única entrada de
  `AUTHORIZED_TEST_DB_USERS`, lista cerrada)
- Alias de conexión permitido: únicamente `default`

## 4. Parametrización por variables de entorno

`config.settings_test.build_test_database()` lee estas variables, todas con
valores predeterminados seguros y validadas contra listas blancas cerradas:

| Variable | Valor por defecto | Lista blanca |
|---|---|---|
| `TEST_DB_NAME` | `agrotech_farms_validation` | `AUTHORIZED_TEST_DATABASES` |
| `TEST_DB_USER` | (sin valor por defecto) | `AUTHORIZED_TEST_DB_USERS` |
| `TEST_DB_PASSWORD` | (sin valor por defecto) | — |
| `TEST_DB_HOST` | `127.0.0.1` | `{127.0.0.1, localhost}` |
| `TEST_DB_PORT` | `3307` | `{3307}` |

Las variables generales (`DB_NAME`, `DB_ENGINE`, `DB_HOST`, `DB_PORT`,
`DATABASE_URL`) **no** alteran el destino de pruebas; `build_test_database` las
ignora por completo.

## 5. Protecciones en tiempo de ejecución

`conftest.py` mantiene dos barreras antes de que pytest-django abra una conexión:

1. `pytest_configure` valida que el `SETTINGS_MODULE` sea `config.settings_test`,
   que el alias sea solo `default` y que el destino esté en la lista blanca.
2. `pytest_fixture_setup` (sobre `django_db_setup`) exige:
   - `--reuse-db` sí presente.
   - `--create-db` rechazado (aborta con `pytest.UsageError`).
   - `TEST_DB_USER` y `TEST_DB_PASSWORD` definidos.
   - que el usuario pertenezca a `AUTHORIZED_TEST_DB_USERS`.
   - un preflight en solo lectura (`SELECT DATABASE(), CURRENT_USER(), @@port`
     y consulta a `information_schema.SCHEMATA`) que confirme base, usuario y
     puerto autorizados antes de preparar la base.

## 6. Pruebas de concurrencia

`apps/farms/tests/test_concurrency.py` valida la baja lógica y el orden de
bloqueo `Farm -> FarmMembership`:

- `test_concurrent_deactivation_keeps_single_audit`: dos bajas simultáneas dejan
  una sola fecha y un solo responsable (idempotencia).
- `test_update_cannot_finish_after_deactivation`: una escritura que pasa la
  comprobación previa no confirma si la finca se desactiva en paralelo.
- `test_deactivation_waits_for_inflight_update`: demuestra que la desactivación
  **no puede completar** mientras una escritura conserva el bloqueo de la fila
  dentro de una transacción real. Usa conexiones independientes por hilo, una
  señal verificable previa a la adquisición y una espera acotada sobre el evento
  de adquisición. **No** depende de `information_schema.innodb_trx` (que exigiría
  el privilegio global `PROCESS`); la exclusión mutua se demuestra de forma
  determinista por coordinación de eventos.

Estas pruebas requieren la base de validación y se ejecutan con
`pytest --reuse-db`; no pueden correr contra SQLite (dependen de `select_for_update`
sobre InnoDB).

## 7. Procedimiento de aprovisionamiento (NO ejecutado)

Se describe para su ejecución manual por el operador, **no** por la suite. No se
escriben contraseñas reales; el valor de contraseña proviene del entorno.

1. Crear la base (MariaDB, InnoDB por defecto, utf8mb4):

```sql
CREATE DATABASE IF NOT EXISTS agrotech_farms_validation
  CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
```

2. Crear el usuario dedicado restringido al host local (la contraseña vive en el
   entorno, no en el repo):

```sql
CREATE USER IF NOT EXISTS 'agrotech_validation_user'@'127.0.0.1'
  IDENTIFIED BY '<TEST_DB_PASSWORD>';
```

> Se restringe a `127.0.0.1` (host por defecto de `TEST_DB_HOST`). Si se usara
> `localhost` (que en MariaDB puede resolverse por socket), se necesitaría una
> cuenta adicional `'agrotech_validation_user'@'localhost'` con los mismos
> permisos acotados.

3. Otorgar permisos **solo** sobre la base de validación (nunca globales):

```sql
REVOKE ALL PRIVILEGES ON `agrotech\_farms\_validation`.*
  FROM 'agrotech_validation_user'@'127.0.0.1';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, DROP
  ON `agrotech\_farms\_validation`.* TO 'agrotech_validation_user'@'127.0.0.1';
FLUSH PRIVILEGES;
```

Justificación de cada privilegio (todos acotados a `agrotech_farms_validation.*`):

| Privilegio | Necesidad |
|---|---|
| `SELECT`, `INSERT`, `UPDATE`, `DELETE` | operación normal de las pruebas |
| `CREATE`, `ALTER`, `INDEX` | DDL de las migraciones aplicadas por pytest-django/Django |
| `DROP` | `TRUNCATE TABLE` que ejecuta pytest-django al limpiar los tests `django_db(transaction=True)`; acotado a la base de validación, nunca global |

No se conceden `ALL PRIVILEGES`, `SUPER`, `GRANT OPTION` ni `PROCESS`.

4. Aplicar el esquema **una sola vez** antes de la primera ejecución. Una base
   recién creada está vacía y `--reuse-db` **reutiliza el esquema existente, no
   lo crea ni lo migra**. Por eso, primero se prepara el esquema con la cuenta
   de pruebas:

```bash
TEST_DB_NAME=agrotech_farms_validation \
TEST_DB_USER=agrotech_validation_user \
TEST_DB_PASSWORD=<valor del entorno> \
TEST_DB_HOST=127.0.0.1 \
TEST_DB_PORT=3307 \
python manage.py migrate --settings=config.settings_test --no-input
```

   Esta conexión apunta exclusivamente a `agrotech_farms_validation`; la guarda
   `validate_test_database` rechaza cualquier otro destino antes de abrir conexión.

5. Verificar que **no** puede acceder a las tres bases protegidas. Cada consulta
   de abajo debe fallar con "access denied" (`ERROR 1044`/`1142`); si alguna
   devuelve filas, el aislamiento está roto:

```sql
-- Debe fallar:
SELECT 1 FROM agrotech_t.some_table;
SELECT 1 FROM agrotech_db.some_table;
SELECT 1 FROM agrotech_farms_test.some_table;
```

6. Definir las variables de entorno antes de lanzar pytest:

```text
TEST_DB_NAME=agrotech_farms_validation
TEST_DB_USER=agrotech_validation_user
TEST_DB_PASSWORD=<valor del entorno>
TEST_DB_HOST=127.0.0.1
TEST_DB_PORT=3307
```

7. Ejecutar la suite reutilizando la base (nunca `--create-db`):

```bash
python -m pytest --reuse-db
```

`--create-db` está prohibido y `conftest.py` aborta si se usa. El CI reproduce
este mismo aprovisionamiento con `mariadb:11.8.8` en el puerto `3307`
(`.github/workflows/ci.yml`).

## 8. Riesgos y límites

- Las migraciones no fijan el motor de tabla en el DDL; `select_for_update`
  depende de InnoDB (motor por defecto de MariaDB). Verificar el motor al
  aprovisionar.
- `TEST.NAME` está forzado a coincidir con `NAME`; cualquier desvío se rechaza.
- En CI el usuario se crea con host `'%'` (base efímera, aislada por red), a
  diferencia del procedimiento manual que usa `'127.0.0.1'`.

## 9. Estado y pendientes

- **Preparación documental:** completada (FARMS-002.4 y FARMS-101).
- **Aprovisionamiento de la base:** pendiente de autorización humana.
- **Migraciones iniciales:** pendientes (paso 4 del procedimiento).
- **Pruebas:** pendientes de ejecución contra la base aprovisionada.
- **Validación funcional de la concurrencia:** pendiente; no se declara validada
  hasta disponer de evidencia de ejecución.
- **Cierre de Farms:** no declarado.
