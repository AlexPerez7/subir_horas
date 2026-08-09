"""
Backend Flask (API JSON pura) que habla con Odoo y gestiona el login
propio de la app. Se despliega aparte del frontend (que vive como
sitio estatico en GitHub Pages) - por eso todo acá es JSON, nada de
paginas server-rendered, y CORS/cookies estan configurados para
funcionar cross-origin.

Autenticacion por sesion web (login propio). Cada usuario esta
asociado a una tarjeta especifica de Odoo (definida al crear su
cuenta con crear_usuario.py) - el backend nunca confia en una
"tarjeta" que venga del navegador para usuarios normales, salvo que
la cuenta sea de administrador.

Instalar dependencias:
    pip install -r requirements.txt

Variables de entorno esperadas (.env, NUNCA subir a git; ver .env.example):

    ODOO_URL=https://www.assertiva.biz/jsonrpc
    ODOO_DB=origami-soft-assertiva-main-...
    ODOO_UID=429
    ODOO_TOKEN=xxxxxxxxxxxxxxxx

    SECRET_KEY=<cadena larga y aleatoria, para firmar las cookies de sesion>
    SESSION_LIFETIME_HORAS=8   (opcional, default 8)

    FRONTEND_ORIGINS=https://tu-usuario.github.io   (lista separada por comas)
    COOKIE_SECURE=true    (poner "false" solo en desarrollo local sin HTTPS)
    COOKIE_SAMESITE=None  (poner "Lax" en desarrollo local, ver README)
"""

import os
import sqlite3
from datetime import date, timedelta

import requests
from flask import Flask, request, jsonify, session
from flask_cors import CORS
from werkzeug.security import check_password_hash, generate_password_hash
from dotenv import load_dotenv

_CARPETA = os.path.dirname(os.path.abspath(__file__))

load_dotenv(os.path.join(_CARPETA, ".env"))

ODOO_URL = os.environ["ODOO_URL"]
ODOO_DB = os.environ["ODOO_DB"]
ODOO_UID = int(os.environ["ODOO_UID"])
ODOO_TOKEN = os.environ["ODOO_TOKEN"]

DB_PATH = os.path.join(_CARPETA, "usuarios.db")

FRONTEND_ORIGINS = [o.strip() for o in os.environ.get("FRONTEND_ORIGINS", "").split(",") if o.strip()]

app = Flask(__name__, static_folder=None)
app.secret_key = os.environ["SECRET_KEY"]
CORS(app, supports_credentials=True, origins=FRONTEND_ORIGINS)

# Expiración de sesión por inactividad: cada request "renueva" el
# contador (comportamiento por defecto de Flask), así que esto
# equivale a "cerrar sesión sola tras X horas sin actividad".
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=int(os.environ.get("SESSION_LIFETIME_HORAS", 8)))

# En producción (frontend en GitHub Pages, backend en Render) la
# cookie viaja cross-site: necesita SameSite=None + Secure=True. En
# desarrollo local (frontend y backend en localhost, distinto
# puerto) son "same-site" así que alcanza con Lax/no-Secure - ver
# README, sección "Modo desarrollo".
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE", "true").strip().lower() != "false"
app.config["SESSION_COOKIE_SAMESITE"] = os.environ.get("COOKIE_SAMESITE", "None")
app.config["SESSION_COOKIE_HTTPONLY"] = True

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
    con.commit()
    con.close()


_inicializar_db()


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


# --------------------------------------------------------------------
# Autenticación por sesión (todo JSON - el frontend es una SPA aparte)
# --------------------------------------------------------------------

RUTAS_PUBLICAS = ("/", "/api/login", "/api/logout")


@app.before_request
def proteger_todo():
    if request.method == "OPTIONS":
        return  # preflight CORS
    if request.path in RUTAS_PUBLICAS:
        return
    if "username" not in session:
        return jsonify({"error": "no autenticado"}), 401


@app.route("/")
def health():
    return jsonify({"status": "ok", "service": "registro-horas-backend"})


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip().lower()
    password = data.get("password", "")

    usuario = obtener_usuario(username)
    if not usuario or not check_password_hash(usuario["password_hash"], password):
        return jsonify({"error": "Usuario o contraseña incorrectos."}), 401

    session.permanent = True  # activa PERMANENT_SESSION_LIFETIME (expira tras inactividad)
    session["username"] = usuario["username"]
    session["tarjeta"] = usuario["tarjeta"]
    session["es_admin"] = bool(usuario["es_admin"])
    return jsonify({
        "ok": True,
        "username": usuario["username"],
        "tarjeta": usuario["tarjeta"],
        "es_admin": bool(usuario["es_admin"]),
    })


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/cambiar-password", methods=["POST"])
def cambiar_password():
    data = request.get_json(silent=True) or {}
    actual = data.get("actual", "")
    nueva = data.get("nueva", "")
    confirmar = data.get("confirmar", "")

    usuario = obtener_usuario(session["username"])
    if not check_password_hash(usuario["password_hash"], actual):
        return jsonify({"error": "La contraseña actual no es correcta."}), 400
    if len(nueva) < 6:
        return jsonify({"error": "La contraseña nueva debe tener al menos 6 caracteres."}), 400
    if nueva != confirmar:
        return jsonify({"error": "Las contraseñas nuevas no coinciden."}), 400

    actualizar_password(session["username"], generate_password_hash(nueva))
    return jsonify({"ok": True})


@app.route("/api/whoami", methods=["GET"])
def whoami():
    return jsonify({
        "username": session.get("username"),
        "tarjeta": session.get("tarjeta"),
        "es_admin": session.get("es_admin", False),
    })


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


def obtener_project_ids():
    return odoo_execute_kw(
        "project.project", "search",
        [[["name", "=", PROJECT_NAME]]],
    )


def subtareas_ids_de_tarjeta(tarjeta):
    project_ids = obtener_project_ids()
    return odoo_execute_kw(
        "project.task", "search",
        [[
            ["project_id", "in", project_ids],
            ["parent_id.name", "=", tarjeta],
        ]],
    )


def buscar_tarea_id(nombre_subtarea, tarjeta):
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


def obtener_employee_de_tarea(task_id):
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


def tarjeta_de_la_request(data_o_args):
    tarjeta_sesion = session["tarjeta"]
    if session.get("es_admin"):
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


# --------------------------------------------------------------------
# Endpoints de la API
# --------------------------------------------------------------------

@app.route("/api/tarjetas", methods=["GET"])
def listar_tarjetas():
    if not session.get("es_admin"):
        return jsonify([{"id": None, "name": session["tarjeta"]}])
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
    project_ids = obtener_project_ids()
    tareas = odoo_execute_kw(
        "project.task", "search_read",
        [[
            ["project_id", "in", project_ids],
            ["parent_id.name", "=", tarjeta],
        ]],
        {"fields": ["id", "name"]},
    )
    return jsonify(tareas)


@app.route("/api/campos", methods=["GET"])
def listar_campos():
    if not session.get("es_admin"):
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
    Revisa los últimos N días hábiles (lun-vie) y devuelve cuáles NO
    tienen ninguna hora registrada, para la tarjeta del usuario.
    """
    tarjeta = tarjeta_de_la_request(request.args)
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


@app.route("/api/recordatorio", methods=["GET"])
def recordatorio():
    """
    Avisa si el último día hábil no tiene ninguna hora registrada,
    para la tarjeta del usuario en sesión.
    """
    tarjeta = session["tarjeta"]
    fecha_revisar = dia_habil_anterior(date.today())

    task_ids = subtareas_ids_de_tarjeta(tarjeta)
    if not task_ids:
        return jsonify({"pendiente": False})

    lineas = odoo_execute_kw(
        "account.analytic.line", "search_read",
        [[["task_id", "in", task_ids], ["date", "=", fecha_revisar.isoformat()]]],
        {"fields": ["id"], "limit": 1},
    )
    return jsonify({"pendiente": len(lineas) == 0, "fecha": fecha_revisar.isoformat()})


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

    task_id = buscar_tarea_id(subtarea, tarjeta)
    if not task_id:
        return jsonify({"error": f"no se encontro la subtarea '{subtarea}' en la tarjeta '{tarjeta}'"}), 404

    tarea = odoo_execute_kw("project.task", "read", [[task_id]], {"fields": ["project_id"]})[0]
    project_id = tarea["project_id"][0]
    employee_id = obtener_employee_de_tarea(task_id)

    nuevo_id = odoo_execute_kw(
        "account.analytic.line", "create",
        [{
            "name": detalle or subtarea,
            "date": fecha,
            "unit_amount": horas,
            "project_id": project_id,
            "task_id": task_id,
            "employee_id": employee_id,
        }],
    )
    return jsonify({"ok": True, "id": nuevo_id})


@app.route("/api/timesheet/<int:line_id>", methods=["PUT"])
def editar_timesheet(line_id):
    data = request.get_json()
    tarjeta = tarjeta_de_la_request(data)

    # Verificar que la línea pertenece a la tarjeta del usuario antes de tocarla
    task_ids_tarjeta = subtareas_ids_de_tarjeta(tarjeta)
    linea_actual = odoo_execute_kw(
        "account.analytic.line", "read",
        [[line_id]], {"fields": ["task_id"]},
    )
    if not linea_actual:
        return jsonify({"error": "no existe esa línea"}), 404
    if linea_actual[0]["task_id"][0] not in task_ids_tarjeta:
        return jsonify({"error": "esa línea no pertenece a tu tarjeta"}), 403

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
        valores["date"] = data["fecha"]
    if data.get("horas"):
        valores["unit_amount"] = data["horas"]
    if "detalle" in data:
        valores["name"] = data["detalle"]

    if not valores:
        return jsonify({"error": "nada para actualizar"}), 400

    ok = odoo_execute_kw("account.analytic.line", "write", [[line_id], valores])
    return jsonify({"ok": ok})


@app.route("/api/timesheet/<int:line_id>", methods=["DELETE"])
def borrar_timesheet(line_id):
    ok = odoo_execute_kw("account.analytic.line", "unlink", [[line_id]])
    return jsonify({"ok": ok})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
