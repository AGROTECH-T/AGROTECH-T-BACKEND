# DEC-09 — Alcance del mutex del orquestador desechable

- **Identificador:** DEC-09
- **Título:** Alcance del mutex del orquestador desechable
- **Estado de decisión:** Aprobada
- **Estado de implementación:** Aplazada parcialmente (mutex implementado;
  validación entre sesiones de Windows aplazada)
- **Fecha:** 2026-11-11

## Contexto

El orquestador desechable (`tools/run_permissions_disposable.ps1`) crea un
contenedor MariaDB que publica `127.0.0.1:3317`, un puerto único en toda la
máquina. Dos ejecuciones simultáneas competirían por ese puerto y por Docker.
Para impedirlo, el orquestador usa un mutex con nombre de Windows en el espacio
de nombres global.

El problema que registra esta decisión es la distancia entre lo que el código
pretende y lo que está demostrado. La exclusión entre procesos de una misma
sesión está probada. La exclusión entre sesiones distintas de Windows, o entre
usuarios distintos, no lo está.

## Decisión

- Se mantiene la implementación actual del mutex, sin cambios.
- Se registra que la exclusión **solo está verificada entre procesos de una
  misma sesión**.
- La validación entre sesiones y usuarios distintos queda **aplazada** por
  decisión del usuario. **No se considera superada.**
- La prueba manual descrita más abajo es una propuesta; **no está ejecutada ni
  autorizada**.

## Implementación existente

En `tools/disposable_orchestrator_lib.ps1` (líneas a fecha de 2026-11-11):

- **Nombre:** `$script:OrchestratorMutexName = "Global\AgrotechDisposableOrchestrator"`
  (línea 2009).
- **Adquisición:** `Enter-OrchestratorLock` (líneas 2011-2031).
  - `New-Object System.Threading.Mutex($false, <nombre>)` crea el mutex o abre
    el existente. Cualquier excepción, incluido el acceso denegado, devuelve
    `$false`.
  - `WaitOne(0)` no espera: si otro proceso lo posee, devuelve `$false` y se
    libera el handle.
  - `AbandonedMutexException` se trata como adquisición correcta. Según .NET,
    al recibir esa excepción el llamante ya es el propietario.
- **Liberación:** `Exit-OrchestratorLock` (líneas 2033-2039): `ReleaseMutex()`
  con errores silenciados, `Dispose()` y la referencia a `$null`. Si el proceso
  muere, el sistema cierra el handle y el mutex queda abandonado.
- **Alcance de la adquisición:**
  - Modo normal: se adquiere tras validar los parámetros y antes de crear
    cualquier directorio o recurso (línea 2590). Se libera en el `finally`,
    después de la limpieza (línea 2678).
  - `-CleanupOnly`: se adquiere en la línea 2455 y se libera en la línea 2518.
  - Ambos modos usan el mismo nombre y se excluyen entre sí.
- **Mutex ocupado o imposible de abrir:** el mensaje es "Otra invocación del
  orquestador está en curso." y el código de salida 50 (líneas 2591-2592 y
  2456-2457), sin crear contexto, estado ni recursos.
- **Seguridad del objeto:** no se especifica `MutexSecurity` ni una DACL
  explícita. Se aplica la DACL por defecto del token del creador.

## Evidencia verificada

Evidencia de pruebas ejecutadas en M3-2.4.3 y M3-2.4.4 (Pester 3.4.0, sin
fallos):

- El nombre usa el prefijo `Global\` (prueba "usa el espacio de nombres
  Global\").
- Exclusión entre procesos de la misma sesión: un proceso hijo PowerShell
  retiene el mutex y `Enter-OrchestratorLock` devuelve `$false`. Al terminar el
  hijo, la adquisición devuelve `$true` (prueba "otro proceso (otra sesión
  simulada) que tiene el mutex impide entrar"). Pese al nombre de la prueba, el
  hijo se ejecuta en la **misma** sesión.
- Puerto ocupado en la comprobación previa: código 50 y estado
  `NO_RESOURCES_CREATED`, sin `compose up`. Prueba unitaria con mocks.
- Puerto tomado durante `compose up` ("port is already allocated"): código 40 y
  eliminación solo de la red propia. Prueba unitaria con mocks.

## Aspectos inferidos y no verificados

| Aspecto | Clasificación |
|---|---|
| Exclusión entre sesiones distintas del mismo usuario | No verificado (comportamiento esperado por el espacio de nombres `Global\`) |
| Exclusión entre usuarios distintos | No verificado (depende de la DACL por defecto y de los privilegios) |
| Concurrencia real entre sesiones | No ejecutada |
| Rama del mutex abandonado (`AbandonedMutexException`) | Inferida por inspección. La prueba termina al hijo sin liberar, pero no comprueba de forma explícita que se tome esa rama |
| Flujo completo con el mutex ocupado → código 50 sin recursos | Inferido por inspección. Las pruebas del flujo simulan el mutex como libre |
| No hace falta `SeCreateGlobalPrivilege` para crear un mutex en `Global\` | Inferido del comportamiento documentado de la plataforma; no comprobado en este equipo |

## Misma sesión frente a sesiones distintas

- **Misma sesión:** todos los procesos comparten el espacio de nombres y el
  token del usuario. Exclusión **verificada**.
- **Sesiones distintas, mismo usuario:** `Global\` hace el objeto visible para
  todas las sesiones, por lo que se espera exclusión. **No verificado.**
- **Usuarios distintos:** sin DACL explícita, un segundo usuario estándar puede
  no tener acceso al mutex creado por otro. En ese caso el constructor falla y
  la ejecución termina con 50: falla cerrado, aunque el mensaje no indica la
  causa real. **No verificado.**
- **Fuera del alcance del mutex:** otra copia o versión del orquestador con
  otro nombre de mutex, y cualquier programa ajeno que use el puerto 3317.

## Relación con el puerto 3317 y los códigos 50 y 40

- `Test-LoopbackPortFree` comprueba cualquier listener TCP de la máquina en el
  puerto 3317, sin filtrar por dirección. Se ejecuta después de buscar recursos
  preexistentes y antes de generar credenciales y de `compose up`.
- **Puerto ya ocupado en la comprobación previa:** código **50**, sin crear
  recursos.
- **Puerto tomado entre la comprobación y `compose up`:** es una carrera entre
  comprobación y uso. La ejecución perdedora falla en `compose up` y devuelve
  código **40** (INFRASTRUCTURE_FAILED). `Update-Ownership` registra lo que llegó
  a crearse y la limpieza se limita a los recursos propios de esa ejecución,
  según las pruebas unitarias con mocks.
- **No todas las colisiones de puerto terminan con 50.**
- El puerto actúa como segunda barrera si el mutex no excluyera: una segunda
  ejecución vería el puerto publicado por la primera. Las ejecuciones tienen
  RunId distintos, así que la búsqueda de recursos preexistentes no las detecta
  entre sí.
- La ejecución perdedora no elimina recursos de la ganadora: la limpieza exige
  la etiqueta `agrotech.owner-proof` de su propia invocación.

## Riesgos residuales

- **R-1:** la exclusión entre sesiones y entre usuarios no está verificada. Si
  fallara, quedan como barreras el puerto 3317 y la propiedad de recursos.
- **R-2:** carrera entre la comprobación del puerto y `compose up`. Resultado
  40 con limpieza propia; no afecta a la ejecución ganadora.
- **R-3:** sin DACL explícita, un usuario puede recibir acceso denegado y salir
  con 50 y un mensaje poco preciso. Falla cerrado.
- **R-4:** el flujo completo con el mutex ocupado no está probado; solo existe
  respaldo por inspección.
- **R-5:** la recuperación tras un mutex abandonado no está validada de forma
  explícita. Los recursos de la ejecución interrumpida pueden seguir vivos; los
  cubren la comprobación del puerto (50) y `-CleanupOnly`.

## Justificación del aplazamiento

- Lo decidió el usuario en M3-2.4.4.
- El uso previsto del orquestador es monousuario, en una sola estación de
  trabajo.
- El puerto 3317 y la verificación de propiedad acotan el impacto de un fallo
  del mutex: no se eliminan recursos ajenos.
- La prueba requiere preparar una segunda sesión de Windows y, para el caso
  multiusuario, otra cuenta. Eso excede el alcance de M3-2.4.4.

## Prueba manual futura (propuesta; no ejecutada ni autorizada)

1. **Sesión A:** retener `Global\AgrotechDisposableOrchestrator` con un proceso
   PowerShell mínimo que no ejecute el orquestador, o lanzar una ejecución
   `-Validate`.
2. **Sesión B, mismo usuario** (RDP o cambio rápido de usuario): ejecutar el
   orquestador. Resultado esperado: código 50, mensaje "Otra invocación del
   orquestador está en curso." y ningún recurso creado.
3. **Sesión B, usuario estándar distinto:** repetir. Resultado esperado: código
   50, por mutex ocupado o por acceso denegado.
4. **Opcional:** con el mutex libre y el puerto 3317 ocupado por la sesión A.
   Resultado esperado: código 50 y estado `NO_RESOURCES_CREATED`.

## Condiciones de seguridad para la prueba futura

- Autorización expresa e independiente.
- Equipo sin otros usuarios trabajando durante la prueba.
- Imagen `mariadb:11.8.8` ya en local con el digest de DEC-08; sin descargas.
- Inventario previo de contenedores, redes y volúmenes, y comparación posterior.
- Ningún acceso a `agrotech_db` ni `agrotech_t`.
- Recuperación prevista mediante `-CleanupOnly`.
- Evidencia guardada fuera del repositorio.
- No modificar permisos de Windows ni crear DACL manualmente.

## Consecuencias

- El mutex protege de forma demostrada frente a ejecuciones concurrentes en la
  misma sesión.
- Entre sesiones o usuarios, la protección es esperada pero no demostrada; la
  seguridad descansa también en el puerto 3317 y en la propiedad de recursos.
- Una colisión de puerto puede terminar con 50 o con 40, según el momento.
- Cualquier cambio en el mutex (DACL explícita, mensaje diferenciado de acceso
  denegado o pruebas del flujo completo) requiere un BUILD autorizado aparte.

## Trazabilidad al código

- `tools/disposable_orchestrator_lib.ps1`: `$script:OrchestratorMutexName`,
  `Enter-OrchestratorLock`, `Exit-OrchestratorLock`, `Test-LoopbackPortFree`,
  `Invoke-EnvironmentStages` (comprobación del puerto, `compose up` y
  `Update-Ownership`), `Invoke-CleanupOnlyMode` e `Invoke-DisposableOrchestrator`
  (adquisición y liberación).
- `tools/tests/run_permissions_disposable.Tests.ps1`:
  - Bloque `AUD-014: mutex Global\ entre procesos`.
  - Pruebas "puerto ocupado: código 50 sin crear recursos" y "puerto ocupado:
    NO_RESOURCES_CREATED, nunca CLEANUP_COMPLETED".
  - Prueba "fallo parcial (solo red): borra solo la red propia y devuelve 40".
  - `Register-OrchestratorMocks` simula el mutex como libre en las pruebas del
    flujo.

## Brechas de implementación

- Validación de la exclusión entre sesiones y entre usuarios: **aplazada**.
- Prueba del flujo completo con el mutex ocupado: pendiente.
- Comprobación explícita de la rama del mutex abandonado: pendiente.
