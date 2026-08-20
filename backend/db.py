"""
Capa de acceso a datos (Postgres en Supabase): cuentas de la app, auditoría
de acciones de administración, y el vínculo chat_id de Telegram → cuenta.
Sin dependencia de Flask - las rutas pasan explícitamente lo que necesitan
(ej. el actor de una auditoría) en vez de que este módulo lea `g` por su
cuenta.

A diferencia de una conexión SQLite a un archivo local (prácticamente
gratis), cada conexión a Supabase es una conexión TCP+TLS a un servidor
remoto - abrir una nueva por cada consulta sería lento. Se usa un pool
chico reusado durante toda la vida del proceso (alcanza con 1-5 conexiones:
el Procfile corre un único worker de gunicorn, sin threading, así que nunca
hay más de un request adentro de este módulo a la vez).
"""

from contextlib import contextmanager
from datetime import datetime

import psycopg2
import psycopg2.extras
import psycopg2.pool
from werkzeug.security import generate_password_hash

from . import config

_pool = psycopg2.pool.ThreadedConnectionPool(1, 5, dsn=config.DATABASE_URL)


@contextmanager
def _cursor(dict_rows=False):
    """Pide una conexión del pool, entrega un cursor, y al salir hace commit
    (o rollback si hubo una excepción) antes de devolver la conexión al
    pool - fundamental con conexiones reusadas: una que vuelve al pool en
    medio de una transacción rota deja fallando al próximo que la use."""
    con = _pool.getconn()
    try:
        cursor_factory = psycopg2.extras.RealDictCursor if dict_rows else None
        with con.cursor(cursor_factory=cursor_factory) as cur:
            yield cur
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        _pool.putconn(con)


def _inicializar_db():
    with _cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                tarjeta TEXT NOT NULL,
                es_admin INTEGER NOT NULL DEFAULT 0
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS auditoria (
                id SERIAL PRIMARY KEY,
                ts TEXT NOT NULL,
                actor TEXT NOT NULL,
                accion TEXT NOT NULL,
                detalle TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS telegram_links (
                chat_id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                linked_at TEXT NOT NULL
            )
        """)


def registrar_auditoria(actor, accion, detalle=""):
    """
    Deja rastro de una acción de administración (crear/eliminar
    usuario, resetear contraseña). Registro permanente (Postgres en
    Supabase) - a diferencia de la época de SQLite en el disco del
    backend, ya no se pierde al reiniciar el servicio.
    """
    with _cursor() as cur:
        cur.execute(
            "INSERT INTO auditoria (ts, actor, accion, detalle) VALUES (%s, %s, %s, %s)",
            (datetime.utcnow().isoformat(timespec="seconds") + "Z", actor, accion, detalle),
        )


def listar_auditoria(limite=50):
    with _cursor(dict_rows=True) as cur:
        cur.execute(
            "SELECT ts, actor, accion, detalle FROM auditoria ORDER BY id DESC LIMIT %s", (limite,)
        )
        filas = cur.fetchall()
    return [dict(f) for f in filas]


def obtener_usuario(username):
    with _cursor(dict_rows=True) as cur:
        cur.execute(
            "SELECT username, password_hash, tarjeta, es_admin FROM usuarios WHERE username = %s",
            (username,),
        )
        fila = cur.fetchone()
    return dict(fila) if fila else None


def listar_usuarios():
    with _cursor(dict_rows=True) as cur:
        cur.execute("SELECT username, tarjeta, es_admin FROM usuarios ORDER BY username")
        filas = cur.fetchall()
    return [dict(f) for f in filas]


def crear_usuario(username, password_hash, tarjeta, es_admin):
    with _cursor() as cur:
        cur.execute(
            "INSERT INTO usuarios (username, password_hash, tarjeta, es_admin) VALUES (%s, %s, %s, %s)",
            (username, password_hash, tarjeta, int(es_admin)),
        )


def eliminar_usuario(username):
    with _cursor() as cur:
        cur.execute("DELETE FROM usuarios WHERE username = %s", (username,))
        return cur.rowcount > 0


def actualizar_password(username, nuevo_hash):
    with _cursor() as cur:
        cur.execute("UPDATE usuarios SET password_hash = %s WHERE username = %s", (nuevo_hash, username))


def vincular_telegram(chat_id, username):
    """Asocia un chat_id de Telegram a una cuenta de la app (ver /vincular
    en el bot). Un chat_id solo puede estar vinculado a un username a la vez
    - re-vincular pisa el vínculo anterior."""
    with _cursor() as cur:
        cur.execute(
            "INSERT INTO telegram_links (chat_id, username, linked_at) VALUES (%s, %s, %s) "
            "ON CONFLICT (chat_id) DO UPDATE SET username = excluded.username, linked_at = excluded.linked_at",
            (chat_id, username, datetime.utcnow().isoformat(timespec="seconds") + "Z"),
        )


def desvincular_telegram(chat_id):
    with _cursor() as cur:
        cur.execute("DELETE FROM telegram_links WHERE chat_id = %s", (chat_id,))
        return cur.rowcount > 0


def usuario_vinculado(chat_id):
    """Devuelve el usuario (con su tarjeta) vinculado a este chat_id de
    Telegram, o None si el chat todavía no hizo /vincular."""
    with _cursor(dict_rows=True) as cur:
        cur.execute("SELECT username FROM telegram_links WHERE chat_id = %s", (chat_id,))
        fila = cur.fetchone()
    if not fila:
        return None
    return obtener_usuario(fila["username"])


def _bootstrap_admin():
    """
    Crea un admin desde variables de entorno si todavía no existe (y no
    toca nada si ya existe). Sigue siendo útil incluso con almacenamiento
    persistente - por ejemplo, si en algún momento se recrea el proyecto de
    Supabase desde cero. Opcional: si no están las tres variables, no hace
    nada.
    """
    admin_user = config.BOOTSTRAP_ADMIN_USERNAME
    admin_pass = config.BOOTSTRAP_ADMIN_PASSWORD
    admin_tarjeta = config.BOOTSTRAP_ADMIN_TARJETA
    if not (admin_user and admin_pass and admin_tarjeta):
        return
    if obtener_usuario(admin_user):
        return

    crear_usuario(admin_user, generate_password_hash(admin_pass), admin_tarjeta, es_admin=True)
