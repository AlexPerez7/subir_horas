"""
Backend minimo (Flask) que recibe las entradas del formulario y las
crea en Odoo como lineas de parte de horas (account.analytic.line).

Usa JSON-RPC (no XML-RPC), porque el ODOO_URL de este proyecto ya
apunta al endpoint /jsonrpc (asi esta configurado en Assertiva).

Instalar dependencias:
    pip install flask flask-cors python-dotenv requests

Variables de entorno esperadas (tu propio .env, separado del de tu
companero, con los mismos valores URL/DB/UID/TOKEN):

    ODOO_URL=https://www.assertiva.biz/jsonrpc
    ODOO_DB=origami-soft-assertiva-main-...
    ODOO_UID=429
    ODOO_TOKEN=xxxxxxxxxxxxxxxx
"""

import os
import sys
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

if getattr(sys, "frozen", False):
    _CARPETA = os.path.dirname(sys.executable)
else:
    _CARPETA = os.path.dirname(os.path.abspath(__file__))

load_dotenv(os.path.join(_CARPETA, ".env"))

ODOO_URL = os.environ["ODOO_URL"]  # debe terminar en /jsonrpc
ODOO_DB = os.environ["ODOO_DB"]
ODOO_UID = int(os.environ["ODOO_UID"])
ODOO_TOKEN = os.environ["ODOO_TOKEN"]

app = Flask(__name__)
CORS(app)  # en produccion, restringir a tu propio origen

PROJECT_NAME = "GER_Produccion Varios NF"
MI_TARJETA = "Alex Perez"  # valor por defecto si el formulario no especifica ninguna


def odoo_execute_kw(model, method, args, kwargs=None):
    """Llama a execute_kw vía JSON-RPC 2.0 (no XML-RPC)."""
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
        # Odoo devuelve el detalle real del error acá (permisos, campo
        # inexistente, etc.) - muy útil para debug.
        raise RuntimeError(data["error"])
    return data["result"]


def obtener_project_ids():
    return odoo_execute_kw(
        "project.project",
        "search",
        [[["name", "=", PROJECT_NAME]]],
    )


def buscar_tarea_id(nombre_subtarea, tarjeta):
    """Busca la subtarea por nombre, restringida a una tarjeta (tarea padre) específica."""
    project_ids = obtener_project_ids()
    task_ids = odoo_execute_kw(
        "project.task",
        "search",
        [
            [
                ["name", "=", nombre_subtarea],
                ["project_id", "in", project_ids],
                ["parent_id.name", "=", tarjeta],
            ]
        ],
        {"limit": 1},
    )
    return task_ids[0] if task_ids else None


def obtener_employee_de_tarea(task_id):
    """
    Resuelve el hr.employee a partir de quién está asignado a la
    subtarea (no de quién llama a la API) - así funciona en el flujo:
    cada tarjeta/subtarea tiene asignada a una persona específica, y
    eso es lo que determina el campo Empleado en el parte de horas.
    """
    tarea = odoo_execute_kw(
        "project.task",
        "read",
        [[task_id]],
        {"fields": ["user_ids"]},
    )[0]
    asignados = tarea.get("user_ids") or []
    if not asignados:
        raise RuntimeError(
            f"La tarea {task_id} no tiene ninguna persona asignada (user_ids vacío)."
        )
    user_id = asignados[0]  # si hay más de una persona asignada, toma la primera

    empleados = odoo_execute_kw(
        "hr.employee",
        "search_read",
        [[["user_id", "=", user_id]]],
        {"fields": ["id", "name"], "limit": 1},
    )
    if not empleados:
        raise RuntimeError(
            f"No se encontró un hr.employee vinculado al usuario asignado (user_id={user_id})."
        )
    return empleados[0]["id"]


@app.route("/api/tarjetas", methods=["GET"])
def listar_tarjetas():
    """
    Lista las tarjetas principales del proyecto (tareas sin padre,
    típicamente una por persona: Alex Perez, Felipe Acevedo, etc.).
    Sirve para poblar el selector de "a nombre de quién" en el formulario.
    """
    project_ids = obtener_project_ids()
    tarjetas = odoo_execute_kw(
        "project.task",
        "search_read",
        [
            [
                ["project_id", "in", project_ids],
                ["parent_id", "=", False],
            ]
        ],
        {"fields": ["id", "name"]},
    )
    return jsonify(tarjetas)


@app.route("/api/subtareas", methods=["GET"])
def listar_subtareas():
    """
    Uso: /api/subtareas?tarjeta=Alex%20Perez
    Si no se pasa 'tarjeta', usa MI_TARJETA por defecto.
    """
    tarjeta = request.args.get("tarjeta", MI_TARJETA)
    project_ids = obtener_project_ids()
    tareas = odoo_execute_kw(
        "project.task",
        "search_read",
        [
            [
                ["project_id", "in", project_ids],
                ["parent_id.name", "=", tarjeta],
            ]
        ],
        {"fields": ["id", "name"]},
    )
    return jsonify(tareas)


@app.route("/api/campos", methods=["GET"])
def listar_campos():
    """
    Descubre los nombres tecnicos reales de los campos de un modelo.

    Uso: /api/campos                                -> account.analytic.line, todos los campos
         /api/campos?modelo=project.task&q=user      -> busca "user" en project.task
         /api/campos?q=solicit                       -> busca "solicit" en account.analytic.line
    """
    modelo = request.args.get("modelo", "account.analytic.line")
    campos = odoo_execute_kw(
        modelo,
        "fields_get",
        [],
        {"attributes": ["string", "type"]},
    )

    q = request.args.get("q", "").strip().lower()
    if q:
        campos = {
            nombre_tecnico: info
            for nombre_tecnico, info in campos.items()
            if q in nombre_tecnico.lower() or q in info.get("string", "").lower()
        }

    return jsonify(campos)


@app.route("/api/timesheet/recientes", methods=["GET"])
def timesheet_recientes():
    """
    Uso: /api/timesheet/recientes?tarjeta=Alex Perez&subtarea=CyberArk - Koandina&limite=8
    Devuelve las últimas líneas reales de account.analytic.line para esa
    subtarea, tal como se ven en la pestaña "Partes de horas" de Odoo,
    más el total acumulado de horas.
    """
    tarjeta = request.args.get("tarjeta", MI_TARJETA)
    subtarea = request.args.get("subtarea")
    limite = int(request.args.get("limite", 8))

    if not subtarea:
        return jsonify({"error": "falta el parámetro subtarea"}), 400

    task_id = buscar_tarea_id(subtarea, tarjeta)
    if not task_id:
        return (
            jsonify(
                {
                    "error": f"no se encontro la subtarea '{subtarea}' en la tarjeta '{tarjeta}'"
                }
            ),
            404,
        )

    lineas = odoo_execute_kw(
        "account.analytic.line",
        "search_read",
        [[["task_id", "=", task_id]]],
        {
            "fields": ["date", "name", "unit_amount", "employee_id"],
            "order": "date desc, id desc",
            "limit": limite,
        },
    )

    todas = odoo_execute_kw(
        "account.analytic.line",
        "search_read",
        [[["task_id", "=", task_id]]],
        {"fields": ["unit_amount"]},
    )
    total_horas = sum(l["unit_amount"] for l in todas)

    return jsonify({"lineas": lineas, "total_horas": total_horas})


@app.route("/api/timesheet", methods=["POST"])
def crear_timesheet():
    data = request.get_json()

    tarjeta = data.get("tarjeta") or MI_TARJETA
    subtarea = data.get("subtarea")
    fecha = data.get("fecha")
    horas = data.get("horas")
    detalle = data.get("detalle", "")

    if not (subtarea and fecha and horas):
        return jsonify({"error": "faltan campos requeridos"}), 400

    task_id = buscar_tarea_id(subtarea, tarjeta)
    if not task_id:
        return (
            jsonify(
                {
                    "error": f"no se encontro la subtarea '{subtarea}' en la tarjeta '{tarjeta}'"
                }
            ),
            404,
        )

    tarea = odoo_execute_kw(
        "project.task",
        "read",
        [[task_id]],
        {"fields": ["project_id"]},
    )[0]
    project_id = tarea["project_id"][0]

    employee_id = obtener_employee_de_tarea(task_id)

    nuevo_id = odoo_execute_kw(
        "account.analytic.line",
        "create",
        [
            {
                "name": detalle or subtarea,
                "date": fecha,
                "unit_amount": horas,
                "project_id": project_id,
                "task_id": task_id,
                "employee_id": employee_id,
            }
        ],
    )

    return jsonify({"ok": True, "id": nuevo_id})


@app.route("/api/timesheet/<int:line_id>", methods=["DELETE"])
def borrar_timesheet(line_id):
    """Elimina una línea de parte de horas por id. Útil para limpiar pruebas."""
    ok = odoo_execute_kw("account.analytic.line", "unlink", [[line_id]])
    return jsonify({"ok": ok})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
