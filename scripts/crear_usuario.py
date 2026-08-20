"""
CLI para crear o resetear usuarios de la app (tabla `usuarios` en Postgres,
ver backend/db.py) - usa las mismas funciones que la API, no SQL propio.

Uso (desde la raíz del repo, con SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY
disponibles en tu .env o variables de entorno):
    python scripts/crear_usuario.py <username> <tarjeta> [--admin]
    python scripts/crear_usuario.py <username> --reset-password
"""

import argparse
import getpass
import os
import sys

from werkzeug.security import generate_password_hash

# El script vive en scripts/, pero el paquete backend/ está en la raíz del
# repo (un nivel arriba) - hay que agregarla a sys.path para poder importar.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend import db


def pedir_password():
    while True:
        p1 = getpass.getpass("Contraseña (mínimo 6 caracteres): ")
        if len(p1) < 6:
            print("Debe tener al menos 6 caracteres.")
            continue
        p2 = getpass.getpass("Confirma la contraseña: ")
        if p1 != p2:
            print("No coinciden, intenta de nuevo.")
            continue
        return p1


def main():
    parser = argparse.ArgumentParser(description="Crear o resetear usuarios de Registro de Horas")
    parser.add_argument("username")
    parser.add_argument("tarjeta", nargs="?", help="Nombre exacto de la tarjeta en Odoo (no aplica con --reset-password)")
    parser.add_argument("--admin", action="store_true", help="Marca al usuario como administrador")
    parser.add_argument("--reset-password", action="store_true", help="Solo resetea la contraseña de un usuario existente")
    args = parser.parse_args()

    username = args.username.strip().lower()

    if args.reset_password:
        password = pedir_password()
        if db.obtener_usuario(username):
            db.actualizar_password(username, generate_password_hash(password))
            print(f"Contraseña actualizada para '{username}'.")
        else:
            print(f"No existe el usuario '{username}'.")
    else:
        if not args.tarjeta:
            parser.error("falta <tarjeta> para crear un usuario nuevo")
        if db.obtener_usuario(username):
            print(f"Ya existe un usuario '{username}'. Usa --reset-password para cambiarle la contraseña.")
        else:
            password = pedir_password()
            db.crear_usuario(username, generate_password_hash(password), args.tarjeta, es_admin=args.admin)
            print(f"Usuario '{username}' creado (tarjeta: '{args.tarjeta}', admin: {args.admin}).")


if __name__ == "__main__":
    main()
