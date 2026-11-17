"""Pruebas de la plantilla de inventario.

Propósito: leer el Excel que descarga la persona y validar sus encabezados
sin depender de la base de datos.
@author Cristian Deysdayr Jimenez
"""
from io import BytesIO
from zipfile import ZipFile

import pytest

from apps.accounts.errors import AppError
from apps.inventario.services.import_products import read_template
from apps.inventario.services.sheet import workbook
from apps.inventario.services.xlsx_reader import MAX_BYTES, read_rows

_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _sample() -> bytes:
    """Plantilla con dos filas y una columna extra al final."""
    return workbook(
        [
            "Producto",
            "Código",
            "Categoría",
            "Unidad",
            "Stock actual",
            "Equivalencia",
            "Stock mínimo",
            "Nota",
        ],
        [
            [
                "NPK 15-15-15",
                "7702000002",
                "Fertilizante/Abono",
                "Bulto (50 kg)",
                "3",
                "50 kg",
                "2",
                "x",
            ],
            ["Cal", "", "Insumo general/Varios", "Kilogramo (kg)", "12.5", "", "0", ""],
        ],
    )


def _sheet(sheet_data: str, shared: str | None = None) -> bytes:
    """Libro mínimo con una sola hoja y, si hace falta, textos compartidos."""
    sheet = (
        f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{_MAIN}">'
        f"<sheetData>{sheet_data}</sheetData></worksheet>"
    )
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
        if shared is not None:
            archive.writestr(
                "xl/sharedStrings.xml",
                f'<?xml version="1.0" encoding="UTF-8"?>'
                f'<sst xmlns="{_MAIN}">{shared}</sst>',
            )
    return buffer.getvalue()


def test_plantilla_limpia_solo_encabezados():
    """La descarga vacía deja solo las siete columnas a llenar."""
    from apps.inventario.services.import_products import COLUMNS

    rows = read_rows(workbook(list(COLUMNS), []))
    assert len(rows) == 1
    assert rows[0] == list(COLUMNS)


def test_lee_la_primera_hoja_y_conserva_el_orden():
    rows = read_rows(_sample())
    assert rows[0][0] == "Producto"
    assert rows[1][0] == "NPK 15-15-15"
    assert rows[1][4] == "3"
    assert rows[2][4] == "12.5"


def test_etiqueta_cada_columna_por_su_encabezado():
    rows = read_template(_sample())
    assert rows[0]["name"] == "NPK 15-15-15"
    assert rows[0]["barcode"] == "7702000002"
    assert rows[0]["quantity"] == "3"
    assert rows[0]["equivalent"] == "50 kg"
    assert rows[1]["min_stock"] == "0"


def test_acepta_las_columnas_en_otro_orden():
    payload = workbook(
        [
            "Stock mínimo",
            "Producto",
            "Unidad",
            "Código",
            "Categoría",
            "Equivalencia",
            "Stock actual",
        ],
        [["1", "Sal", "Kilogramo (kg)", "12", "Insumo general/Varios", "", "4"]],
    )
    row = read_template(payload)[0]
    assert row["name"] == "Sal"
    assert row["quantity"] == "4"
    assert row["min_stock"] == "1"


def test_avisa_si_falta_una_columna():
    payload = workbook(["Producto", "Código"], [["Sal", "12"]])
    with pytest.raises(AppError) as error:
        read_template(payload)
    assert "faltan columnas" in str(error.value)


def test_lee_textos_compartidos_y_numeros():
    payload = _sheet(
        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="C1" t="s"><v>1</v></c></row>'
        '<row r="2"><c r="A2" t="inlineStr"><is><t>NPK</t></is></c>'
        '<c r="B2"><v>7702000002</v></c><c r="C2" t="s"><v>2</v></c></row>',
        "<si><t>Producto</t></si><si><t>Unidad</t></si><si><t>Bulto (50 kg)</t></si>",
    )
    rows = read_rows(payload)
    assert rows[0] == ["Producto", "", "Unidad"]
    assert rows[1] == ["NPK", "7702000002", "Bulto (50 kg)"]


def test_rechaza_archivos_que_no_son_excel():
    with pytest.raises(AppError) as error:
        read_rows(b"no soy un zip")
    assert "Excel válido" in str(error.value)


def test_rechaza_archivos_demasiado_grandes():
    with pytest.raises(AppError) as error:
        read_rows(b"x" * (MAX_BYTES + 1))
    assert "2 MB" in str(error.value)
