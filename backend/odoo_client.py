"""
Cliente JSON-RPC de Odoo y resoluciones de datos (project_ids, subtareas de
una tarjeta, id de tarea por nombre, empleado asignado a una tarea) con
caché en memoria de TTL corto - project.task/hr.employee cambian con poca
frecuencia (los edita un admin directo en Odoo), así que 60s de staleness
es un costo aceptable a cambio de no repetir 3-4 llamadas JSON-RPC por cada
carga de página o cada línea de una carga en lote. Mismo criterio que los
diccionarios en memoria de auth.py/telegram_bot.py: alcanza con memoria del
proceso porque el Procfile corre un único worker.
"""

import time

import requests

from . import config


def odoo_execute_kw(model, method, args, kwargs=None):
    payload = {
        "jsonrpc": "2.0",
        "method": "call",
        "params": {
            "service": "object",
            "method": "execute_kw",
            "args": [config.ODOO_DB, config.ODOO_UID, config.ODOO_TOKEN, model, method, args, kwargs or {}],
        },
        "id": 1,
    }
    resp = requests.post(config.ODOO_URL, json=payload, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(data["error"])
    return data["result"]


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
        [[["name", "=", config.PROJECT_NAME]]],
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
