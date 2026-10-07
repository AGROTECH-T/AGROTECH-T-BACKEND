# infrastructure/docker/

El arranque con contenedores vive en la raíz del repositorio porque el contexto de build es el proyecto completo:

- `Dockerfile`
- `docker-compose.yml`
- `.dockerignore`

`docker compose up --build` levanta MariaDB 11.8.8, Redis 8 y Gunicorn. El backend también puede correr contra una MariaDB local, sin Docker.
