"""
Capa de acceso a datos (Supabase, vía su API REST/PostgREST sobre HTTPS):
cuentas de la app, auditoría de acciones de administración, y el vínculo
chat_id de Telegram → cuenta. Sin dependencia de Flask - las rutas pasan
explícitamente lo que necesitan (ej. el actor de una auditoría) en vez de
que este módulo lea `g` por su cuenta.

Usa la API REST de Supabase (paquete `supabase`, habla HTTPS/443) en vez
de una conexión directa por el protocolo de Postgres (`psycopg2`, puertos
5432/6543) porque el backend corre en una red que solo deja salir tráfico
HTTPS - ver README, "Configurar Supabase". Usa la `service_role` key (no
la `anon`), que bypassea Row Level Security: es el equivalente al acceso
total que ya tenía la conexión directa a Postgres, y como esta key nunca
sale del backend (el navegador no le habla nunca a Supabase directo), es
seguro.

Consecuencia de usar la API REST: no se pueden crear tablas desde acá
(PostgREST no soporta DDL) - las tablas se crean una única vez a mano en
el SQL Editor de Supabase, ver README.
"""

from datetime import datetime

from supabase import create_client
from werkzeug.security import generate_password_hash

from . import config

_client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)


def _inicializar_db():
    """No-op a propósito: a diferencia de una conexión Postgres directa,
    la API REST no puede correr CREATE TABLE. Las tablas (usuarios,
    auditoria, telegram_links) se crean una única vez a mano en el SQL
    Editor de Supabase - ver README, "Configurar Supabase"."""
    pass


def registrar_auditoria(actor, accion, detalle=""):
    """
    Deja rastro de una acción de administración (crear/eliminar
    usuario, resetear contraseña). Registro permanente en Supabase -
    no se pierde al reiniciar el servicio.
    """
    _client.table("auditoria").insert({
        "ts": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "actor": actor,
        "accion": accion,
        "detalle": detalle,
    }).execute()


def listar_auditoria(limite=50):
    res = (
        _client.table("auditoria")
        .select("ts,actor,accion,detalle")
        .order("id", desc=True)
        .limit(limite)
        .execute()
    )
    return res.data


def obtener_usuario(username):
    res = (
        _client.table("usuarios")
        .select("username,password_hash,tarjeta,es_admin")
        .eq("username", username)
        .execute()
    )
    return res.data[0] if res.data else None


def listar_usuarios():
    res = _client.table("usuarios").select("username,tarjeta,es_admin").order("username").execute()
    return res.data


def crear_usuario(username, password_hash, tarjeta, es_admin):
    _client.table("usuarios").insert({
        "username": username,
        "password_hash": password_hash,
        "tarjeta": tarjeta,
        "es_admin": int(es_admin),
    }).execute()


def eliminar_usuario(username):
    res = _client.table("usuarios").delete().eq("username", username).execute()
    return len(res.data) > 0


def actualizar_password(username, nuevo_hash):
    _client.table("usuarios").update({"password_hash": nuevo_hash}).eq("username", username).execute()


def vincular_telegram(chat_id, username):
    """Asocia un chat_id de Telegram a una cuenta de la app (ver /vincular
    en el bot). Un chat_id solo puede estar vinculado a un username a la vez
    - re-vincular pisa el vínculo anterior."""
    _client.table("telegram_links").upsert({
        "chat_id": chat_id,
        "username": username,
        "linked_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }).execute()


def desvincular_telegram(chat_id):
    res = _client.table("telegram_links").delete().eq("chat_id", chat_id).execute()
    return len(res.data) > 0


def usuario_vinculado(chat_id):
    """Devuelve el usuario (con su tarjeta) vinculado a este chat_id de
    Telegram, o None si el chat todavía no hizo /vincular."""
    res = _client.table("telegram_links").select("username").eq("chat_id", chat_id).execute()
    if not res.data:
        return None
    return obtener_usuario(res.data[0]["username"])


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
