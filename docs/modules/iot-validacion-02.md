# Validación del backend IoT (BUILD-BACKEND-IOT-VALIDATION-02)

Estado: **PARCIALMENTE VALIDADO** en entorno desechable (MariaDB 11.8.8). Sin
integración real con Flespi, Layerbase, Render ni ESP32.

## Reproducir la validación con cobertura

```powershell
.\tools\run_permissions_disposable.ps1 -Validate -CoverageIot `
  -SettingsModule config.settings_test_disposable_iot -Tests apps/iot/tests `
  -MariaDbImageDigest sha256:24e76fcec8c003a0362d0dd53f4806e7e79458d7fdeaf47437760e19496f5a9c `
  -OutputDirectory <ruta-absoluta-fuera-del-repositorio>
```

`-CoverageIot` solo se admite con `-Validate`. Añade a pytest
`--cov=apps/iot --cov-branch --cov-config=<OutputDirectory>\coveragerc-iot` y
escribe `coverage-iot.{data,xml,json}` en `OutputDirectory`. No modifica el
preflight, la guarda de `--reuse-db`, el puerto 3318, la imagen fijada por
digest ni las rutas de prueba autorizadas. La configuración mide todo
`apps/iot` con ramas, **incluidas las migraciones**; solo omite
`apps/iot/tests/*` (código de prueba, no producto). El umbral del proyecto
(`--cov-fail-under=80` en `pyproject.toml`) no se modificó.

Recalcular sin migraciones a partir de los mismos datos:

```powershell
.venv\Scripts\python.exe -m coverage report --data-file=<OutputDirectory>\coverage-iot.data --omit="apps/iot/migrations/*,apps/iot/tests/*"
```

## Reversión de migraciones IoT

| Migración | Reversión | Motivo |
|---|---|---|
| 0016 (CHECK de actor) | Libre | Solo cambia una restricción; sin datos |
| 0015 / 0014 (bandeja MQTT) | Barrera explícita en 0015 | Borraría entregas durables |
| 0013 (guards de evidencia) | Barrera + desinstalación verificada | Quita la protección de la evidencia aceptada |
| 0012 (triggers de historial) | Ya era reversible con verificación de definiciones | — |
| 0011 (UUID y correcciones) | Barrera explícita | Borraría correcciones y UUID públicos |

- Defecto encontrado: Django deshace las migraciones de la más nueva a la más
  antigua y solo comprueba la reversibilidad al llegar a cada una. Un
  `migrate iot 0012` **eliminaba las tablas de 0015/0014** antes de fallar en
  0013. Ahora la barrera de 0015 rechaza la operación antes de tocar nada que
  contenga datos.
- La barrera solo se abre con el setting de Django
  `IOT_ALLOW_DESTRUCTIVE_HISTORY_ROLLBACK=True`, que no existe en
  `config.settings`, no se lee del entorno y solo activan las pruebas sobre la
  base desechable (`apps/iot/migrations/_rollback_guard.py`).
- Con la barrera abierta se probó en MariaDB desechable: bajar a 0001/zero,
  volver a subir y verificar tablas, FK compuestas y definiciones exactas de
  los triggers.
- Estrategia en producción: no revertir. Ante un despliegue fallido, volver a
  la imagen anterior (las migraciones son compatibles hacia adelante) y
  restaurar desde respaldo si hiciera falta. Cualquier reversión real es un
  procedimiento de administración revisado.

## SIGTERM y ciclo de vida (`apps/iot/process_signals.py`)

| Proceso | Comportamiento probado |
|---|---|
| MQTT consumer | SIGTERM → `stop()`: no persiste ni confirma mensajes nuevos; una persistencia en curso termina antes de su ACK; no reconecta; sale con código 0. Lo no confirmado lo reentrega la sesión persistente. |
| Delivery worker | SIGTERM → termina el lote en curso (acotado) y sale con 0; la espera ociosa se interrumpe en ≤0,5 s. Una muerte abrupta deja el lease, que se recupera al vencer (pruebas J y crash). |
| API (gunicorn) | Gunicorn gestiona SIGTERM de forma nativa (cierre ordenado con `graceful_timeout`). **No probado**: gunicorn no se ejecuta en Windows. |

Sin manejador, Python como PID 1 ignora SIGTERM en un contenedor y la
plataforma termina enviando SIGKILL. La prueba entre procesos
(`test_real_process_handles_sigterm_and_exits_zero`) solo se ejecuta en POSIX
(CI Linux); en Windows `os.kill(SIGTERM)` es `TerminateProcess`.

## CI (`.github/workflows/ci.yml`), revisión estática

- Permisos: `SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, DROP, INDEX,
  REFERENCES, TRIGGER` solo sobre `agrotech_farms_validation` del contenedor
  `mariadb:11.8.8` en `127.0.0.1:3307`. Sin binlog, no requiere `SUPER`. Nada
  apunta a Layerbase.
- Protección: `config.settings_test` solo admite la base
  `agrotech_farms_validation`, el host local, el puerto 3307 y el usuario de
  validación; `--reuse-db` es obligatorio.
- Cobertura: `pyproject.toml` ya ejecuta `--cov` (accounts, farms,
  trazabilidad, iot) con `--cov-fail-under=80`.
- Bloqueante previo: el paso `ruff check .` falla con 118 errores en código
  existente (ninguno en archivos nuevos de HARDEN-01 ni VALIDATION-02).
- La imagen de CI usa la etiqueta `mariadb:11.8.8`, no un digest.
- **Pendiente de CI remoto**: no se ejecutó GitHub Actions.
