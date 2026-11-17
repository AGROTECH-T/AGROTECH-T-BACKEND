"""Lectura de libros .xlsx sin dependencias extra.

Propósito: tomar la primera hoja de la plantilla de inventario.
Contexto: el archivo se lee en memoria y nunca se escribe en disco; solo se
admiten las hojas que trae la plantilla.
@author Cristian Deysdayr Jimenez
"""
import re
import zipfile
from decimal import Decimal, InvalidOperation
from io import BytesIO
from xml.etree.ElementTree import Element

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from apps.accounts.errors import AppError

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 1000

_CELL = re.compile(r"^([A-Za-z]+)")
_FIRST_SHEET = "xl/worksheets/sheet1.xml"
_SHARED = "xl/sharedStrings.xml"
_WORKBOOK = "xl/workbook.xml"
_WORKBOOK_RELS = "xl/_rels/workbook.xml.rels"


def read_rows(payload: bytes) -> list[list[str]]:
    """Devuelve las filas de la primera hoja, sin las vacías del final."""
    if not payload:
        raise AppError("el archivo está vacío", 400)
    if len(payload) > MAX_BYTES:
        raise AppError("el archivo supera los 2 MB", 400)
    try:
        with zipfile.ZipFile(BytesIO(payload)) as book:
            names = set(book.namelist())
            shared = _shared_strings(book, names)
            rows = _sheet_rows(book.read(_sheet_path(book, names)), shared)
    except zipfile.BadZipFile as exc:
        raise AppError("el archivo no es un Excel válido", 400) from exc
    except KeyError as exc:
        raise AppError("el Excel no trae la hoja de la plantilla", 400) from exc
    except ElementTree.ParseError as exc:
        raise AppError("el contenido del Excel no se pudo leer", 400) from exc
    except DefusedXmlException as exc:
        raise AppError("el Excel trae contenido no permitido", 400) from exc
    trimmed = _trim(rows)
    if len(trimmed) > MAX_ROWS + 1:
        raise AppError(f"la plantilla admite hasta {MAX_ROWS} productos", 400)
    return trimmed


def _sheet_path(book: zipfile.ZipFile, names: set[str]) -> str:
    """Ruta de la primera hoja del libro."""
    if _WORKBOOK in names:
        sheet_id = _first_sheet_id(book.read(_WORKBOOK))
        if sheet_id and _WORKBOOK_RELS in names:
            target = _relationship(book.read(_WORKBOOK_RELS), sheet_id)
            if target:
                path = f"xl/{target.lstrip('/')}"
                if path in names:
                    return path
    if _FIRST_SHEET in names:
        return _FIRST_SHEET
    raise AppError("el Excel no trae hojas", 400)


def _first_sheet_id(payload: bytes) -> str:
    """Identificador de relación de la primera hoja."""
    root = ElementTree.fromstring(payload)
    for node in root.iter():
        if _local(node.tag) == "sheet":
            for key, value in node.attrib.items():
                if _local(key) == "id":
                    return value
    return ""


def _relationship(payload: bytes, sheet_id: str) -> str:
    """Destino de la hoja dentro de las relaciones del libro."""
    root = ElementTree.fromstring(payload)
    for node in root.iter():
        if _local(node.tag) == "Relationship" and node.get("Id") == sheet_id:
            return node.get("Target", "")
    return ""


def _shared_strings(book: zipfile.ZipFile, names: set[str]) -> list[str]:
    """Textos compartidos del libro, en el orden de sus índices."""
    if _SHARED not in names:
        return []
    root = ElementTree.fromstring(book.read(_SHARED))
    values = []
    for item in root:
        if _local(item.tag) != "si":
            continue
        values.append(_texts(item))
    return values


def _sheet_rows(payload: bytes, shared: list[str]) -> list[list[str]]:
    """Convierte las filas de la hoja en listas de texto."""
    root = ElementTree.fromstring(payload)
    rows = []
    for node in root.iter():
        if _local(node.tag) != "row":
            continue
        cells = {}
        for cell in node:
            if _local(cell.tag) != "c":
                continue
            index = _column_index(cell.get("r", ""))
            if index is None:
                continue
            cells[index] = _cell_value(cell, shared)
        width = max(cells) + 1 if cells else 0
        rows.append([cells.get(index, "") for index in range(width)])
    return rows


def _cell_value(cell: Element, shared: list[str]) -> str:
    """Texto de una celda, según el tipo que guarda el Excel."""
    kind = cell.get("t", "n")
    raw = ""
    for child in cell:
        name = _local(child.tag)
        if name == "v":
            raw = child.text or ""
        elif name == "is":
            raw = _texts(child)
    if kind == "s":
        try:
            return shared[int(raw)]
        except (ValueError, IndexError):
            return ""
    if kind == "b":
        return "Sí" if raw == "1" else "No"
    if kind in ("inlineStr", "str"):
        return raw
    return _number_text(raw)


def _texts(node: Element) -> str:
    """Une los textos de un nodo, incluidos los tramos con formato."""
    return "".join(
        child.text or "" for child in node.iter() if _local(child.tag) == "t"
    )


def _number_text(raw: str) -> str:
    """Número sin notación científica ni ceros de más."""
    text = raw.strip()
    if not text:
        return ""
    try:
        value = Decimal(text)
    except InvalidOperation:
        return text
    return format(value.normalize(), "f")


def _column_index(ref: str) -> int | None:
    """A1 -> 0; vacío -> None."""
    match = _CELL.match(ref)
    if match is None:
        return None
    index = 0
    for letter in match.group(1).upper():
        index = index * 26 + ord(letter) - 64
    return index - 1


def _trim(rows: list[list[str]]) -> list[list[str]]:
    """Quita las filas vacías del final que agrega Excel."""
    while rows and not any(cell.strip() for cell in rows[-1]):
        rows.pop()
    return rows


def _local(tag: str) -> str:
    """Nombre de la etiqueta sin su espacio de nombres."""
    return tag.rsplit("}", 1)[-1]
