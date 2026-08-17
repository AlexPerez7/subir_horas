"""
Backend Flask (API JSON pura) que habla con Odoo y gestiona el login
propio de la app. Se despliega aparte del frontend (que vive como
sitio estatico en GitHub Pages) - por eso todo acá es JSON, nada de
paginas server-rendered.

Autenticacion por token, no por cookie de sesion: el login devuelve un
token firmado que el frontend guarda (localStorage) y manda en el
header "Authorization: Bearer <token>" en cada pedido. Se eligio este
esquema en vez de cookies porque frontend (GitHub Pages) y backend
(Render) viven en dominios distintos, y varios navegadores (Safari,
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

    FRONTEND_ORIGINS=https://tu-usuario.github.io   (lista separada por comas)

    CRON_SECRET=<cadena aleatoria>   (opcional - habilita /api/recordatorio-cron
        para el recordatorio automático por Telegram; ver README)

    TELEGRAM_BOT_TOKEN=<token de @BotFather>       (opcional - habilita el bot
    TELEGRAM_WEBHOOK_SECRET=<cadena aleatoria>      interactivo de Telegram;
                                                     ver README. TELEGRAM_CHAT_ID
                                                     NO hace falta acá: cada
                                                     cuenta se vincula con
                                                     /vincular desde el propio
                                                     chat - ese secret solo lo
                                                     usan los workflows de
                                                     GitHub Actions de cron)
"""

import os
import re
import sqlite3
import sys
import time
import unicodedata
from datetime import date, datetime, timedelta

import requests
from flask import Flask, request, jsonify, g
from flask_cors import CORS
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from werkzeug.security import check_password_hash, generate_password_hash
from dotenv import load_dotenv

_CARPETA = os.path.dirname(os.path.abspath(__file__))

load_dotenv(os.path.join(_CARPETA, ".env"))


def _env_obligatoria(nombre):
    """Como os.environ[nombre], pero con un mensaje claro (qué variable
    falta y dónde revisar) en vez de un KeyError críptico en los logs de
    Render cuando falta configurar algo."""
    valor = os.environ.get(nombre)
    if not valor:
        sys.exit(
            f"Falta la variable de entorno obligatoria '{nombre}'. "
            f"Revisá tu .env local (o las variables de entorno en Render) - ver .env.example."
        )
    return valor


ODOO_URL = _env_obligatoria("ODOO_URL")
ODOO_DB = _env_obligatoria("ODOO_DB")
ODOO_UID = int(_env_obligatoria("ODOO_UID"))
ODOO_TOKEN = _env_obligatoria("ODOO_TOKEN")

DB_PATH = os.path.join(_CARPETA, "usuarios.db")

FRONTEND_ORIGINS = [o.strip() for o in os.environ.get("FRONTEND_ORIGINS", "").split(",") if o.strip()]
TOKEN_LIFETIME_SEGUNDOS = int(os.environ.get("SESSION_LIFETIME_HORAS", 8)) * 3600
CRON_SECRET = os.environ.get("CRON_SECRET", "")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")

app = Flask(__name__, static_folder=None)
app.secret_key = _env_obligatoria("SECRET_KEY")
CORS(app, origins=FRONTEND_ORIGINS, allow_headers=["Content-Type", "Authorization"])

_serializer = URLSafeTimedSerializer(app.secret_key, salt="registro-horas-token")

PROJECT_NAME = "GER_Produccion Varios NF"


# --------------------------------------------------------------------
# Usuarios (SQLite)
# --------------------------------------------------------------------

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


def registrar_auditoria(accion, detalle=""):
    """
    Deja rastro de una acción de administración (crear/eliminar
    usuario, resetear contraseña). Igual que usuarios.db, se pierde en
    cada redeploy de Render free (disco efímero) - sirve para auditar
    entre deploys, no como registro permanente.
    """
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO auditoria (ts, actor, accion, detalle) VALUES (?, ?, ?, ?)",
        (datetime.utcnow().isoformat(timespec="seconds") + "Z", g.usuario["username"], accion, detalle),
    )
    con.commit()
    con.close()


def obtener_usuario(username):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    fila = con.execute(
        "SELECT username, password_hash, tarjeta, es_admin FROM usuarios WHERE username = ?",
        (username,),
    ).fetchone()
    con.close()
    return dict(fila) if fila else None


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


def requiere_admin():
    """Devuelve una respuesta 403 si el usuario actual no es administrador, o None si puede seguir."""
    if not g.usuario.get("es_admin"):
        return jsonify({"error": "solo administradores"}), 403
    return None


def _bootstrap_admin():
    """
    Crea un admin desde variables de entorno si todavía no existe (y no
    toca nada si ya existe). Pensado para plataformas sin acceso a
    Shell en el plan gratuito (ej. Render): en vez de correr
    crear_usuario.py a mano, el propio backend se auto-crea el primer
    admin al arrancar. Opcional: si no están las tres variables, no
    hace nada.
    """
    admin_user = os.environ.get("BOOTSTRAP_ADMIN_USERNAME", "").strip().lower()
    admin_pass = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "")
    admin_tarjeta = os.environ.get("BOOTSTRAP_ADMIN_TARJETA", "").strip()
    if not (admin_user and admin_pass and admin_tarjeta):
        return
    if obtener_usuario(admin_user):
        return

    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO usuarios (username, password_hash, tarjeta, es_admin) VALUES (?, ?, ?, 1)",
        (admin_user, generate_password_hash(admin_pass), admin_tarjeta),
    )
    con.commit()
    con.close()


_inicializar_db()
_bootstrap_admin()


# --------------------------------------------------------------------
# Autenticación por token (todo JSON - el frontend es una SPA aparte)
# --------------------------------------------------------------------

def generar_token(usuario):
    return _serializer.dumps({
        "username": usuario["username"],
        "tarjeta": usuario["tarjeta"],
        "es_admin": bool(usuario["es_admin"]),
    })


def decodificar_token(token):
    try:
        return _serializer.loads(token, max_age=TOKEN_LIFETIME_SEGUNDOS)
    except (BadSignature, SignatureExpired):
        return None


RUTAS_PUBLICAS = ("/", "/api/login", "/api/recordatorio-cron", "/api/resumen-semanal-cron", "/api/telegram-webhook")


@app.before_request
def proteger_todo():
    if request.method == "OPTIONS":
        return  # preflight CORS
    if request.path in RUTAS_PUBLICAS:
        return

    auth = request.headers.get("Authorization", "")
    token = auth[len("Bearer "):] if auth.startswith("Bearer ") else None
    usuario = decodificar_token(token) if token else None
    if not usuario:
        return jsonify({"error": "no autenticado"}), 401
    g.usuario = usuario


@app.route("/")
def health():
    return jsonify({"status": "ok", "service": "registro-horas-backend"})


# Bloqueo simple tras varios intentos fallidos de login (o de /vincular del
# bot de Telegram, ver más abajo), en memoria del proceso (no en SQLite):
# alcanza porque el Procfile corre un único worker de gunicorn, así que no
# hace falta coordinar estado entre procesos. Se resetea en cada redeploy,
# igual que usuarios.db. _intentos_login usa el username como clave;
# _intentos_vincular_telegram usa el chat_id (ahí todavía no sabemos qué
# username está probando).
_intentos_login = {}
_intentos_vincular_telegram = {}
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


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip().lower()
    password = data.get("password", "")

    restante = _segundos_bloqueado(_intentos_login, username)
    if restante > 0:
        minutos = int(restante // 60) + 1
        return jsonify({"error": f"Demasiados intentos fallidos. Probá de nuevo en {minutos} min."}), 429

    usuario = obtener_usuario(username)
    if not usuario or not check_password_hash(usuario["password_hash"], password):
        _registrar_intento_fallido(_intentos_login, username)
        return jsonify({"error": "Usuario o contraseña incorrectos."}), 401

    _limpiar_intentos(_intentos_login, username)
    return jsonify({
        "ok": True,
        "token": generar_token(usuario),
        "username": usuario["username"],
        "tarjeta": usuario["tarjeta"],
        "es_admin": bool(usuario["es_admin"]),
        "expira_en_segundos": TOKEN_LIFETIME_SEGUNDOS,
    })


@app.route("/api/cambiar-password", methods=["POST"])
def cambiar_password():
    data = request.get_json(silent=True) or {}
    actual = data.get("actual", "")
    nueva = data.get("nueva", "")
    confirmar = data.get("confirmar", "")

    usuario = obtener_usuario(g.usuario["username"])
    if not check_password_hash(usuario["password_hash"], actual):
        return jsonify({"error": "La contraseña actual no es correcta."}), 400
    if len(nueva) < 6:
        return jsonify({"error": "La contraseña nueva debe tener al menos 6 caracteres."}), 400
    if nueva != confirmar:
        return jsonify({"error": "Las contraseñas nuevas no coinciden."}), 400

    actualizar_password(g.usuario["username"], generate_password_hash(nueva))
    return jsonify({"ok": True})


@app.route("/api/whoami", methods=["GET"])
def whoami():
    return jsonify({
        "username": g.usuario.get("username"),
        "tarjeta": g.usuario.get("tarjeta"),
        "es_admin": g.usuario.get("es_admin", False),
    })


# --------------------------------------------------------------------
# Gestión de usuarios (solo administradores)
# --------------------------------------------------------------------

@app.route("/api/usuarios", methods=["GET"])
def listar_usuarios():
    error = requiere_admin()
    if error:
        return error
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    filas = con.execute("SELECT username, tarjeta, es_admin FROM usuarios ORDER BY username").fetchall()
    con.close()
    return jsonify([dict(f) for f in filas])


@app.route("/api/usuarios", methods=["POST"])
def crear_usuario_api():
    error = requiere_admin()
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
    if obtener_usuario(username):
        return jsonify({"error": f"ya existe el usuario '{username}'"}), 409

    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO usuarios (username, password_hash, tarjeta, es_admin) VALUES (?, ?, ?, ?)",
        (username, generate_password_hash(password), tarjeta, int(es_admin_nuevo)),
    )
    con.commit()
    con.close()
    registrar_auditoria("crear_usuario", f"username={username} tarjeta={tarjeta} es_admin={es_admin_nuevo}")
    return jsonify({"ok": True})


@app.route("/api/usuarios/<username>/resetear-password", methods=["POST"])
def resetear_password_usuario(username):
    error = requiere_admin()
    if error:
        return error

    username = username.strip().lower()
    data = request.get_json(silent=True) or {}
    nueva = data.get("nueva", "")

    if len(nueva) < 6:
        return jsonify({"error": "la contraseña debe tener al menos 6 caracteres"}), 400
    if not obtener_usuario(username):
        return jsonify({"error": f"no existe el usuario '{username}'"}), 404

    actualizar_password(username, generate_password_hash(nueva))
    registrar_auditoria("resetear_password", f"username={username}")
    return jsonify({"ok": True})


@app.route("/api/usuarios/<username>", methods=["DELETE"])
def borrar_usuario(username):
    error = requiere_admin()
    if error:
        return error

    username = username.strip().lower()
    if username == g.usuario["username"]:
        return jsonify({"error": "no podés eliminar tu propio usuario"}), 400

    con = sqlite3.connect(DB_PATH)
    cur = con.execute("DELETE FROM usuarios WHERE username = ?", (username,))
    con.commit()
    con.close()
    if not cur.rowcount:
        return jsonify({"error": f"no existe el usuario '{username}'"}), 404
    registrar_auditoria("eliminar_usuario", f"username={username}")
    return jsonify({"ok": True})


@app.route("/api/auditoria", methods=["GET"])
def listar_auditoria():
    error = requiere_admin()
    if error:
        return error
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    filas = con.execute(
        "SELECT ts, actor, accion, detalle FROM auditoria ORDER BY id DESC LIMIT 50"
    ).fetchall()
    con.close()
    return jsonify([dict(f) for f in filas])


# --------------------------------------------------------------------
# Cliente Odoo (JSON-RPC)
# --------------------------------------------------------------------

def odoo_execute_kw(model, method, args, kwargs=None):
    payload = {
        "jsonrpc": "2.0",
        "method": "call",
        "params": {
            "service": "object",
            "method": "execute_kw",
            "args": [ODOO_DB, ODOO_UID, ODOO_TOKEN, model, method, args, kwargs or {}],
        },
        "id": 1,
    }
    resp = requests.post(ODOO_URL, json=payload, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(data["error"])
    return data["result"]


# Caché en memoria con TTL corto para las resoluciones de Odoo que se repiten
# en casi cada request (project_ids, subtareas de una tarjeta, id de tarea
# por nombre, empleado de una tarea) - project.task/hr.employee cambian con
# poca frecuencia (los edita un admin directo en Odoo), así que 60s de
# staleness es un costo aceptable a cambio de no repetir 3-4 llamadas
# JSON-RPC por cada carga de página o cada línea de una carga en lote.
# Mismo criterio que _intentos_login/PENDIENTES_TELEGRAM más abajo: alcanza
# con memoria del proceso porque el Procfile corre un único worker.
_CACHE_TTL_SEGUNDOS = 60
_cache_project_ids = {}
_cache_subtareas = {}
_cache_tarea_id = {}
_cache_employee_id = {}


def _con_cache(cache_dict, clave, funcion):
    ahora = time.time()
    entrada = cache_dict.get(clave)
    if entrada and ahora - entrada[1] < _CACHE_TTL_SEGUNDOS:
        return entrada[0]
    valor = funcion()
    cache_dict[clave] = (valor, ahora)
    return valor


def obtener_project_ids():
    return _con_cache(_cache_project_ids, "_", lambda: odoo_execute_kw(
        "project.project", "search",
        [[["name", "=", PROJECT_NAME]]],
    ))


def subtareas_de_tarjeta(tarjeta):
    def _consultar():
        project_ids = obtener_project_ids()
        return odoo_execute_kw(
            "project.task", "search_read",
            [[
                ["project_id", "in", project_ids],
                ["parent_id.name", "=", tarjeta],
            ]],
            {"fields": ["id", "name"]},
        )
    return _con_cache(_cache_subtareas, tarjeta, _consultar)


def subtareas_ids_de_tarjeta(tarjeta):
    return [t["id"] for t in subtareas_de_tarjeta(tarjeta)]


def buscar_tarea_id(nombre_subtarea, tarjeta):
    def _consultar():
        project_ids = obtener_project_ids()
        task_ids = odoo_execute_kw(
            "project.task", "search",
            [[
                ["name", "=", nombre_subtarea],
                ["project_id", "in", project_ids],
                ["parent_id.name", "=", tarjeta],
            ]],
            {"limit": 1},
        )
        return task_ids[0] if task_ids else None
    return _con_cache(_cache_tarea_id, (nombre_subtarea, tarjeta), _consultar)


def obtener_employee_de_tarea(task_id):
    def _consultar():
        tarea = odoo_execute_kw(
            "project.task", "read",
            [[task_id]], {"fields": ["user_ids"]},
        )[0]
        asignados = tarea.get("user_ids") or []
        if not asignados:
            raise RuntimeError(f"La tarea {task_id} no tiene ninguna persona asignada (user_ids vacío).")
        user_id = asignados[0]

        empleados = odoo_execute_kw(
            "hr.employee", "search_read",
            [[["user_id", "=", user_id]]],
            {"fields": ["id", "name"], "limit": 1},
        )
        if not empleados:
            raise RuntimeError(f"No se encontró un hr.employee vinculado al usuario asignado (user_id={user_id}).")
        return empleados[0]["id"]
    return _con_cache(_cache_employee_id, task_id, _consultar)


def tarjeta_de_la_request(data_o_args):
    tarjeta_sesion = g.usuario["tarjeta"]
    if g.usuario.get("es_admin"):
        tarjeta_pedida = data_o_args.get("tarjeta")
        if tarjeta_pedida:
            return tarjeta_pedida
    return tarjeta_sesion


def dia_habil_anterior(d):
    """Viernes si d es lunes o domingo, si no, el día calendario anterior."""
    if d.weekday() == 0:   # lunes
        return d - timedelta(days=3)
    if d.weekday() == 6:   # domingo
        return d - timedelta(days=2)
    return d - timedelta(days=1)


def dias_habiles_atras(n, desde=None):
    """Últimos n días hábiles (lunes a viernes) antes de 'desde' (no incluye 'desde')."""
    dias = []
    d = desde or date.today()
    while len(dias) < n:
        d = d - timedelta(days=1)
        if d.weekday() < 5:  # 0-4 = lunes a viernes
            dias.append(d)
    return dias


def _validar_horas(valor):
    """Devuelve el valor como float si es un número > 0, o None si no lo es."""
    try:
        horas = float(valor)
    except (TypeError, ValueError):
        return None
    return horas if horas > 0 else None


def _fecha_valida(valor):
    try:
        date.fromisoformat(valor)
        return True
    except (TypeError, ValueError):
        return False


# --------------------------------------------------------------------
# Endpoints de la API
# --------------------------------------------------------------------

@app.route("/api/tarjetas", methods=["GET"])
def listar_tarjetas():
    if not g.usuario.get("es_admin"):
        return jsonify([{"id": None, "name": g.usuario["tarjeta"]}])
    project_ids = obtener_project_ids()
    tarjetas = odoo_execute_kw(
        "project.task", "search_read",
        [[
            ["project_id", "in", project_ids],
            ["parent_id", "=", False],
        ]],
        {"fields": ["id", "name"]},
    )
    return jsonify(tarjetas)


@app.route("/api/subtareas", methods=["GET"])
def listar_subtareas():
    tarjeta = tarjeta_de_la_request(request.args)
    return jsonify(subtareas_de_tarjeta(tarjeta))


@app.route("/api/campos", methods=["GET"])
def listar_campos():
    if not g.usuario.get("es_admin"):
        return jsonify({"error": "solo administradores"}), 403
    modelo = request.args.get("modelo", "account.analytic.line")
    campos = odoo_execute_kw(modelo, "fields_get", [], {"attributes": ["string", "type"]})
    q = request.args.get("q", "").strip().lower()
    if q:
        campos = {k: v for k, v in campos.items() if q in k.lower() or q in v.get("string", "").lower()}
    return jsonify(campos)


@app.route("/api/timesheet/recientes", methods=["GET"])
def timesheet_recientes():
    tarjeta = tarjeta_de_la_request(request.args)
    subtarea = request.args.get("subtarea")
    limite = int(request.args.get("limite", 8))

    if not subtarea:
        return jsonify({"error": "falta el parámetro subtarea"}), 400

    task_id = buscar_tarea_id(subtarea, tarjeta)
    if not task_id:
        return jsonify({"error": f"no se encontro la subtarea '{subtarea}' en la tarjeta '{tarjeta}'"}), 404

    lineas = odoo_execute_kw(
        "account.analytic.line", "search_read",
        [[["task_id", "=", task_id]]],
        {"fields": ["date", "name", "unit_amount", "employee_id"],
         "order": "date desc, id desc", "limit": limite},
    )
    todas = odoo_execute_kw(
        "account.analytic.line", "search_read",
        [[["task_id", "=", task_id]]],
        {"fields": ["unit_amount"]},
    )
    total_horas = sum(l["unit_amount"] for l in todas)

    return jsonify({"lineas": lineas, "total_horas": total_horas})


@app.route("/api/timesheet/dia", methods=["GET"])
def timesheet_dia():
    """
    Uso: /api/timesheet/dia?fecha=2026-07-02
    Devuelve todas las líneas de esa fecha, de cualquier subtarea,
    para la tarjeta del usuario (o la indicada, si es admin).
    """
    tarjeta = tarjeta_de_la_request(request.args)
    fecha = request.args.get("fecha")
    if not fecha:
        return jsonify({"error": "falta el parámetro fecha"}), 400

    task_ids = subtareas_ids_de_tarjeta(tarjeta)
    if not task_ids:
        return jsonify({"lineas": [], "total_horas": 0})

    lineas = odoo_execute_kw(
        "account.analytic.line", "search_read",
        [[["task_id", "in", task_ids], ["date", "=", fecha]]],
        {"fields": ["task_id", "name", "unit_amount"], "order": "id asc"},
    )

    ids_unicos = list({l["task_id"][0] for l in lineas})
    nombres = {}
    if ids_unicos:
        tareas = odoo_execute_kw("project.task", "read", [ids_unicos], {"fields": ["name"]})
        nombres = {t["id"]: t["name"] for t in tareas}

    resultado = [
        {
            "id": l["id"],
            "subtarea": nombres.get(l["task_id"][0], l["task_id"][1]),
            "descripcion": l["name"],
            "horas": l["unit_amount"],
        }
        for l in lineas
    ]
    total_horas = sum(l["unit_amount"] for l in lineas)

    return jsonify({"lineas": resultado, "total_horas": total_horas})


@app.route("/api/dias-faltantes", methods=["GET"])
def dias_faltantes():
    """
    Uso: /api/dias-faltantes?dias=10
      o: /api/dias-faltantes?desde=2026-08-01
    Con 'dias', revisa los últimos N días hábiles antes de hoy. Con
    'desde', revisa los días hábiles desde esa fecha hasta ayer (p.ej.
    para "días sin cargar horas en lo que va del mes"). Devuelve
    cuáles no tienen ninguna hora registrada, para la tarjeta del usuario.
    """
    tarjeta = tarjeta_de_la_request(request.args)
    desde_str = request.args.get("desde")

    if desde_str:
        try:
            desde = date.fromisoformat(desde_str)
        except ValueError:
            return jsonify({"error": "Formato de fecha inválido en 'desde' (usar AAAA-MM-DD)."}), 400
        hoy = date.today()
        dias_a_revisar = []
        d = desde
        while d < hoy:
            if d.weekday() < 5:
                dias_a_revisar.append(d)
            d += timedelta(days=1)
    else:
        n = int(request.args.get("dias", 10))
        dias_a_revisar = dias_habiles_atras(n)

    if not dias_a_revisar:
        return jsonify({"faltantes": []})

    task_ids = subtareas_ids_de_tarjeta(tarjeta)
    if not task_ids:
        return jsonify({"faltantes": [d.isoformat() for d in dias_a_revisar]})

    fecha_min = min(dias_a_revisar).isoformat()
    fecha_max = max(dias_a_revisar).isoformat()

    lineas = odoo_execute_kw(
        "account.analytic.line", "search_read",
        [[["task_id", "in", task_ids], ["date", ">=", fecha_min], ["date", "<=", fecha_max]]],
        {"fields": ["date"]},
    )
    dias_con_horas = {l["date"] for l in lineas}

    faltantes = [d.isoformat() for d in dias_a_revisar if d.isoformat() not in dias_con_horas]
    return jsonify({"faltantes": faltantes})


def _calcular_recordatorio(tarjeta):
    fecha_revisar = dia_habil_anterior(date.today())
    task_ids = subtareas_ids_de_tarjeta(tarjeta)
    if not task_ids:
        return {"pendiente": False, "fecha": fecha_revisar.isoformat()}

    lineas = odoo_execute_kw(
        "account.analytic.line", "search_read",
        [[["task_id", "in", task_ids], ["date", "=", fecha_revisar.isoformat()]]],
        {"fields": ["id"], "limit": 1},
    )
    return {"pendiente": len(lineas) == 0, "fecha": fecha_revisar.isoformat()}


@app.route("/api/recordatorio", methods=["GET"])
def recordatorio():
    """
    Avisa si el último día hábil no tiene ninguna hora registrada,
    para la tarjeta del usuario en sesión.
    """
    return jsonify(_calcular_recordatorio(g.usuario["tarjeta"]))


@app.route("/api/recordatorio-cron", methods=["GET"])
def recordatorio_cron():
    """
    Igual que /api/recordatorio, pero pensada para un job automático
    (GitHub Actions) sin usuario logueado - protegida por un secreto
    compartido (header X-Cron-Secret) en vez de un token de sesión.
    Revisa la tarjeta de BOOTSTRAP_ADMIN_TARJETA. Si CRON_SECRET no
    está configurado, el endpoint queda deshabilitado (404) en vez de
    aceptar pedidos sin protección.
    """
    if not CRON_SECRET or request.headers.get("X-Cron-Secret") != CRON_SECRET:
        return jsonify({"error": "no encontrado"}), 404

    tarjeta = os.environ.get("BOOTSTRAP_ADMIN_TARJETA", "").strip()
    if not tarjeta:
        return jsonify({"error": "no hay BOOTSTRAP_ADMIN_TARJETA configurada"}), 500

    return jsonify(_calcular_recordatorio(tarjeta))


def _calcular_resumen(tarjeta):
    """
    Total de horas cargadas en la semana actual (lunes a domingo) y en
    el mes actual (día 1 al último), para la tarjeta dada. Se pide un
    único rango que cubre ambos períodos y se separa en Python, porque
    cuando la semana actual cruza fin/inicio de mes los dos rangos no
    son el uno subconjunto del otro.
    """
    hoy = date.today()

    inicio_semana = hoy - timedelta(days=hoy.weekday())
    fin_semana = inicio_semana + timedelta(days=6)
    inicio_mes = hoy.replace(day=1)
    fin_mes = (
        date(hoy.year, hoy.month + 1, 1) - timedelta(days=1)
        if hoy.month < 12
        else date(hoy.year, 12, 31)
    )

    task_ids = subtareas_ids_de_tarjeta(tarjeta)
    if not task_ids:
        return {"semana": 0, "mes": 0, "por_subtarea": []}

    fecha_min = min(inicio_semana, inicio_mes).isoformat()
    fecha_max = max(fin_semana, fin_mes).isoformat()

    lineas = odoo_execute_kw(
        "account.analytic.line", "search_read",
        [[["task_id", "in", task_ids], ["date", ">=", fecha_min], ["date", "<=", fecha_max]]],
        {"fields": ["date", "unit_amount", "task_id"]},
    )

    inicio_semana_iso, fin_semana_iso = inicio_semana.isoformat(), fin_semana.isoformat()
    inicio_mes_iso, fin_mes_iso = inicio_mes.isoformat(), fin_mes.isoformat()

    total_semana = sum(l["unit_amount"] for l in lineas if inicio_semana_iso <= l["date"] <= fin_semana_iso)
    total_mes = sum(l["unit_amount"] for l in lineas if inicio_mes_iso <= l["date"] <= fin_mes_iso)

    lineas_semana = [l for l in lineas if inicio_semana_iso <= l["date"] <= fin_semana_iso]
    ids_unicos = list({l["task_id"][0] for l in lineas_semana})
    nombres = {}
    if ids_unicos:
        tareas = odoo_execute_kw("project.task", "read", [ids_unicos], {"fields": ["name"]})
        nombres = {t["id"]: t["name"] for t in tareas}

    por_subtarea_totales = {}
    for l in lineas_semana:
        nombre = nombres.get(l["task_id"][0], l["task_id"][1])
        por_subtarea_totales[nombre] = por_subtarea_totales.get(nombre, 0) + l["unit_amount"]
    por_subtarea = sorted(
        [{"subtarea": k, "horas": v} for k, v in por_subtarea_totales.items()],
        key=lambda x: -x["horas"],
    )

    return {"semana": total_semana, "mes": total_mes, "por_subtarea": por_subtarea}


@app.route("/api/resumen", methods=["GET"])
def resumen_horas():
    return jsonify(_calcular_resumen(g.usuario["tarjeta"]))


@app.route("/api/resumen-semanal-cron", methods=["GET"])
def resumen_semanal_cron():
    """
    Igual que /api/recordatorio-cron: pensada para un job automático
    (GitHub Actions) sin usuario logueado, protegida por X-Cron-Secret.
    Devuelve el total de horas de la semana actual para la tarjeta de
    BOOTSTRAP_ADMIN_TARJETA, para mandar un resumen semanal por
    Telegram (ver README).
    """
    if not CRON_SECRET or request.headers.get("X-Cron-Secret") != CRON_SECRET:
        return jsonify({"error": "no encontrado"}), 404

    tarjeta = os.environ.get("BOOTSTRAP_ADMIN_TARJETA", "").strip()
    if not tarjeta:
        return jsonify({"error": "no hay BOOTSTRAP_ADMIN_TARJETA configurada"}), 500

    return jsonify(_calcular_resumen(tarjeta))


# --------------------------------------------------------------------
# Bot interactivo de Telegram
# --------------------------------------------------------------------
#
# A diferencia de los dos endpoints -cron de arriba (empujan un mensaje
# por un job periódico de GitHub Actions), esto es un webhook: Telegram
# le pega un POST a esta URL cada vez que alguien le escribe al bot, o
# toca un botón (configurado una única vez con el método setWebhook de
# la Bot API, ver README). No usa sesión de la app ni CRON_SECRET - se
# protege con el secret_token propio de Telegram (header
# X-Telegram-Bot-Api-Secret-Token), más el vínculo chat_id→cuenta que cada
# usuario crea con /vincular (ver más abajo), para que nadie más pueda
# hacerle preguntas al bot ni cargar horas a nombre de otra cuenta.
#
# El bot es multiusuario: cualquier cuenta de la app (no solo el admin
# bootstrap) puede vincular su chat de Telegram con /vincular <usuario>
# <contraseña> y usar el bot para su propia tarjeta - antes estaba
# hardcodeado a BOOTSTRAP_ADMIN_TARJETA y a un único TELEGRAM_CHAT_ID fijo,
# leído de una variable de entorno acá. Ese TELEGRAM_CHAT_ID ya no hace
# falta en el .env de este backend (el vínculo ahora vive en SQLite), pero
# sigue siendo necesario como secret de GitHub Actions para los workflows
# de recordatorio y resumen semanal, que le pegan directo a la API de
# Telegram sin pasar por acá.
#
# Además de responder preguntas, el bot deja registrar horas por chat
# ("2h hoy: reunión con cliente") - Telegram no tiene forma de mandar
# un <select>, así que la subtarea se elige con botones inline en un
# segundo paso. Mientras se espera esa elección (o las horas, si el
# registro arrancó tocando un botón de "día sin cargar"), el estado
# pendiente vive en PENDIENTES_TELEGRAM, en memoria del proceso - igual
# que _intentos_login más arriba: el Procfile corre un único worker de
# gunicorn, así que no hace falta compartir esto entre procesos. Si el
# server se reinicia a mitad de un registro simplemente se pierde y hay
# que volver a escribir, no es grave.

PENDIENTES_TELEGRAM = {}


def telegram_enviar_mensaje(chat_id, texto, teclado=None):
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        return
    payload = {"chat_id": chat_id, "text": texto}
    if teclado:
        payload["reply_markup"] = {"inline_keyboard": teclado}
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json=payload,
            timeout=10,
        )
    except requests.RequestException:
        pass  # el peor caso es que no llegue el mensaje, no vale la pena reintentar acá


def telegram_editar_mensaje(chat_id, message_id, texto):
    if not TELEGRAM_BOT_TOKEN or not message_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText",
            json={"chat_id": chat_id, "message_id": message_id, "text": texto},
            timeout=10,
        )
    except requests.RequestException:
        pass


def telegram_responder_callback(callback_id, texto=None):
    if not TELEGRAM_BOT_TOKEN:
        return
    payload = {"callback_query_id": callback_id, **({"text": texto} if texto else {})}
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
            json=payload,
            timeout=10,
        )
    except requests.RequestException:
        pass


def _quitar_acentos(texto):
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


_RE_HORAS = re.compile(r"(\d+(?:[.,]\d+)?)\s*h(?:s\.?|oras?)?\b", re.IGNORECASE)
_RE_FECHA_DDMM = re.compile(r"\b(\d{1,2})[/-](\d{1,2})\b")
_DISPARADORES_FALTANTES = (
    "falta", "sin cargar", "sin subir", "sin horas",
    "no he subido", "no subi", "no cargue", "no cargu", "no cargado",
    "dias sin", "que dias", "que dia",
)
_DISPARADORES_RESUMEN = ("resum", "semana", "mes", "cuant", "llevo", "total")


def _parsear_horas(texto):
    """Busca un patrón tipo '2h' o '1,5 horas' en el texto.
    Devuelve (horas, texto_sin_ese_pedazo) o (None, texto) si no encuentra nada."""
    m = _RE_HORAS.search(texto)
    if not m:
        return None, texto
    horas = float(m.group(1).replace(",", "."))
    resto = (texto[:m.start()] + texto[m.end():]).strip(" :-,.")
    return horas, resto


def _parsear_fecha(texto):
    """Busca 'hoy', 'ayer' o una fecha dd/mm en el texto.
    Devuelve (fecha_iso, texto_sin_ese_pedazo); si no encuentra nada, asume hoy."""
    sin_acentos = _quitar_acentos(texto.lower())

    m = re.search(r"\bhoy\b", sin_acentos)
    if m:
        return date.today().isoformat(), (texto[:m.start()] + texto[m.end():]).strip(" :-,.")

    m = re.search(r"\bayer\b", sin_acentos)
    if m:
        ayer = date.today() - timedelta(days=1)
        return ayer.isoformat(), (texto[:m.start()] + texto[m.end():]).strip(" :-,.")

    m = _RE_FECHA_DDMM.search(texto)
    if m:
        try:
            f = date(date.today().year, int(m.group(2)), int(m.group(1)))
            return f.isoformat(), (texto[:m.start()] + texto[m.end():]).strip(" :-,.")
        except ValueError:
            pass  # "32/13" y similares - se ignora y se sigue con hoy

    return date.today().isoformat(), texto


def _teclado_subtareas(tarjeta):
    return [
        [{"text": t["name"][:60], "callback_data": f"subtarea:{t['id']}"}]
        for t in subtareas_de_tarjeta(tarjeta)
    ]


def _texto_ayuda():
    return (
        "Hola 👋 Puedo ayudarte con:\n"
        "/resumen — horas de esta semana y este mes\n"
        "/faltantes — días hábiles sin cargar\n"
        "\"2h hoy: reunión con cliente\" — registra horas (elegís la subtarea con botones)\n"
        "/desvincular — deja de usar el bot con tu cuenta actual"
    )


def _texto_resumen(tarjeta):
    r = _calcular_resumen(tarjeta)
    detalle = "\n".join(f"- {s['subtarea']}: {s['horas']:.1f}h" for s in r["por_subtarea"])
    detalle = detalle or "(sin horas cargadas esta semana)"
    return f"📊 Esta semana: {r['semana']:.1f}h · Este mes: {r['mes']:.1f}h\n\n{detalle}"


def _texto_faltantes(tarjeta):
    """Devuelve (texto, teclado): un botón por día sin cargar que arranca
    el registro con esa fecha ya puesta (ver _manejar_callback_telegram)."""
    dias = dias_habiles_atras(10)
    task_ids = subtareas_ids_de_tarjeta(tarjeta)
    if not task_ids:
        return "No encontré subtareas para tu tarjeta.", None
    fecha_min = min(dias).isoformat()
    fecha_max = max(dias).isoformat()
    lineas = odoo_execute_kw(
        "account.analytic.line", "search_read",
        [[["task_id", "in", task_ids], ["date", ">=", fecha_min], ["date", "<=", fecha_max]]],
        {"fields": ["date"]},
    )
    con_horas = {l["date"] for l in lineas}
    faltantes = sorted(d for d in dias if d.isoformat() not in con_horas)
    if not faltantes:
        return "✅ No te falta cargar ningún día hábil de los últimos 10.", None
    listado = "\n".join("- " + d.strftime("%d/%m") for d in faltantes)
    teclado = [
        [{"text": "Cargar " + d.strftime("%d/%m"), "callback_data": "faltante:" + d.isoformat()}]
        for d in faltantes
    ]
    return f"📋 Días hábiles sin horas cargadas (últimos 10):\n{listado}", teclado


def _procesar_mensaje_telegram(texto, chat_id, tarjeta):
    """Interpreta un mensaje de texto libre. Devuelve (texto_respuesta, teclado_o_None)."""
    pendiente = PENDIENTES_TELEGRAM.get(chat_id)

    if pendiente and pendiente["etapa"] == "esperando_horas":
        horas, detalle = _parsear_horas(texto)
        if horas is None:
            return "No entendí las horas. Mandame algo como '2h reunión con cliente'.", None
        teclado = _teclado_subtareas(tarjeta)
        if not teclado:
            del PENDIENTES_TELEGRAM[chat_id]
            return "No encontré subtareas para tu tarjeta.", None
        PENDIENTES_TELEGRAM[chat_id] = {
            "etapa": "elegir_subtarea", "fecha": pendiente["fecha"], "horas": horas, "detalle": detalle,
        }
        return "¿En qué subtarea? 👇", teclado

    normalizado = _quitar_acentos(texto.lower().strip())

    if _RE_HORAS.search(texto):
        fecha, resto = _parsear_fecha(texto)
        horas, detalle = _parsear_horas(resto)
        if horas is None:  # la fecha se comió el número por algún solape raro - probamos de nuevo sobre el texto original
            horas, detalle = _parsear_horas(texto)
        teclado = _teclado_subtareas(tarjeta)
        if not teclado:
            return "No encontré subtareas para tu tarjeta.", None
        PENDIENTES_TELEGRAM[chat_id] = {"etapa": "elegir_subtarea", "fecha": fecha, "horas": horas, "detalle": detalle}
        return "¿En qué subtarea? 👇", teclado

    if any(p in normalizado for p in _DISPARADORES_FALTANTES):
        return _texto_faltantes(tarjeta)

    if any(p in normalizado for p in _DISPARADORES_RESUMEN):
        return _texto_resumen(tarjeta), None

    return "No entendí. " + _texto_ayuda(), None


def _manejar_vincular(texto, chat_id):
    """Comando /vincular <usuario> <contraseña>: valida contra usuarios.db
    (igual que /api/login) y guarda el mapeo chat_id→username. Protegido
    contra fuerza bruta con el mismo mecanismo de bloqueo que el login web,
    en un diccionario separado keyado por chat_id."""
    restante = _segundos_bloqueado(_intentos_vincular_telegram, chat_id)
    if restante > 0:
        minutos = int(restante // 60) + 1
        telegram_enviar_mensaje(chat_id, f"Demasiados intentos fallidos. Probá de nuevo en {minutos} min.")
        return

    partes = texto.split(maxsplit=2)
    if len(partes) < 3:
        telegram_enviar_mensaje(chat_id, "Uso: /vincular <usuario> <contraseña>")
        return

    username = partes[1].strip().lower()
    password = partes[2]
    usuario = obtener_usuario(username)
    if not usuario or not check_password_hash(usuario["password_hash"], password):
        _registrar_intento_fallido(_intentos_vincular_telegram, chat_id)
        telegram_enviar_mensaje(chat_id, "Usuario o contraseña incorrectos.")
        return

    _limpiar_intentos(_intentos_vincular_telegram, chat_id)
    vincular_telegram(chat_id, username)
    PENDIENTES_TELEGRAM.pop(chat_id, None)  # por si el chat ya tenía un registro a medio hacer con la cuenta anterior
    telegram_enviar_mensaje(chat_id, f"Listo, vinculé este chat a la cuenta '{username}' ✅\n\n" + _texto_ayuda())


def _manejar_callback_telegram(callback):
    mensaje = callback.get("message") or {}
    chat_id = str((mensaje.get("chat") or {}).get("id", ""))
    message_id = mensaje.get("message_id")
    callback_id = callback.get("id")
    data = callback.get("data") or ""

    usuario = usuario_vinculado(chat_id)
    if not usuario:
        telegram_responder_callback(callback_id, "Vinculá tu cuenta primero con /vincular.")
        return
    tarjeta = usuario["tarjeta"]

    if data.startswith("faltante:"):
        fecha = data.split(":", 1)[1]
        PENDIENTES_TELEGRAM[chat_id] = {"etapa": "esperando_horas", "fecha": fecha}
        telegram_responder_callback(callback_id)
        bonita = datetime.strptime(fecha, "%Y-%m-%d").strftime("%d/%m")
        telegram_editar_mensaje(chat_id, message_id, f"🗓️ {bonita} — mandame las horas y la descripción, ej. '2h reunión con cliente'.")
        return

    if data.startswith("subtarea:"):
        pendiente = PENDIENTES_TELEGRAM.get(chat_id)
        if not pendiente or pendiente.get("etapa") != "elegir_subtarea":
            telegram_responder_callback(callback_id, "Se perdió el contexto del registro, empezá de nuevo.")
            return
        try:
            _crear_linea_timesheet(int(data.split(":", 1)[1]), pendiente["fecha"], pendiente["horas"], pendiente["detalle"])
        except Exception as e:
            print(f"[telegram] error creando línea desde el bot: {e}", file=sys.stderr)
            telegram_responder_callback(callback_id, "No se pudo registrar, hubo un error con Odoo.")
            return
        del PENDIENTES_TELEGRAM[chat_id]
        telegram_responder_callback(callback_id, "Registrado ✅")
        bonita = datetime.strptime(pendiente["fecha"], "%Y-%m-%d").strftime("%d/%m")
        telegram_editar_mensaje(chat_id, message_id, f"✅ {pendiente['horas']:.2f}h el {bonita} — cargado en Odoo.")
        return

    telegram_responder_callback(callback_id)


@app.route("/api/telegram-webhook", methods=["POST"])
def telegram_webhook():
    if not TELEGRAM_WEBHOOK_SECRET or request.headers.get("X-Telegram-Bot-Api-Secret-Token") != TELEGRAM_WEBHOOK_SECRET:
        return jsonify({"error": "no encontrado"}), 404

    update = request.get_json(silent=True) or {}

    if "callback_query" in update:
        _manejar_callback_telegram(update["callback_query"])
        return jsonify({"ok": True})

    mensaje = update.get("message") or update.get("edited_message") or {}
    texto = (mensaje.get("text") or "").strip()
    chat_id = str((mensaje.get("chat") or {}).get("id", ""))

    # Siempre 200: Telegram reintenta con backoff si no le contestamos
    # 2xx, y no queremos reintentos por mensajes que decidimos ignorar.
    if not texto or not chat_id:
        return jsonify({"ok": True})

    comando = texto.lower().strip().split("@")[0]  # "/resumen@mi_bot" -> "/resumen", por si Telegram lo agrega

    if comando.startswith("/vincular"):
        _manejar_vincular(texto, chat_id)
        return jsonify({"ok": True})

    if comando in ("/desvincular", "/unlink"):
        ok = desvincular_telegram(chat_id)
        PENDIENTES_TELEGRAM.pop(chat_id, None)
        telegram_enviar_mensaje(chat_id, "Cuenta desvinculada." if ok else "No tenías ninguna cuenta vinculada.")
        return jsonify({"ok": True})

    usuario = usuario_vinculado(chat_id)
    if not usuario:
        telegram_enviar_mensaje(chat_id, "No vinculé este chat a ninguna cuenta todavía. Mandá /vincular <usuario> <contraseña> primero.")
        return jsonify({"ok": True})
    tarjeta = usuario["tarjeta"]

    try:
        if comando.startswith("/"):
            PENDIENTES_TELEGRAM.pop(chat_id, None)  # un comando explícito cancela cualquier registro a medio hacer

        if comando in ("/start", "/ayuda", "/help", "ayuda", "help"):
            respuesta, teclado = _texto_ayuda(), None
        elif comando == "/resumen":
            respuesta, teclado = _texto_resumen(tarjeta), None
        elif comando == "/faltantes":
            respuesta, teclado = _texto_faltantes(tarjeta)
        elif comando == "/registrar":
            respuesta, teclado = "Mandame algo como '2h hoy: reunión con cliente' y elegís la subtarea con botones.", None
        else:
            respuesta, teclado = _procesar_mensaje_telegram(texto, chat_id, tarjeta)
    except Exception as e:
        print(f"[telegram] error procesando mensaje: {e}", file=sys.stderr)
        respuesta, teclado = "Tuve un problema consultando Odoo. Probá de nuevo en un rato; si sigue, revisá los logs del backend.", None

    telegram_enviar_mensaje(chat_id, respuesta, teclado)
    return jsonify({"ok": True})


@app.route("/api/dias-cargados", methods=["GET"])
def dias_cargados():
    """
    Uso: /api/dias-cargados?dias=30
    Total de horas cargadas por día calendario en los últimos N días
    (incluye hoy), para la tarjeta del usuario. Pensado para un
    heatmap tipo calendario - los días sin horas no vienen en la
    lista (se asumen 0 en el frontend).
    """
    tarjeta = tarjeta_de_la_request(request.args)
    n = int(request.args.get("dias", 30))
    hoy = date.today()
    desde = hoy - timedelta(days=n - 1)

    task_ids = subtareas_ids_de_tarjeta(tarjeta)
    if not task_ids:
        return jsonify({"dias": []})

    lineas = odoo_execute_kw(
        "account.analytic.line", "search_read",
        [[["task_id", "in", task_ids], ["date", ">=", desde.isoformat()], ["date", "<=", hoy.isoformat()]]],
        {"fields": ["date", "unit_amount"]},
    )
    totales = {}
    for l in lineas:
        totales[l["date"]] = totales.get(l["date"], 0) + l["unit_amount"]

    return jsonify({"dias": [{"fecha": f, "horas": h} for f, h in totales.items()]})


def _crear_linea_timesheet(task_id, fecha, horas, detalle):
    """Crea la línea en account.analytic.line para una subtarea ya resuelta
    (por id). Usado tanto por /api/timesheet (que resuelve el id a partir
    de un nombre de subtarea) como por el bot de Telegram (que ya tiene el
    id porque lo sacó de un botón)."""
    tarea = odoo_execute_kw("project.task", "read", [[task_id]], {"fields": ["project_id", "name"]})[0]
    employee_id = obtener_employee_de_tarea(task_id)
    return odoo_execute_kw(
        "account.analytic.line", "create",
        [{
            "name": detalle or tarea["name"],
            "date": fecha,
            "unit_amount": horas,
            "project_id": tarea["project_id"][0],
            "task_id": task_id,
            "employee_id": employee_id,
        }],
    )


@app.route("/api/timesheet", methods=["POST"])
def crear_timesheet():
    data = request.get_json()
    tarjeta = tarjeta_de_la_request(data)

    subtarea = data.get("subtarea")
    fecha = data.get("fecha")
    horas = data.get("horas")
    detalle = data.get("detalle", "")

    if not (subtarea and fecha and horas):
        return jsonify({"error": "faltan campos requeridos"}), 400
    if not _fecha_valida(fecha):
        return jsonify({"error": "fecha inválida (usar AAAA-MM-DD)"}), 400
    horas = _validar_horas(horas)
    if horas is None:
        return jsonify({"error": "horas inválidas (debe ser un número mayor a 0)"}), 400

    task_id = buscar_tarea_id(subtarea, tarjeta)
    if not task_id:
        return jsonify({"error": f"no se encontro la subtarea '{subtarea}' en la tarjeta '{tarjeta}'"}), 404

    nuevo_id = _crear_linea_timesheet(task_id, fecha, horas, detalle or subtarea)
    return jsonify({"ok": True, "id": nuevo_id})


def _verificar_linea_de_tarjeta(line_id, tarjeta):
    """Devuelve None si la línea existe y pertenece a la tarjeta dada, o una
    respuesta de error (mismo patrón que requiere_admin()) si no - para que
    editar_timesheet y borrar_timesheet no dupliquen este chequeo."""
    task_ids_tarjeta = subtareas_ids_de_tarjeta(tarjeta)
    linea_actual = odoo_execute_kw(
        "account.analytic.line", "read",
        [[line_id]], {"fields": ["task_id"]},
    )
    if not linea_actual:
        return jsonify({"error": "no existe esa línea"}), 404
    if linea_actual[0]["task_id"][0] not in task_ids_tarjeta:
        return jsonify({"error": "esa línea no pertenece a tu tarjeta"}), 403
    return None


@app.route("/api/timesheet/<int:line_id>", methods=["PUT"])
def editar_timesheet(line_id):
    data = request.get_json()
    tarjeta = tarjeta_de_la_request(data)

    error = _verificar_linea_de_tarjeta(line_id, tarjeta)
    if error:
        return error

    valores = {}

    nueva_subtarea = data.get("subtarea")
    if nueva_subtarea:
        nuevo_task_id = buscar_tarea_id(nueva_subtarea, tarjeta)
        if not nuevo_task_id:
            return jsonify({"error": f"no se encontro la subtarea '{nueva_subtarea}' en la tarjeta '{tarjeta}'"}), 404
        tarea = odoo_execute_kw("project.task", "read", [[nuevo_task_id]], {"fields": ["project_id"]})[0]
        valores["task_id"] = nuevo_task_id
        valores["project_id"] = tarea["project_id"][0]
        valores["employee_id"] = obtener_employee_de_tarea(nuevo_task_id)

    if data.get("fecha"):
        if not _fecha_valida(data["fecha"]):
            return jsonify({"error": "fecha inválida (usar AAAA-MM-DD)"}), 400
        valores["date"] = data["fecha"]
    if data.get("horas") is not None:
        horas_validas = _validar_horas(data["horas"])
        if horas_validas is None:
            return jsonify({"error": "horas inválidas (debe ser un número mayor a 0)"}), 400
        valores["unit_amount"] = horas_validas
    if "detalle" in data:
        valores["name"] = data["detalle"]

    if not valores:
        return jsonify({"error": "nada para actualizar"}), 400

    ok = odoo_execute_kw("account.analytic.line", "write", [[line_id], valores])
    return jsonify({"ok": ok})


@app.route("/api/timesheet/<int:line_id>", methods=["DELETE"])
def borrar_timesheet(line_id):
    tarjeta = tarjeta_de_la_request(request.args)

    error = _verificar_linea_de_tarjeta(line_id, tarjeta)
    if error:
        return error

    ok = odoo_execute_kw("account.analytic.line", "unlink", [[line_id]])
    return jsonify({"ok": ok})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
