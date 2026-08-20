"""
Autenticación por token (no por cookie de sesión): el login devuelve un
token firmado que el frontend guarda (localStorage) y manda en el header
"Authorization: Bearer <token>" en cada pedido. Se eligió este esquema en
vez de cookies porque frontend (GitHub Pages) y backend (Koyeb) viven en
dominios distintos, y varios navegadores (Safari, Brave, Samsung Internet)
bloquean por defecto las cookies "de terceros" aunque tengan
SameSite=None; Secure.

También vive acá el bloqueo genérico tras intentos fallidos (usado tanto
por el login web como por /vincular del bot de Telegram, cada uno con su
propio diccionario en memoria).
"""

import time

from flask import g, jsonify
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from . import config

_serializer = URLSafeTimedSerializer(config.SECRET_KEY, salt="registro-horas-token")


def generar_token(usuario):
    return _serializer.dumps({
        "username": usuario["username"],
        "tarjeta": usuario["tarjeta"],
        "es_admin": bool(usuario["es_admin"]),
    })


def decodificar_token(token):
    try:
        return _serializer.loads(token, max_age=config.TOKEN_LIFETIME_SEGUNDOS)
    except (BadSignature, SignatureExpired):
        return None


def requiere_admin():
    """Devuelve una respuesta 403 si el usuario actual no es administrador, o None si puede seguir."""
    if not g.usuario.get("es_admin"):
        return jsonify({"error": "solo administradores"}), 403
    return None


# Bloqueo simple tras varios intentos fallidos, en memoria del proceso (no
# en la base de datos): alcanza porque el Procfile corre un único worker de
# gunicorn, así que no hace falta coordinar estado entre procesos. Este
# contador sí se resetea en cada redeploy (a diferencia de los usuarios,
# que viven en Postgres) - no tiene mayor impacto, vuelve a arrancar en
# cero. Cada feature que lo usa (login web, bot de Telegram) tiene su
# propio diccionario y pasa su propia clave (username o chat_id) - estas
# funciones son genéricas sobre esos dos parámetros.
INTENTOS_MAXIMOS = 5
BLOQUEO_SEGUNDOS = 300


def _segundos_bloqueado(intentos_dict, clave):
    estado = intentos_dict.get(clave)
    if not estado:
        return 0
    return max(0, estado["bloqueado_hasta"] - time.time())


def _registrar_intento_fallido(intentos_dict, clave):
    estado = intentos_dict.setdefault(clave, {"conteo": 0, "bloqueado_hasta": 0})
    estado["conteo"] += 1
    if estado["conteo"] >= INTENTOS_MAXIMOS:
        estado["bloqueado_hasta"] = time.time() + BLOQUEO_SEGUNDOS
        estado["conteo"] = 0


def _limpiar_intentos(intentos_dict, clave):
    intentos_dict.pop(clave, None)
