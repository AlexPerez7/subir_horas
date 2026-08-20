"""Salud del servicio, login, cambio de contraseña propio, y whoami."""

from flask import Blueprint, g, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

from .. import auth, config, db

bp = Blueprint("auth_routes", __name__)

# Bloqueo tras intentos fallidos de login, keyado por username - ver
# auth._segundos_bloqueado y compañía.
_intentos_login = {}


@bp.route("/")
def health():
    return jsonify({"status": "ok", "service": "registro-horas-backend"})


@bp.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip().lower()
    password = data.get("password", "")

    restante = auth._segundos_bloqueado(_intentos_login, username)
    if restante > 0:
        minutos = int(restante // 60) + 1
        return jsonify({"error": f"Demasiados intentos fallidos. Prueba de nuevo en {minutos} min."}), 429

    usuario = db.obtener_usuario(username)
    if not usuario or not check_password_hash(usuario["password_hash"], password):
        auth._registrar_intento_fallido(_intentos_login, username)
        return jsonify({"error": "Usuario o contraseña incorrectos."}), 401

    auth._limpiar_intentos(_intentos_login, username)
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
