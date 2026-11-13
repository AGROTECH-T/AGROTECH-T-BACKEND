"""Catálogo centralizado de permisos operativos.

Propósito: definir de forma única y tipada los permisos operativos del sistema,
sus techos por rol y las reglas de identificación.

Contexto: el catálogo vive en código (no en base de datos) porque los módulos y
acciones se agregan con el desarrollo, no en tiempo de ejecución. Las
asignaciones y restricciones se persistirán en etapas posteriores. Los permisos
estructurales y de autogestión no forman parte de este catálogo.
"""
import re
from dataclasses import dataclass

from apps.farms.models import FarmRole

# Categorías de permiso. Solo "operational" se administra por la matriz.
CATEGORY_OPERATIONAL = "operational"
CATEGORY_STRUCTURAL = "structural"
CATEGORY_SELF_SERVICE = "self_service"
_CATEGORIES = frozenset({CATEGORY_OPERATIONAL, CATEGORY_STRUCTURAL, CATEGORY_SELF_SERVICE})

# Identificadores: minúsculas y snake_case.
_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]*$")
MAX_IDENTIFIER_LENGTH = 64


def validate_identifier(value: str, field: str) -> str:
    """Valida que un identificador sea minúscula y snake_case."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} no puede estar vacío.")
    if len(value) > MAX_IDENTIFIER_LENGTH:
        raise ValueError(f"{field} excede {MAX_IDENTIFIER_LENGTH} caracteres.")
    if not _IDENTIFIER_RE.match(value):
        raise ValueError(
            f"{field} debe estar en minúsculas y snake_case: {value!r}."
        )
    return value


@dataclass(frozen=True)
class Permission:
    """Un permiso operativo del catálogo."""

    module: str
    action: str
    label: str
    allowed_roles: frozenset
    assignable: bool = True
    requires_policy: bool = False
    category: str = CATEGORY_OPERATIONAL

    def __post_init__(self) -> None:
        object.__setattr__(self, "module", validate_identifier(self.module, "module"))
        object.__setattr__(self, "action", validate_identifier(self.action, "action"))
        if self.category not in _CATEGORIES:
            raise ValueError(f"categoría inválida: {self.category!r}.")
        for role in self.allowed_roles:
            if role not in FarmRole.values:
                raise ValueError(f"rol inválido en {self.key}: {role!r}.")

    @property
    def key(self) -> str:
        """Identificador compuesto del permiso."""
        return f"{self.module}.{self.action}"


PERMISSION_CATALOG = (
    Permission(
        module="farms",
        action="consultar",
        label="Consultar fincas propias",
        allowed_roles=frozenset(
            {FarmRole.ADMINISTRADOR, FarmRole.OPERADOR, FarmRole.CONSULTA}
        ),
    ),
    Permission(
        module="inventario",
        action="consultar",
        label="Consultar inventario",
        allowed_roles=frozenset(
            {FarmRole.ADMINISTRADOR, FarmRole.OPERADOR, FarmRole.CONSULTA}
        ),
    ),
    Permission(
        module="inventario",
        action="crear",
        label="Crear insumos y registrar movimientos",
        allowed_roles=frozenset({FarmRole.ADMINISTRADOR, FarmRole.OPERADOR}),
    ),
)


def _validate_catalog_unique() -> None:
    """Rechaza duplicados de (module, action) en el catálogo."""
    seen = set()
    for perm in PERMISSION_CATALOG:
        if perm.key in seen:
            raise ValueError(f"permiso duplicado: {perm.key}.")
        seen.add(perm.key)


_validate_catalog_unique()


def role_ceiling(role: str) -> frozenset:
    """Retorna el conjunto de permisos operativos permitidos para un rol.

    No consulta la base de datos. Si el rol es desconocido, falla de forma
    controlada en lugar de devolver permisos por defecto.
    """
    if role not in FarmRole.values:
        raise ValueError(f"rol desconocido: {role!r}.")
    return frozenset(
        perm.key
        for perm in PERMISSION_CATALOG
        if perm.category == CATEGORY_OPERATIONAL and role in perm.allowed_roles
    )
