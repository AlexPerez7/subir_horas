"""
CLI para crear o resetear usuarios de la app (tabla `usuarios` en
usuarios.db, junto a backend_odoo.py).

Uso:
    python crear_usuario.py <username> <tarjeta> [--admin]
    python crear_usuario.py <username> --reset-password

En Render (plan free, disco efímero): correr esto desde la pestaña
"Shell" del servicio ya desplegado, no en tu máquina - usuarios.db
vive en el disco del servicio, no en tu computadora. Hay que volver
a correrlo después de cada redeploy del backend.
"""

import argparse
import getpass
import sqlite3

from werkzeug.security import generate_password_hash

from backend_odoo import DB_PATH


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
    con = sqlite3.connect(DB_PATH)

    if args.reset_password:
        password = pedir_password()
        cur = con.execute(
            "UPDATE usuarios SET password_hash = ? WHERE username = ?",
            (generate_password_hash(password), username),
        )
        con.commit()
        if cur.rowcount:
            print(f"Contraseña actualizada para '{username}'.")
        else:
            print(f"No existe el usuario '{username}'.")
    else:
        if not args.tarjeta:
            parser.error("falta <tarjeta> para crear un usuario nuevo")
        password = pedir_password()
        try:
            con.execute(
                "INSERT INTO usuarios (username, password_hash, tarjeta, es_admin) VALUES (?, ?, ?, ?)",
                (username, generate_password_hash(password), args.tarjeta, int(args.admin)),
            )
            con.commit()
            print(f"Usuario '{username}' creado (tarjeta: '{args.tarjeta}', admin: {args.admin}).")
        except sqlite3.IntegrityError:
            print(f"Ya existe un usuario '{username}'. Usa --reset-password para cambiarle la contraseña.")

    con.close()


if __name__ == "__main__":
    main()
