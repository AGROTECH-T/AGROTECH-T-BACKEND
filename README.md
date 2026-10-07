<div align="center">

<img src="https://capsule-render.vercel.app/api?type=waving&color=0:050505,50:001B2E,100:00F7FF&height=240&section=header&text=AGROTECH-T&fontSize=64&fontColor=00F7FF&animation=fadeIn&fontAlignY=36&desc=SISTEMA%20INTELIGENTE%20DE%20GESTI%C3%93N%20AGROPECUARIA&descAlignY=59&descSize=16" width="100%"/>

<img src="https://readme-typing-svg.demolab.com?font=JetBrains+Mono&weight=700&size=20&duration=2200&pause=700&color=00F7FF&center=true&vCenter=true&width=900&lines=%3E+INICIANDO+SISTEMA...;%3E+AGROTECH-T+BACKEND;%3E+PYTHON+%2B+DJANGO+%2B+DRF;%3E+ARQUITECTURA+MODULAR+POR+DOMINIOS;%3E+MARIADB+%2F%2F+BASE+DE+DATOS+%C3%9ANICA;%3E+FRONTEND+%2F%2F+REPOSITORIO+SEPARADO;%3E+IoT+LISTO+%2F%2F+FUTURO;%3E+BASE+OPERATIVA+EN+L%C3%8DNEA+%E2%9C%93" alt="Animación de arranque del sistema"/>

<br/>

<img src="https://img.shields.io/badge/BACKEND-DJANGO_5.2-092E20?style=for-the-badge&logo=django&logoColor=white" alt="Django"/>
<img src="https://img.shields.io/badge/API-REST_DJANGO-FF1709?style=for-the-badge&logo=django&logoColor=white" alt="DRF"/>
<img src="https://img.shields.io/badge/BASE_DE_DATOS-MARIADB-003545?style=for-the-badge&logo=mariadb&logoColor=white" alt="MariaDB"/>
<img src="https://img.shields.io/badge/ARQUITECTURA-MODULAR-001B2E?style=for-the-badge&logo=uml&logoColor=00F7FF" alt="Modular"/>
<img src="https://img.shields.io/badge/ESTADO-ANDAMIAJE-yellow?style=for-the-badge&logo=statuspage&logoColor=black" alt="Andamiaje"/>

<br/><br/>

🛰️ [`SISTEMA`](#system_boot) · 🧬 [`ARQUITECTURA`](#architecture_matrix) · 🧩 [`MÓDULOS`](#domain_modules) · 🧪 [`TECNOLOGÍAS`](#technology_matrix) · 🚀 [`PROYECTO`](#project_database) · 🌿 [`GITFLOW`](#development_protocol) · 🗺️ [`RUTA`](#roadmap) · 📚 [`DOCS`](#documentation)

</div>

---

> [!NOTE]
> ## 🛰️ `// SYSTEM_BOOT` — AGROTECH-T BACKEND · ● ANDAMIAJE
> **Sistema:** Sistema Inteligente de Gestión Agropecuaria · **Repo:** `AGROTECH-T-BACKEND` (solo backend) · **Entorno:** Desarrollo · **Frontend:** repositorio separado (`AGROTECH-T-FRONTEND`)

<div align="center">

```text
AGROTECH-T BACKEND ......................... ● DESARROLLO
ARQUITECTURA ............................... MODULAR POR DOMINIOS
API ........................................ DJANGO REST FRAMEWORK
BASE DE DATOS .............................. MARIADB // BASE DE DATOS ÚNICA
FRONTEND ................................... REACT + TYPESCRIPT // REPOSITORIO SEPARADO
MÓDULOS DE DOMINIO ......................... 12 // SOLO ANDAMIAJE
LÓGICA DE NEGOCIO .......................... ○ AÚN NO IMPLEMENTADA
```

</div>

---

## 🟢 `// SYSTEM_STATUS`

<div align="center">

| MÓDULO | RUTA | ESTADO | NOTA |
|:-------|:-----|:------:|:-----|
| 👤 CUENTAS | `apps/accounts/` | 🟡 ANDAMIAJE | Dominio de autenticación · Cristian · sin implementar |
| 🏡 FINCAS | `apps/farms/` | 🟡 ANDAMIAJE | Dominio multi-finca · sin implementar |
| 🐔 AVICULTURA | `apps/production/avicultura/` | 🟡 ANDAMIAJE | 🔵 SIGUIENTE · primer módulo productivo |
| 🐟 PISCICULTURA | `apps/production/piscicultura/` | 🟡 ANDAMIAJE | Reservado |
| 🐖 PORCICULTURA | `apps/production/porcicultura/` | 🟡 ANDAMIAJE | Reservado |
| 🌱 AGRICULTURA | `apps/production/agricultura/` | 🟡 ANDAMIAJE | Reservado |
| 💧 HIDROPONIA | `apps/production/hidroponia/` | 🟡 ANDAMIAJE | Reservado |
| 📦 INVENTARIO | `apps/inventario/` | 🟡 ANDAMIAJE | Cristian · sin implementar |
| 📊 PANEL | `apps/dashboard/` | 🟡 ANDAMIAJE | Reservado |
| 🚨 ALERTAS | `apps/alertas/` | 🟡 ANDAMIAJE | Reservado |
| 🔎 TRAZABILIDAD | `apps/trazabilidad/` | 🟡 ANDAMIAJE | Reservado · QR en el futuro |
| 📡 IoT | `apps/iot/` | 🟡 ANDAMIAJE | Reservado · sensores en el futuro |

</div>

> [!IMPORTANT]
> 🟡 `ANDAMIAJE` = carpeta + `AppConfig` de Django, nada más. Sin modelos, sin endpoints, sin lógica de negocio.
> Solo existe `GET /api/health/` como verificación de infraestructura. Nada más está implementado.

---

## 🧬 `// ARCHITECTURE_MATRIX`

<div align="center">

```text
USUARIO
 ↓
REACT + TYPESCRIPT ............ AGROTECH-T-FRONTEND // REPOSITORIO SEPARADO
 ↓
API REST ...................... DJANGO REST FRAMEWORK
 ↓
DJANGO + DRF .................. AGROTECH-T-BACKEND // ESTE REPOSITORIO
 ↓
MÓDULOS DE DOMINIO ............ apps/ // 12 DOMINIOS DESACOPLADOS
 ↓
MARIADB ....................... BASE DE DATOS ÚNICA
```

</div>

> [!TIP]
> **Modular por dominios, listo para evolucionar hacia servicios independientes.**
> Sin microservicios en esta etapa. Sin monolito desordenado. Un solo proyecto Django,
> una sola base de datos, límites claros — para que cualquier dominio *pueda* extraerse
> como servicio independiente en el futuro sin reescribir el sistema.

<details>
<summary><b>🔌 REGLA DE DESACOPLAMIENTO — cómo deben comunicarse los dominios</b></summary>

- Sin dependencias circulares.
- Ningún módulo toca directamente las estructuras internas de otro.
- La comunicación entre dominios solo ocurre mediante interfaces/servicios definidos.

```text
❌ INCORRECTO:  avicultura → modifica directamente los modelos de inventario
✅ CORRECTO:    avicultura → interfaz/servicio definido → inventario
```

</details>

---

## 🧩 `// DOMAIN_MODULES`

<div align="center">

```text
apps/
├── 👤 accounts/
├── 🏡 farms/
│
├── 🌾 production/
│   ├── 🐔 avicultura/ ...... 🔵 SIGUIENTE
│   ├── 🐟 piscicultura/
│   ├── 🐖 porcicultura/
│   ├── 🌱 agricultura/
│   └── 💧 hidroponia/
│
├── 📦 inventario/
├── 📊 dashboard/
├── 🚨 alertas/
├── 🔎 trazabilidad/
└── 📡 iot/
```

</div>

> [!NOTE]
> `common/` contiene **únicamente** componentes transversales y realmente reutilizables —
> nunca lógica específica de un dominio. Está vacío a propósito: sin abstracciones prematuras.

---

## 🌾 `// PRODUCTION_MATRIX`

<div align="center">

| MÓDULO | RUTA | ESTADO |
|:-------|:-----|:-------|
| 🐔 AVICULTURA | `apps/production/avicultura/` | 🔵 SIGUIENTE · primer módulo productivo a desarrollar |
| 🐟 PISCICULTURA | `apps/production/piscicultura/` | ⚪ RESERVADO |
| 🐖 PORCICULTURA | `apps/production/porcicultura/` | ⚪ RESERVADO |
| 🌱 AGRICULTURA | `apps/production/agricultura/` | ⚪ RESERVADO |
| 💧 HIDROPONIA | `apps/production/hidroponia/` | ⚪ RESERVADO |

**🐔 AVICULTURA / GALPÓN es el primer módulo productivo programado — aún sin lógica de negocio definida ni implementada.**

</div>

---

## 🧪 `// TECHNOLOGY_MATRIX`

<div align="center">

### ⚙️ STACK BACKEND — ESTE REPOSITORIO
<img src="https://skillicons.dev/icons?i=python,django,git,github" alt="Stack backend"/>

### 🎨 STACK FRONTEND — REPOSITORIO SEPARADO `AGROTECH-T-FRONTEND`
<img src="https://skillicons.dev/icons?i=react,typescript" alt="Stack frontend"/>

<br/>

<img src="https://img.shields.io/badge/Python-3.13-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.13"/>
<img src="https://img.shields.io/badge/Django-5.2-092E20?style=for-the-badge&logo=django&logoColor=white" alt="Django 5.2"/>
<img src="https://img.shields.io/badge/DRF-API_REST-FF1709?style=for-the-badge&logo=django&logoColor=white" alt="DRF"/>
<img src="https://img.shields.io/badge/MariaDB-BD_%C3%9ANICA-003545?style=for-the-badge&logo=mariadb&logoColor=white" alt="MariaDB"/>
<img src="https://img.shields.io/badge/Git-GITFLOW-F05032?style=for-the-badge&logo=git&logoColor=white" alt="Git"/>
<img src="https://img.shields.io/badge/GitHub-AGROTECH_T-181717?style=for-the-badge&logo=github&logoColor=white" alt="GitHub"/>

</div>

---

## 🚜 `// PROJECT_DATABASE`

<div align="center">

### PROYECTO_01 — AGROTECH-T · 🟡 DESARROLLO

| CAMPO | VALOR |
|:------|:------|
| ESTADO | 🟡 DESARROLLO // solo andamiaje base |
| TIPO | PLATAFORMA AGROPECUARIA SaaS · multi-finca |
| ARQUITECTURA | ARQUITECTURA MODULAR POR DOMINIOS |
| BACKEND | DJANGO 5.2 + DJANGO REST FRAMEWORK |
| BASE DE DATOS | MARIADB // base de datos única |
| FRONTEND | REACT + TYPESCRIPT // repositorio independiente |
| SECTOR | AGRICULTURA + SOFTWARE + DATOS + IoT |
| IoT | 📡 INTEGRACIÓN FUTURA // sensores y dispositivos |

</div>

---

## 🌿 `// DEVELOPMENT_PROTOCOL` — GITFLOW

<div align="center">

```text
main ............................ ● ESTABLE
  │
  ▼
develop ....................... ● INTEGRACIÓN
  │
  ▼
feature/* ................... ● DESARROLLO AISLADO
```

| RAMA | ROL |
|:-----|:----|
| `main` | Solo código estable · sin desarrollo experimental |
| `develop` | Rama de integración |
| `feature/*` | Una rama aislada por funcionalidad |

</div>

> [!IMPORTANT]
> **Reglas del protocolo**
> - No desarrollar directamente en `main`
> - No `push --force` · No `reset --hard` · No `git clean` · No eliminar ramas
> - Revisar los cambios antes de cada commit · Probar antes de integrar · Commits claros

---

## ⚙️ `// CURRENT_BUILD` — ESTADO REAL

<div align="center">

| COMPONENTE | ESTADO |
|:-----------|:-------|
| 🟢 INFRAESTRUCTURA BASE | ✅ HECHO |
| 🟢 ESTRUCTURA MODULAR · 12 dominios | ✅ HECHO |
| 🟢 CONFIGURACIÓN DJANGO · MariaDB único | ✅ HECHO |
| 🟢 CI INICIAL · sintaxis + protección de secretos | ✅ HECHO |
| 🟢 ESTRUCTURA DE DOCUMENTACIÓN | ✅ HECHO |
| 🟡 LÓGICA DE NEGOCIO | ⬜ SIN INICIAR |
| 🟡 INTEGRACIÓN DE AUTENTICACIÓN | ⬜ SIN INICIAR |
| 🟡 GESTIÓN DE FINCAS | ⬜ SIN INICIAR |
| 🟡 MÓDULOS PRODUCTIVOS | ⬜ SIN INICIAR |

</div>

---

## 👥 `// TEAM_PROTOCOL`

<div align="center">

| MIEMBRO | DOMINIOS |
|:--------|:---------|
| **CRISTIAN** | 👤 CUENTAS / AUTENTICACIÓN · 📦 INVENTARIO |
| **SAMIR** | 🌾 PRODUCCIÓN · 🐔 AVICULTURA |
| **FÉNIX** | 🌿 CONOCIMIENTO DEL SECTOR AGROPECUARIO |

</div>

---

## 🗺️ `// ROADMAP`

<div align="center">

| ESTADO | HITO |
|:------:|:-----|
| ✅ | ARQUITECTURA BASE · andamiaje modular |
| 🔄 | AUTENTICACIÓN · dominio de cuentas |
| 🔄 | MULTI-FINCA · dominio de fincas |
| 🔄 | AVICULTURA · primer módulo productivo |
| ⬜ | PISCICULTURA |
| ⬜ | PORCICULTURA |
| ⬜ | AGRICULTURA |
| ⬜ | HIDROPONIA |
| ⬜ | INVENTARIO |
| ⬜ | PANEL |
| ⬜ | ALERTAS |
| ⬜ | TRAZABILIDAD · QR |
| ⬜ | IoT · sensores y dispositivos |
| ⬜ | IA · visión |

> ✅ hecho · 🔄 siguiente en cola (sin iniciar) · ⬜ planificado/reservado

</div>

---

## 🔌 `// API_LAYER`

<div align="center">

```text
FRONTEND ...................... AGROTECH-T-FRONTEND
   │
   ▼
API REST ...................... JSON SOBRE HTTP
   │
   ▼
DJANGO REST FRAMEWORK ......... AGROTECH-T-BACKEND
   │
   ▼
SERVICIOS DE DOMINIO .......... interfaces desacopladas // POR DEFINIR
   │
   ▼
MARIADB ....................... BASE DE DATOS ÚNICA
```

**Endpoint activo (verificación de infraestructura):** `GET /api/health/` → `{"status": "ok"}`
> [!NOTE]
> Aún no existe ningún otro endpoint. En este documento no se inventa ninguno.

</div>

---

## 📚 `// DOCUMENTATION`

<div align="center">

| DOCS | RUTA |
|:-----|:-----|
| 🧬 Arquitectura | [`docs/architecture/`](docs/architecture/architecture.md) |
| 📝 Decisiones (ADR) | [`docs/decisions/`](docs/decisions/) |
| 🧩 Módulos | [`docs/modules/`](docs/modules/) |
| 🔌 Contratos API | [`docs/api/`](docs/api/) |

</div>

---

## 💻 `// LOCAL_BOOT`

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python manage.py check
```

> [!TIP]
> **Entorno:** Python 3.13 · Django 5.2 · MariaDB (base de datos única).
> Copia `.env.example` a `.env` y completa los valores locales — `.env` está ignorado por Git y jamás debe commitearse. Sin Docker obligatorio en esta etapa.

---

<div align="center">

<img src="https://readme-typing-svg.demolab.com?font=JetBrains+Mono&weight=700&size=18&duration=2500&pause=1000&color=00F7FF&center=true&vCenter=true&width=700&lines=%3E+AGRICULTURA.;%3E+SOFTWARE.;%3E+DATOS.;%3E+IoT.;%3E+AGROTECH-T." alt="Identidad"/>

**🌾 AGROTECH-T // SISTEMA INTELIGENTE DE GESTIÓN AGROPECUARIA**
<br/>
`AGRICULTURA · SOFTWARE · DATOS · IoT`

<br/>

<img src="https://capsule-render.vercel.app/api?type=waving&color=0:00F7FF,50:001B2E,100:050505&height=140&section=footer" width="100%"/>

</div>
