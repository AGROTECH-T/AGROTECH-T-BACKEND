# DEC-12 — Fijación por digest de la imagen Redis del orquestador

- **Identificador:** DEC-12
- **Título:** Fijación por digest de la imagen Redis del orquestador desechable
- **Estado de decisión:** Aprobada
- **Estado de implementación:** Implementada
- **Fecha:** 2026-11-12

## Contexto

F10.3.4-A añadió a `tools/run_permissions_disposable.ps1` el modificador
`-WithRedis` (solo con `-Validate`). El Redis desechable usa la imagen oficial
`redis:7.4.12`, fijada por digest con `-RedisImageDigest`, y Compose se ejecuta
con `--pull never`. Igual que con MariaDB ([DEC-08](DEC-08-fijacion-digest-mariadb-orquestador.md)),
el valor es el digest del **índice OCI multiplataforma**, no el de una
plataforma concreta. En F10.3.4-B el usuario autorizó verificarlo, descargar la
imagen y registrar el digest.

## Decisión

Valor aprobado para `-RedisImageDigest`:

```
sha256:2e3eb11462c4e3759e6972678dd051d150ff54b1e60714b3c33b701260172a90
```

Referencia efectiva: `redis:7.4.12@sha256:2e3eb11462c4e3759e6972678dd051d150ff54b1e60714b3c33b701260172a90`.

## Evidencia de verificación (2026-11-12T15:40:15Z)

Se hicieron consultas HTTP de solo lectura a fuentes oficiales y las tres
coinciden:

| Fuente | Resultado |
|---|---|
| API de Docker Hub `https://hub.docker.com/v2/repositories/library/redis/tags/7.4.12` | `name` 7.4.12; `digest` `sha256:2e3eb114…a90`; `media_type` `application/vnd.oci.image.index.v1+json`; `last_updated` 2026-11-05T12:05:13Z |
| Registro `https://registry-1.docker.io/v2/library/redis/manifests/7.4.12` (token anónimo de `auth.docker.io`, `Accept` de índice OCI y de lista de manifiestos) | Cabecera `Docker-Content-Digest`: `sha256:2e3eb114…a90`; `Content-Type`: `application/vnd.oci.image.index.v1+json` |
| SHA-256 calculado localmente sobre el cuerpo del índice devuelto por el registro | `sha256:2e3eb114…a90` |

- **Repositorio:** `library/redis`, la imagen oficial de Docker Hub.
- **Etiqueta:** `7.4.12`.

Plataformas del índice (digest de cada manifiesto de plataforma):

| Plataforma | Digest |
|---|---|
| linux/amd64 | `sha256:a6dd428750118c2c2c67d3f555faef0aa15a2b71198b40e1cb3b9229b1cf3094` |
| linux/arm/v7 | `sha256:4eb03b951e8b9c49a919b33f4df3a58924b8656518b5b14e9611c065a282bfe8` |
| linux/arm64/v8 | `sha256:dbd05155efb852010d576fc1f09b6ade858944f84d333bb7a2efb1c1c8074a22` |
| linux/386 | `sha256:ec1ba654784216123f7e9611c7434d8d595c767ce1adeb4bc41ec89f06ab4dee` |
| linux/ppc64le | `sha256:94f3f5c15d324c9003430843219a6f3c2b7015ca838f5fb3334d9c0bfc78f184` |

El índice contiene además 5 manifiestos `unknown/unknown` de tipo
`attestation-manifest`.

## Descarga y comprobación local

- `docker pull redis:7.4.12` informó `Digest: sha256:2e3eb114…a90`.
- `docker image ls --no-trunc --digests --filter reference=redis:7.4.12` mostró
  `redis|7.4.12|sha256:2e3eb114…a90`, la línea exacta que exige
  `Test-PinnedRedisImageAvailable`.
- `RepoDigests` de la imagen local: `redis@sha256:2e3eb114…a90`; plataforma
  linux/amd64.
- En las ejecuciones reales de F10.3.4-B, la etiqueta `com.docker.compose.image`
  del contenedor fue `sha256:a6dd4287…3094`, el manifiesto linux/amd64 del
  índice.
- No se descargó ninguna otra imagen.

## Consecuencias

- Un valor distinto, o el digest de una plataforma concreta, hace que el
  orquestador aborte con 10 sin crear recursos.
- Si la etiqueta `7.4.12` se vuelve a publicar con otro índice, la imagen local
  conserva el digest aprobado. Una imagen nueva requiere repetir esta
  verificación y actualizar esta decisión.

## Trazabilidad

- `tools/disposable_orchestrator_lib.ps1`: `$script:RedisImageRepository`,
  `$script:RedisImageTag`, `Test-PinnedRedisImageAvailable` y
  `Test-RedisRenderedService`.
- `docker-compose.disposable.redis.yml`.
- [orquestador-desechable-redis.md](../modules/orquestador-desechable-redis.md).

## Brechas de implementación

Ninguna para la fijación. La validación funcional está en el documento del
módulo.
