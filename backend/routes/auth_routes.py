"""Salud del servicio, login, cambio de contraseña propio, y whoami."""

from flask import Blueprint, g, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

from .. import auth, config, db

bp = Blueprint("auth_routes", __name__)

# Bloqueo tras intentos fallidos de login. Se lleva por username y también
# por IP: solo por username, cualquiera podría bloquearle el login a otra
# persona mandando contraseñas falsas con su usuario; el contador por IP
# corta ese abuso sin castigar a la víctima. Ver auth._segundos_bloqueado
# y compañía.
_intentos_login = {}
_intentos_login_ip = {}

# Hash fijo contra el que comparar cuando el usuario no existe, para que
# la respuesta tarde lo mismo que con un usuario real y no se pueda
# distinguir "usuario inexistente" de "contraseña incorrecta" por el
# tiempo de respuesta.
_HASH_DUMMY = generate_password_hash("password-que-nunca-se-usa")


@bp.route("/")
def health():
    db_ok = db.ping()
    return jsonify({
        "status": "ok", 
        "service": "registro-horas-backend", 
        "db_ok": db_ok
    })


@bp.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip().lower()
    password = data.get("password", "")

    ip = auth.ip_cliente()
    restante = max(
        auth._segundos_bloqueado(_intentos_login, username),
        auth._segundos_bloqueado(_intentos_login_ip, ip),
    )
    if restante > 0:
        minutos = int(restante // 60) + 1
        return jsonify({"error": f"Demasiados intentos fallidos. Prueba de nuevo en {minutos} min."}), 429

    usuario = db.obtener_usuario(username)
    hash_a_comparar = usuario["password_hash"] if usuario else _HASH_DUMMY
    if not check_password_hash(hash_a_comparar, password) or not usuario:
        auth._registrar_intento_fallido(_intentos_login, username)
        auth._registrar_intento_fallido(_intentos_login_ip, ip)
        return jsonify({"error": "Usuario o contraseña incorrectos."}), 401

    auth._limpiar_intentos(_intentos_login, username)
    auth._limpiar_intentos(_intentos_login_ip, ip)
    return jsonify({
        "ok": True,
        "token": auth.generar_token(usuario),
        "username": usuario["username"],
        "tarjeta": usuario["tarjeta"],
        "es_admin": bool(usuario["es_admin"]),
        "expira_en_segundos": config.TOKEN_LIFETIME_SEGUNDOS,
    })


@bp.route("/api/cambiar-password", methods=["POST"])
def cambiar_password():
    data = request.get_json(silent=True) or {}
    actual = data.get("actual", "")
    nueva = data.get("nueva", "")
    confirmar = data.get("confirmar", "")

    usuario = db.obtener_usuario(g.usuario["username"])
    if not usuario:
        return jsonify({"error": "Tu usuario ya no existe. Volvé a iniciar sesión."}), 401
    if not check_password_hash(usuario["password_hash"], actual):
        return jsonify({"error": "La contraseña actual no es correcta."}), 400
    if len(nueva) < 6:
        return jsonify({"error": "La contraseña nueva debe tener al menos 6 caracteres."}), 400
    if nueva != confirmar:
        return jsonify({"error": "Las contraseñas nuevas no coinciden."}), 400

    db.actualizar_password(g.usuario["username"], generate_password_hash(nueva))
    return jsonify({"ok": True})


@bp.route("/api/whoami", methods=["GET"])
def whoami():
    return jsonify({
        "username": g.usuario.get("username"),
        "tarjeta": g.usuario.get("tarjeta"),
        "es_admin": g.usuario.get("es_admin", False),
    })
