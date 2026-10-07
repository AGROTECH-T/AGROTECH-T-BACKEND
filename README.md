# AGROTECH-T-BACKEND

Backend del sistema SaaS agropecuario multi-finca **AGROTECH-T**.
Este repositorio contiene exclusivamente el backend. El frontend vive en el
repositorio independiente `AGROTECH-T-FRONTEND` y se comunica vía API REST.

## Arquitectura

Arquitectura modular por dominios, preparada para evolucionar hacia servicios
independientes. Sin microservicios en esta etapa, sin monolito desordenado:
un solo proyecto Django con límites claros entre módulos.

Ver `docs/architecture/architecture.md`.

## Tecnologías

- Python 3.13
- Django 5.2
- Django REST Framework
- MariaDB (única base de datos del proyecto)
- Git + GitHub con GitFlow (`main` estable, `develop` integración, `feature/*` desarrollo)

## Estructura general

```text
AGROTECH-T-BACKEND/
├── config/                  # Configuración general de Django
├── apps/
│   ├── accounts/            # Autenticación (responsable: Cristian)
│   ├── farms/               # Fincas, membresías, relación usuario-finca
│   ├── production/
│   │   ├── avicultura/      # Primer módulo productivo (fase posterior)
│   │   ├── piscicultura/
│   │   ├── porcicultura/
│   │   ├── agricultura/
│   │   └── hidroponia/
│   ├── inventario/          # Responsable: Cristian
│   ├── dashboard/
│   ├── alertas/
│   ├── trazabilidad/
│   └── iot/
├── common/                  # Solo transversal reutilizable (hoy vacío a propósito)
├── tests/
├── docs/
│   ├── architecture/
│   ├── decisions/
│   ├── modules/
│   └── api/
├── infrastructure/docker/   # Reservado (sin Docker complejo todavía)
├── .github/workflows/
├── manage.py
├── requirements.txt
├── .env.example
└── README.md
```

## Estado actual del proyecto

Infraestructura base y arquitectura inicial. Sin lógica funcional:

- Sin login / registro / OTP.
- Sin fincas, galpones, lotes, producción, inventario ni IoT.
- Solo esqueletos de apps Django, configuración mínima y documentación base.

## Regla de trabajo por módulos

- Cada dominio vive en su propio paquete bajo `apps/`.
- Sin dependencias circulares.
- Ningún módulo modifica estructuras internas de otro directamente;
  comunicación solo vía interfaces/servicios definidos.
- `common/` no es cajón de basura: solo código transversal reutilizable.
- Las decisiones de negocio agropecuario no definidas no se implementan:
  se detienen y se explican.

## Puesta en marcha local (base)

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python manage.py check
```
