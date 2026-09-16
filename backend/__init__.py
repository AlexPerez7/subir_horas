"""
Backend Flask (API JSON pura) que habla con Odoo y gestiona el login
propio de la app. Se despliega aparte del frontend (que vive como
sitio estatico en GitHub Pages) - por eso todo acá es JSON, nada de
paginas server-rendered.

Autenticacion por token, no por cookie de sesion: el login devuelve un
token firmado que el frontend guarda (localStorage) y manda en el
header "Authorization: Bearer <token>" en cada pedido. Se eligio este
esquema en vez de cookies porque frontend (GitHub Pages) y backend
(VM propia, vía Tailscale Funnel) viven en dominios distintos, y varios navegadores (Safari,
Brave, Samsung Internet, y cada vez mas) bloquean por defecto las
cookies "de terceros" aunque tengan SameSite=None; Secure - un token
en un header no depende de ninguna politica de cookies.

Cada usuario esta asociado a una tarjeta especifica de Odoo (definida
al crear su cuenta) - el backend nunca confia en una "tarjeta" que
venga del navegador para usuarios normales, salvo que la cuenta sea de
administrador.

Instalar dependencias:
    pip install -r requirements.txt

Variables de entorno esperadas (.env, NUNCA subir a git; ver .env.example):

    ODOO_URL=https://www.assertiva.biz/jsonrpc
    ODOO_DB=origami-soft-assertiva-main-...
    ODOO_UID=429
    ODOO_TOKEN=xxxxxxxxxxxxxxxx

    SECRET_KEY=<cadena larga y aleatoria, para firmar los tokens>
    SESSION_LIFETIME_HORAS=8   (opcional, default 8 - vigencia del token)

    SUPABASE_URL=<Project URL de Supabase>
    SUPABASE_SERVICE_ROLE_KEY=<service_role key de Supabase, NO la anon>
        (usuarios y auditoría, vía la API REST de Supabase)

    FRONTEND_ORIGINS=https://tu-usuario.github.io   (lista separada por comas)

Organización del código (este paquete):

    config.py    - variables de entorno y constantes
    db.py        - Supabase (vía API REST): usuarios, auditoría
    auth.py      - token de sesión y bloqueo por intentos fallidos
    odoo_client.py - cliente JSON-RPC de Odoo + caché
    horas.py     - lógica de negocio (días hábiles, resumen, validaciones)
    routes/      - un blueprint por área de la API

`backend_odoo.py`, en la raíz del repo, es sólo el punto de entrada que
usa gunicorn (ver `deploy/subir-horas.service`): crea la app acá y la
expone como `app`.
"""

import logging
import traceback

from flask import Flask, g, jsonify, request
from werkzeug.exceptions import HTTPException
from flask_cors import CORS

from . import auth, config, db

_log = logging.getLogger("registro-horas")

RUTAS_PUBLICAS = ("/", "/api/login")


def create_app():
    app = Flask(__name__, static_folder=None)
    app.secret_key = config.SECRET_KEY
    CORS(app, origins=config.FRONTEND_ORIGINS, allow_headers=["Content-Type", "Authorization"])

    db._inicializar_db()
    db._bootstrap_admin()

    from .routes.auth_routes import bp as auth_bp
    from .routes.usuarios_routes import bp as usuarios_bp
    from .routes.timesheet_routes import bp as timesheet_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(usuarios_bp)
    app.register_blueprint(timesheet_bp)

    @app.errorhandler(Exception)
    def manejar_error(e):
        # Los errores HTTP explícitos (abort(4xx), 404 de ruta, etc.) se
        # devuelven tal cual. Cualquier otra excepción (un fallo de Odoo,
        # un bug) se loguea completa en journalctl pero al cliente le
        # llega solo un mensaje genérico - nunca el traceback ni el
        # detalle interno de Odoo.
        if isinstance(e, HTTPException):
            return e
        _log.error("Error no controlado en %s %s:\n%s", request.method, request.path, traceback.format_exc())
        return jsonify({"error": "Error interno del servidor. Reintentá en un momento."}), 500

    @app.before_request
    def proteger_todo():
        if request.method == "OPTIONS":
            return  # preflight CORS
        if request.path in RUTAS_PUBLICAS:
            return

        auth_header = request.headers.get("Authorization", "")
        token = auth_header[len("Bearer "):] if auth_header.startswith("Bearer ") else None
        usuario = auth.decodificar_token(token) if token else None
        if not usuario:
            return jsonify({"error": "no autenticado"}), 401
        g.usuario = usuario

    return app
