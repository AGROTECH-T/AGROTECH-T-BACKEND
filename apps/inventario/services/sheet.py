"""Libro de Excel sencillo, sin dependencias extra.

Propósito: armar .xlsx de una o varias hojas con texto plano.
@author Cristian Deysdayr Jimenez
"""
# ruff: noqa: E501
import zipfile
from io import BytesIO


def workbook(headers: list[str], rows: list[list[str]]) -> bytes:
    """Arma un .xlsx de una hoja con texto plano."""
    return book([("Historial", headers, rows)])


def book(sheets: list[tuple[str, list[str], list[list[str]]]]) -> bytes:
    """Arma un .xlsx con varias hojas.

    Cada hoja es una tupla: nombre, encabezados y filas.
    """
    package = BytesIO()
    with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _types(len(sheets)))
        archive.writestr("_rels/.rels", _ROOT_RELS)
        archive.writestr("xl/workbook.xml", _workbook([name for name, _, _ in sheets]))
        archive.writestr("xl/_rels/workbook.xml.rels", _workbook_rels(len(sheets)))
        for index, (_, headers, rows) in enumerate(sheets, start=1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", _sheet(headers, rows))
    return package.getvalue()


def _sheet(headers: list[str], rows: list[list[str]]) -> str:
    """XML de la hoja."""
    body = [_row(1, headers)]
    for index, row in enumerate(rows, start=2):
        body.append(_row(index, row))
    last = _column(len(headers)) + str(max(len(rows) + 1, 1))
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="A1:{last}"/>'
        "<sheetData>"
        + "".join(body)
        + "</sheetData></worksheet>"
    )


def _row(index: int, values: list[str]) -> str:
    """Una fila de celdas de texto."""
    cells = []
    for column, value in enumerate(values, start=1):
        ref = f"{_column(column)}{index}"
        cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{_text(value)}</t></is></c>')
    return f'<row r="{index}">{"".join(cells)}</row>'


def _column(number: int) -> str:
    """1 -> A."""
    name = ""
    while number:
        number, rest = divmod(number - 1, 26)
        name = chr(65 + rest) + name
    return name


def _text(value) -> str:
    """Escapa el texto para que el XML sea válido."""
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _types(count: int) -> str:
    """Tipos de contenido del paquete."""
    sheets = "".join(
        f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index in range(1, count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        f"{sheets}"
        "</Types>"
    )


def _workbook(names: list[str]) -> str:
    """Índice del libro con el nombre de cada hoja."""
    sheets = "".join(
        f'<sheet name="{_text(name)}" sheetId="{index}" r:id="rId{index}"/>'
        for index, name in enumerate(names, start=1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{sheets}</sheets></workbook>"
    )


def _workbook_rels(count: int) -> str:
    """Relaciones entre el libro y sus hojas."""
    links = "".join(
        f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{links}</Relationships>"
    )


_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>
"""
