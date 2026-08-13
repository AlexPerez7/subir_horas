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
"""

import os
import sqlite3
from datetime import date, timedelta

import requests
from flask import Flask, request, jsonify, g
from flask_cors import CORS
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
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
TOKEN_LIFETIME_SEGUNDOS = int(os.environ.get("SESSION_LIFETIME_HORAS", 8)) * 3600
CRON_SECRET = os.environ.get("CRON_SECRET", "")

app = Flask(__name__, static_folder=None)
app.secret_key = os.environ["SECRET_KEY"]
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


RUTAS_PUBLICAS = ("/", "/api/login", "/api/recordatorio-cron")


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


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip().lower()
    password = data.get("password", "")

    usuario = obtener_usuario(username)
    if not usuario or not check_password_hash(usuario["password_hash"], password):
        return jsonify({"error": "Usuario o contraseña incorrectos."}), 401

    return jsonify({
        "ok": True,
        "token": generar_token(usuario),
        "username": usuario["username"],
        "tarjeta": usuario["tarjeta"],
        "es_admin": bool(usuario["es_admin"]),
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
    return jsonify({"ok": True})


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


@app.route("/api/resumen", methods=["GET"])
def resumen_horas():
    """
    Total de horas cargadas en la semana actual (lunes a domingo) y en
    el mes actual (día 1 al último), para la tarjeta del usuario en
    sesión. Se pide un único rango que cubre ambos períodos y se separa
    en Python, porque cuando la semana actual cruza fin/inicio de mes
    los dos rangos no son el uno subconjunto del otro.
    """
    tarjeta = g.usuario["tarjeta"]
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
        return jsonify({"semana": 0, "mes": 0})

    fecha_min = min(inicio_semana, inicio_mes).isoformat()
    fecha_max = max(fin_semana, fin_mes).isoformat()

    lineas = odoo_execute_kw(
        "account.analytic.line", "search_read",
        [[["task_id", "in", task_ids], ["date", ">=", fecha_min], ["date", "<=", fecha_max]]],
        {"fields": ["date", "unit_amount"]},
    )

    inicio_semana_iso, fin_semana_iso = inicio_semana.isoformat(), fin_semana.isoformat()
    inicio_mes_iso, fin_mes_iso = inicio_mes.isoformat(), fin_mes.isoformat()

    total_semana = sum(l["unit_amount"] for l in lineas if inicio_semana_iso <= l["date"] <= fin_semana_iso)
    total_mes = sum(l["unit_amount"] for l in lineas if inicio_mes_iso <= l["date"] <= fin_mes_iso)

    return jsonify({"semana": total_semana, "mes": total_mes})


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
    tarjeta = tarjeta_de_la_request(request.args)

    # Verificar que la línea pertenece a la tarjeta del usuario antes de borrarla
    task_ids_tarjeta = subtareas_ids_de_tarjeta(tarjeta)
    linea_actual = odoo_execute_kw(
        "account.analytic.line", "read",
        [[line_id]], {"fields": ["task_id"]},
    )
    if not linea_actual:
        return jsonify({"error": "no existe esa línea"}), 404
    if linea_actual[0]["task_id"][0] not in task_ids_tarjeta:
        return jsonify({"error": "esa línea no pertenece a tu tarjeta"}), 403

    ok = odoo_execute_kw("account.analytic.line", "unlink", [[line_id]])
    return jsonify({"ok": ok})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
