"""Préstamo y devolución de un producto.

Propósito: bajar el stock al prestar y subirlo al devolver, guardando a quién.
@author Cristian Deysdayr Jimenez
"""
import re
from decimal import Decimal

from django.db import transaction
from django.db.models import F, QuerySet

from apps.accounts.errors import AppError
from apps.accounts.models import User
from apps.inventario.models import MovementKind, Product, ProductLoan
from apps.inventario.services.membership import require_membership
from apps.inventario.services.movements import register_movement
from apps.inventario.services.permissions import READ, WRITE_STOCK, require_action

ZERO = Decimal("0")
DOCUMENT = re.compile(r"^\d{5,15}$")
LOAN_NOTE = re.compile(
    r"^(?P<kind>Préstamo|Devolución): (?P<name>.+) "
    r"\((?P<document>\d+)\)(?:\. (?P<observation>.*))?$"
)


def list_loans(*, user: User, farm_id: int, product_id: int) -> QuerySet[ProductLoan]:
    """Todos los préstamos del producto, pendientes y devueltos."""
    membership = require_membership(user=user, farm_id=farm_id)
    require_action(membership, READ)
    _product(farm_id, product_id)
    return ProductLoan.objects.filter(product_id=product_id)


def list_open_loans(*, user: User, farm_id: int, product_id: int) -> QuerySet[ProductLoan]:
    """Préstamos de ese producto que todavía no se devolvieron por completo."""
    return list_loans(
        user=user,
        farm_id=farm_id,
        product_id=product_id,
    ).filter(returned_quantity__lt=F("quantity"))


def lend_product(
    *,
    user: User,
    farm_id: int,
    product_id: int,
    borrower_name: str,
    document_number: str,
    quantity: Decimal,
) -> tuple[ProductLoan, Product]:
    """Registra el préstamo y descuenta la cantidad del stock."""
    name = _person(borrower_name, "escribe el nombre completo", 120)
    document = _document(document_number)
    with transaction.atomic():
        movement = register_movement(
            user=user,
            farm_id=farm_id,
            product_id=product_id,
            kind=MovementKind.SALIDA,
            quantity=quantity,
            note=f"Préstamo: {name} ({document})",
        )
        loan = ProductLoan.objects.create(
            product_id=product_id,
            borrower_name=name,
            document_number=document,
            quantity=movement.quantity,
            created_by=user,
        )
    return loan, movement.product


def return_loan(
    *,
    user: User,
    farm_id: int,
    product_id: int,
    loan_id: int,
    quantity: Decimal,
    note: str = "",
) -> tuple[ProductLoan, Product]:
    """Devuelve parte o todo lo prestado y suma esa cantidad al stock."""
    membership = require_membership(user=user, farm_id=farm_id)
    require_action(membership, WRITE_STOCK)
    with transaction.atomic():
        loan = (
            ProductLoan.objects.select_for_update()
            .filter(id=loan_id, product_id=product_id, product__farm_id=farm_id)
            .first()
        )
        if loan is None:
            raise AppError("préstamo no encontrado", 404)
        pending = loan.quantity - loan.returned_quantity
        if pending <= ZERO:
            raise AppError("ese préstamo ya se devolvió", 400)
        if quantity > pending:
            raise AppError(f"pendiente {pending}, no se puede devolver {quantity}", 400)
        movement = register_movement(
            user=user,
            farm_id=farm_id,
            product_id=product_id,
            kind=MovementKind.ENTRADA,
            quantity=quantity,
            note=_return_note(loan.borrower_name, loan.document_number, note),
        )
        loan.returned_quantity += movement.quantity
        loan.save(update_fields=["returned_quantity", "updated_at"])
    return loan, movement.product


def _return_note(name: str, document: str, extra: str) -> str:
    """Nota de la devolución, con la observación si la escribieron."""
    text = f"Devolución: {name} ({document})"
    clean = extra.strip()
    if clean:
        text = f"{text}. {clean}"
    return text[:200]


def read_loan_note(note: str) -> dict[str, str] | None:
    """Separa estado, persona, documento y observación de una nota de préstamo.

    Las notas de préstamo y devolución las escribe este módulo, así que
    siempre tienen el mismo formato. Si la nota no es de un préstamo,
    devuelve ``None`` para tratarla como una observación normal.
    """
    match = LOAN_NOTE.match(note.strip())
    if match is None:
        return None
    return {
        "state": "Prestado" if match["kind"] == "Préstamo" else "Devuelto",
        "name": match["name"],
        "document": match["document"],
        "observation": match["observation"] or "",
    }


def _product(farm_id: int, product_id: int) -> Product:
    """Busca el producto de esa finca."""
    product = Product.objects.filter(id=product_id, farm_id=farm_id, is_removed=False).first()
    if product is None:
        raise AppError("producto no encontrado", 404)
    return product


def _document(value: str) -> str:
    """Acepta solo el número de documento, entre 5 y 15 dígitos."""
    clean = "".join(value.split())
    if not DOCUMENT.fullmatch(clean):
        raise AppError("solo números. ejemplo: 1234567890", 400)
    return clean


def _person(value: str, message: str, limit: int) -> str:
    """Exige un texto corto."""
    clean = " ".join(value.split())
    if not clean:
        raise AppError(message, 400)
    if len(clean) > limit:
        raise AppError("el dato es demasiado largo", 400)
    return clean
