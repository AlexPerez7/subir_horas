"""
Variables de entorno dummy para que `backend/config.py` no aborte
(sys.exit) al importarse durante los tests - los tests de
tests/test_horas.py solo ejercitan funciones puras (fechas,
validaciones), nunca hablan con Odoo ni Supabase de verdad, así que el
valor de estas variables no importa, solo que existan.

Este archivo vive en la raíz del repo (no dentro de tests/) para que
pytest agregue la raíz a sys.path y `from backend.horas import ...`
funcione sin instalar el paquete.
"""

import os

for _nombre, _valor in {
    "ODOO_URL": "https://test.local/jsonrpc",
    "ODOO_DB": "test-db",
    "ODOO_UID": "1",
    "ODOO_TOKEN": "test-token",
    "SECRET_KEY": "test-secret",
    "SUPABASE_URL": "https://test.supabase.co",
    "SUPABASE_SERVICE_ROLE_KEY": "test-key",
}.items():
    os.environ.setdefault(_nombre, _valor)
