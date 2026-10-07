"""Inicializa configuración, entorno y el driver de MariaDB.

Propósito: cargar .env antes que el resto de ajustes y registrar PyMySQL.
Contexto: las claves reales viven solo en el entorno.
@author Cristian Deysdayr Jimenez
"""
from pathlib import Path

import pymysql
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
pymysql.install_as_MySQLdb()
