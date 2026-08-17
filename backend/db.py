"""
Capa de acceso a SQLite (usuarios.db): cuentas de la app, auditoría de
acciones de administración, y el vínculo chat_id de Telegram → cuenta. Sin
dependencia de Flask - las rutas pasan explícitamente lo que necesitan
(ej. el actor de una auditoría) en vez de que este módulo lea `g` por su
cuenta.
"""

import sqlite3
from datetime import datetime

from werkzeug.security import generate_password_hash

from . import config

DB_PATH = config.DB_PATH


def _inicializar_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            tarjeta TEXT NOT NULL,
            es_admin INTEGER NOT NULL DEFAULT 0
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            actor TEXT NOT NULL,
            accion TEXT NOT NULL,
            detalle TEXT
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS telegram_links (
            chat_id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            linked_at TEXT NOT NULL
        )
    """)
    con.commit()
    con.close()


def registrar_auditoria(actor, accion, detalle=""):
    """
    Deja rastro de una acción de administración (crear/eliminar
    usuario, resetear contraseña). Igual que usuarios.db, se pierde en
    cada redeploy de Render free (disco efímero) - sirve para auditar
    entre deploys, no como registro permanente.
    """
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO auditoria (ts, actor, accion, detalle) VALUES (?, ?, ?, ?)",
        (datetime.utcnow().isoformat(timespec="seconds") + "Z", actor, accion, detalle),
    )
    con.commit()
    con.close()


def listar_auditoria(limite=50):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    filas = con.execute(
        "SELECT ts, actor, accion, detalle FROM auditoria ORDER BY id DESC LIMIT ?", (limite,)
    ).fetchall()
    con.close()
    return [dict(f) for f in filas]


def obtener_usuario(username):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    fila = con.execute(
        "SELECT username, password_hash, tarjeta, es_admin FROM usuarios WHERE username = ?",
        (username,),
    ).fetchone()
    con.close()
    return dict(fila) if fila else None


def listar_usuarios():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    filas = con.execute("SELECT username, tarjeta, es_admin FROM usuarios ORDER BY username").fetchall()
    con.close()
    return [dict(f) for f in filas]


def crear_usuario(username, password_hash, tarjeta, es_admin):
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO usuarios (username, password_hash, tarjeta, es_admin) VALUES (?, ?, ?, ?)",
        (username, password_hash, tarjeta, int(es_admin)),
    )
    con.commit()
    con.close()


def eliminar_usuario(username):
    con = sqlite3.connect(DB_PATH)
    cur = con.execute("DELETE FROM usuarios WHERE username = ?", (username,))
    con.commit()
    con.close()
    return cur.rowcount > 0


def actualizar_password(username, nuevo_hash):
    con = sqlite3.connect(DB_PATH)
    con.execute("UPDATE usuarios SET password_hash = ? WHERE username = ?", (nuevo_hash, username))
    con.commit()
    con.close()


def vincular_telegram(chat_id, username):
    """Asocia un chat_id de Telegram a una cuenta de la app (ver /vincular
    en el bot). Un chat_id solo puede estar vinculado a un username a la vez
    - re-vincular pisa el vínculo anterior."""
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO telegram_links (chat_id, username, linked_at) VALUES (?, ?, ?) "
        "ON CONFLICT(chat_id) DO UPDATE SET username = excluded.username, linked_at = excluded.linked_at",
        (chat_id, username, datetime.utcnow().isoformat(timespec="seconds") + "Z"),
    )
    con.commit()
    con.close()


def desvincular_telegram(chat_id):
    con = sqlite3.connect(DB_PATH)
    cur = con.execute("DELETE FROM telegram_links WHERE chat_id = ?", (chat_id,))
    con.commit()
    con.close()
    return cur.rowcount > 0


def usuario_vinculado(chat_id):
    """Devuelve el usuario (con su tarjeta) vinculado a este chat_id de
    Telegram, o None si el chat todavía no hizo /vincular."""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    fila = con.execute("SELECT username FROM telegram_links WHERE chat_id = ?", (chat_id,)).fetchone()
    con.close()
    if not fila:
        return None
    return obtener_usuario(fila["username"])


def _bootstrap_admin():
    """
    Crea un admin desde variables de entorno si todavía no existe (y no
    toca nada si ya existe). Pensado para plataformas sin acceso a
    Shell en el plan gratuito (ej. Render): en vez de correr
    scripts/crear_usuario.py a mano, el propio backend se auto-crea el
    primer admin al arrancar. Opcional: si no están las tres variables, no
    hace nada.
    """
    admin_user = config.BOOTSTRAP_ADMIN_USERNAME
    admin_pass = config.BOOTSTRAP_ADMIN_PASSWORD
    admin_tarjeta = config.BOOTSTRAP_ADMIN_TARJETA
    if not (admin_user and admin_pass and admin_tarjeta):
        return
    if obtener_usuario(admin_user):
        return

    crear_usuario(admin_user, generate_password_hash(admin_pass), admin_tarjeta, es_admin=True)
