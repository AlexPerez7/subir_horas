"""Gestión de usuarios y auditoría - todo solo para administradores."""

from flask import Blueprint, g, jsonify, request
from werkzeug.security import generate_password_hash

from .. import auth, db

bp = Blueprint("usuarios_routes", __name__)


@bp.route("/api/usuarios", methods=["GET"])
def listar_usuarios():
    error = auth.requiere_admin()
    if error:
        return error
    return jsonify(db.listar_usuarios())


@bp.route("/api/usuarios", methods=["POST"])
def crear_usuario_api():
    error = auth.requiere_admin()
    if error:
        return error

    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip().lower()
    tarjeta = data.get("tarjeta", "").strip()
    password = data.get("password", "")
    es_admin_nuevo = bool(data.get("es_admin"))

    if not username or not tarjeta:
        return jsonify({"error": "faltan usuario o tarjeta"}), 400
    if len(password) < 6:
        return jsonify({"error": "la contraseña debe tener al menos 6 caracteres"}), 400
    if db.obtener_usuario(username):
        return jsonify({"error": f"ya existe el usuario '{username}'"}), 409

    db.crear_usuario(username, generate_password_hash(password), tarjeta, es_admin_nuevo)
    db.registrar_auditoria(g.usuario["username"], "crear_usuario", f"username={username} tarjeta={tarjeta} es_admin={es_admin_nuevo}")
    return jsonify({"ok": True})


@bp.route("/api/usuarios/<username>/resetear-password", methods=["POST"])
def resetear_password_usuario(username):
    error = auth.requiere_admin()
    if error:
        return error

    username = username.strip().lower()
    data = request.get_json(silent=True) or {}
    nueva = data.get("nueva", "")

    if len(nueva) < 6:
        return jsonify({"error": "la contraseña debe tener al menos 6 caracteres"}), 400
    if not db.obtener_usuario(username):
        return jsonify({"error": f"no existe el usuario '{username}'"}), 404

    db.actualizar_password(username, generate_password_hash(nueva))
    db.registrar_auditoria(g.usuario["username"], "resetear_password", f"username={username}")
    return jsonify({"ok": True})


@bp.route("/api/usuarios/<username>", methods=["DELETE"])
def borrar_usuario(username):
    error = auth.requiere_admin()
    if error:
        return error

    username = username.strip().lower()
    if username == g.usuario["username"]:
        return jsonify({"error": "no podés eliminar tu propio usuario"}), 400

    if not db.eliminar_usuario(username):
        return jsonify({"error": f"no existe el usuario '{username}'"}), 404
    db.registrar_auditoria(g.usuario["username"], "eliminar_usuario", f"username={username}")
    return jsonify({"ok": True})


@bp.route("/api/auditoria", methods=["GET"])
def listar_auditoria():
    error = auth.requiere_admin()
    if error:
        return error
    return jsonify(db.listar_auditoria(limite=50))
