# AGROTECH-T — Arquitectura del backend

> Documento inicial de infraestructura base. No contiene decisiones de negocio
> agropecuario: esas se definirán en fases posteriores y se registrarán en
> `docs/decisions/`.

## 1. Arquitectura seleccionada

Arquitectura modular por dominios, preparada para evolucionar hacia servicios
independientes.

- Monolito modular Django (un solo despliegue, una sola base de datos).
- Organización por dominios funcionales en `apps/`.
- El subdominio productivo se agrupa en `apps/production/`:
  avicultura, piscicultura, porcicultura, agricultura, hidroponia.

No hay microservicios en esta etapa. No hay múltiples bases de datos.

## 2. Motivo de la elección

- Evitar la complejidad operativa de microservicios sin necesidad actual.
- Evitar un monolito desordenado sin límites entre módulos.
- Mantener velocidad de desarrollo con un equipo pequeño.
- Dejar abierta la evolución futura sin reescribir el sistema.

## 3. Separación por dominios

| Dominio | Ubicación | Responsabilidad prevista (sin implementar) |
|---|---|---|
| accounts | `apps/accounts/` | Autenticación implementada: registro, JWE, OTP y sesiones |
| farms | `apps/farms/` | Fincas, membresías, relación usuario-finca (transversal) |
| avicultura | `apps/production/avicultura/` | Primer módulo productivo (fase posterior: galpón) |
| piscicultura | `apps/production/piscicultura/` | Reservado |
| porcicultura | `apps/production/porcicultura/` | Reservado |
| agricultura | `apps/production/agricultura/` | Reservado |
| hidroponia | `apps/production/hidroponia/` | Reservado |
| inventario | `apps/inventario/` | Inventario (responsable: Cristian) |
| dashboard | `apps/dashboard/` | Indicadores y visualización agregada |
| alertas | `apps/alertas/` | Sistema de alertas |
| trazabilidad | `apps/trazabilidad/` | Trazabilidad (+ QR a futuro) |
| iot | `apps/iot/` | Sensores y dispositivos (futuro) |

Componentes transversales realmente reutilizables viven en `common/`.
`common/` no debe convertirse en cajón de basura ni contener lógica
específica de un dominio.

## 4. Relación entre backend y frontend

- Este repositorio (`AGROTECH-T-BACKEND`) es exclusivamente el backend.
- El frontend vive en un repositorio independiente (`AGROTECH-T-FRONTEND`).
- La comunicación entre ambos es vía API REST (Django REST Framework).
- Este repositorio nunca debe contener código del frontend.

## 5. Regla de desacoplamiento

- Los módulos deben mantenerse desacoplados. Sin dependencias circulares.
- Ningún módulo modifica directamente las estructuras internas de otro.
- La comunicación entre dominios se hace vía interfaces/servicios definidos.

Ejemplo:

- INCORRECTO: `avicultura` modifica directamente modelos internos de `inventario`.
- CORRECTO: `avicultura` → interfaz/servicio definido → `inventario`.

Esto vale aunque todo corra en el mismo proyecto Django hoy.

## 6. Evolución futura hacia servicios

La organización por dominios permite que, si algún día fuera necesario,
un dominio se extraiga como servicio independiente sin reescribir todo
el sistema. Esa decisión no está tomada: cuando se tome, se documentará
en `docs/decisions/` con su justificación.

## 7. Infraestructura actual

Un solo proyecto Django y una sola base MariaDB. Redis respalda límites de
tasa y caché. `docker compose` en la raíz es opcional (MariaDB, Redis y
Gunicorn). El arranque local contra MariaDB instalada sigue siendo válido.
