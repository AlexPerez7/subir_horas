"""
Toda la configuración de la app leída de variables de entorno (.env local,
o el .env en la VM de producción) - un único lugar para saber
qué variables existen y cuáles son obligatorias, en vez de os.environ
disperso por los demás módulos.
"""

import os
import sys

from dotenv import load_dotenv

# Raíz del repo (padre de este paquete backend/) - NO la carpeta backend/
# misma. El .env vive ahí, junto a requirements.txt, no dentro del
# paquete.
_CARPETA = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

load_dotenv(os.path.join(_CARPETA, ".env"))


def _env_obligatoria(nombre):
    """Como os.environ[nombre], pero con un mensaje claro (qué variable
    falta y dónde revisar) en vez de un KeyError críptico en los logs de
    systemd (`journalctl -u subir-horas`) cuando falta configurar algo."""
    valor = os.environ.get(nombre)
    if not valor:
        sys.exit(
            f"Falta la variable de entorno obligatoria '{nombre}'. "
            f"Revisa tu .env local (o el .env de la VM) - ver .env.example."
        )
    return valor


ODOO_URL = _env_obligatoria("ODOO_URL")
ODOO_DB = _env_obligatoria("ODOO_DB")
ODOO_UID = int(_env_obligatoria("ODOO_UID"))
ODOO_TOKEN = _env_obligatoria("ODOO_TOKEN")
SECRET_KEY = _env_obligatoria("SECRET_KEY")

# Supabase, vía su API REST (no conexión directa a Postgres - ver
# backend/db.py para el porqué). Sacar ambos valores de Supabase:
# Project Settings → API. SUPABASE_SERVICE_ROLE_KEY (no la "anon") porque
# bypassea Row Level Security - el mismo nivel de acceso que ya tenía la
# conexión directa. Nunca sale del backend, así que es seguro.
SUPABASE_URL = _env_obligatoria("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = _env_obligatoria("SUPABASE_SERVICE_ROLE_KEY")

FRONTEND_ORIGINS = [o.strip() for o in os.environ.get("FRONTEND_ORIGINS", "").split(",") if o.strip()]
TOKEN_LIFETIME_SEGUNDOS = int(os.environ.get("SESSION_LIFETIME_HORAS", 8)) * 3600
CRON_SECRET = os.environ.get("CRON_SECRET", "")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")

# Ver _bootstrap_admin() en db.py: crea este admin al arrancar si todavía no
# existe (evita depender de loguearte a la VM solo para el primer login).
BOOTSTRAP_ADMIN_USERNAME = os.environ.get("BOOTSTRAP_ADMIN_USERNAME", "").strip().lower()
BOOTSTRAP_ADMIN_PASSWORD = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "")
BOOTSTRAP_ADMIN_TARJETA = os.environ.get("BOOTSTRAP_ADMIN_TARJETA", "").strip()

PROJECT_NAME = "GER_Produccion Varios NF"
