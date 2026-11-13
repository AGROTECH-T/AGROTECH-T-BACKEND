# DEC-08 — Fijación por digest de la imagen MariaDB del orquestador desechable

- **Identificador:** DEC-08
- **Título:** Fijación por digest de la imagen MariaDB del orquestador desechable
- **Estado de decisión:** Aprobada
- **Estado de implementación:** Implementada
- **Fecha:** 2026-11-11

## Contexto

El orquestador desechable de validación (`tools/run_permissions_disposable.ps1`)
crea un contenedor MariaDB 11.8.8 propio de cada invocación. Una etiqueta como
`mariadb:11.8.8` es mutable: puede volver a publicarse y apuntar a otro
contenido. El riesgo R-ORQ-16 exige que la imagen sea inmutable y que nunca se
descargue durante una ejecución. Por eso el orquestador exige
`-MariaDbImageDigest`, fija la imagen por digest en el override que genera,
ejecuta `compose up --pull never` y aborta (código 10) si la imagen no está ya
en local con ese digest.

Este registro deja constancia del digest aprobado y de qué digest debe
suministrar el operador.

## Decisión

- **Imagen:** `mariadb:11.8.8`.
- **Digest del índice OCI (valor que se utiliza):**
  `sha256:24e76fcec8c003a0362d0dd53f4806e7e79458d7fdeaf47437760e19496f5a9c`.
- **Referencia completa de la imagen:**
  `mariadb:11.8.8@sha256:24e76fcec8c003a0362d0dd53f4806e7e79458d7fdeaf47437760e19496f5a9c`.
- **Forma de proporcionar el parámetro:**

  ```powershell
  -MariaDbImageDigest sha256:24e76fcec8c003a0362d0dd53f4806e7e79458d7fdeaf47437760e19496f5a9c
  ```

- **Digest linux/amd64 (solo trazabilidad; no se pasa como parámetro):**
  `sha256:e564bcaefb87c3d2b8b09b539ea791d34f4b62c05e2e7cb03f5d6388bdc54f2c`.

### Diferencia entre digest de índice y digest de plataforma

- El **digest del índice OCI** identifica el manifiesto
  `application/vnd.oci.image.index.v1+json` que publica la etiqueta. El índice
  agrupa las variantes linux/amd64, linux/arm64/v8, linux/ppc64le y
  linux/s390x, más sus manifiestos de atestación.
- El **digest de plataforma** identifica uno solo de esos manifiestos
  (`application/vnd.oci.image.manifest.v1+json`), aquí el de linux/amd64. Docker
  lo resuelve a partir del índice según la arquitectura de la máquina.

### Justificación de usar el índice OCI

- Es el digest que publica la etiqueta oficial (`digest` de la etiqueta en
  Docker Hub y cabecera `Docker-Content-Digest` del registro).
- Es el valor que compara el orquestador: `Test-PinnedImageAvailable` lo coteja
  con la columna Digest de `docker image ls --digests`.
- No depende de la arquitectura y mantiene la inmutabilidad: el índice fija por
  hash el manifiesto de cada plataforma.
- Si se suministra el digest de plataforma, la comprobación de imagen local no
  coincide y la ejecución se aborta con 10, sin descargar nada (comportamiento
  observado con el almacén de imágenes containerd de Docker Desktop).

## Fuentes y evidencia de verificación

Verificación del 2026-11-11 mediante consultas HTTP de solo lectura de
metadatos, sin descargar capas ni ejecutar contenedores:

- **Docker Hub (oficial):**
  `https://hub.docker.com/v2/repositories/library/mariadb/tags/11.8.8`. Devuelve
  la etiqueta `11.8.8` con `digest`
  `sha256:24e76fcec8c003a0362d0dd53f4806e7e79458d7fdeaf47437760e19496f5a9c`,
  `media_type` `application/vnd.oci.image.index.v1+json` y `tag_last_pushed`
  2026-08-19T23:35:55Z, y la variante linux/amd64 con `digest`
  `sha256:e564bcaefb87c3d2b8b09b539ea791d34f4b62c05e2e7cb03f5d6388bdc54f2c`.
- **Registro oficial (`registry-1.docker.io`):**
  - `/v2/library/mariadb/manifests/11.8.8`: cabecera `Docker-Content-Digest`
    igual al digest del índice. El SHA-256 recalculado del cuerpo del manifiesto
    coincide.
  - `/v2/library/mariadb/manifests/sha256:e564…54f2c`: cabecera
    `Docker-Content-Digest` igual al digest linux/amd64. El SHA-256 recalculado
    del cuerpo coincide. Anotaciones
    `org.opencontainers.image.version=11.8.8-noble` y
    `org.opencontainers.image.source=https://github.com/MariaDB/mariadb-docker.git#53935c78b82bff912b361357d59db11d7246ea96:11.8`.
- **Evidencia histórica (integración M3-2.4.4 del 2026-11-11, previa a este
  registro):** la imagen local `mariadb:11.8.8` presentaba el mismo digest de
  índice y la variante linux/amd64 `e564…`. Cinco ejecuciones reales del
  orquestador con este digest terminaron con código 0 y limpieza completa.
  Esa evidencia no se repitió al crear este documento.

## Consecuencias

- El digest no se escribe en el código ni en `docker-compose.disposable.yml`:
  lo aporta el operador con `-MariaDbImageDigest` y se fija en el override
  generado fuera del repositorio.
- Si la etiqueta `mariadb:11.8.8` se vuelve a publicar, su digest cambia. Antes
  de usar otro valor hay que repetir la verificación en las fuentes oficiales y
  actualizar este registro.
- La imagen debe estar ya disponible en local con este digest; el orquestador
  no la descarga.
- `docker-compose.yml` (desarrollo) y `.github/workflows/ci.yml` usan
  `mariadb:11.8.8` sin digest. Quedan fuera del alcance del orquestador y de
  esta decisión.

## Trazabilidad al código

- `tools/disposable_orchestrator_lib.ps1`:
  - Constantes `ImageRepository`, `ImageTag` e `ImageDigestPattern`
    (`^sha256:[0-9a-f]{64}$`).
  - `Test-OrchestratorOptions`: digest obligatorio fuera de `-CleanupOnly`,
    validación de formato y construcción de `mariadb:11.8.8@<digest>`.
  - `New-ComposeOverride`: escribe la imagen fijada en el override.
  - `Test-PinnedImageAvailable`: compara `mariadb|11.8.8|<digest>` con
    `docker image ls --no-trunc --digests`.
  - `Test-ComposeRenderedConfig`: exige que la imagen renderizada por Compose sea
    exactamente la fijada.
  - `Invoke-EnvironmentStages`: `compose up -d --no-build --no-recreate --pull never`.
- `tools/run_permissions_disposable.ps1`: parámetro `-MariaDbImageDigest` y su
  ayuda.
- Pruebas: `tools/tests/run_permissions_disposable.Tests.ps1`, bloque
  `R-ORQ-16: imagen fijada por digest y sin descargas`.

## Brechas de implementación

- **O-5 (diferida a una fase posterior):** `run-state.json` no registra la
  referencia exacta de imagen. Hoy consta en
  `<OutputDirectory>\compose\invocation.override.yml` y en las etiquetas de
  Compose del contenedor mientras existe. La mejora propuesta es añadir el campo
  al estado, fuera del texto protegido por el HMAC v1. Requiere autorización
  propia.
