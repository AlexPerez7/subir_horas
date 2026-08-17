"""
Todo lo que habla de horas cargadas: catálogo (tarjetas/subtareas/campos),
CRUD de líneas de timesheet, y los cálculos de resumen/recordatorio/días
faltantes (incluidas las variantes -cron para GitHub Actions).
"""

from datetime import date, timedelta

from flask import Blueprint, g, jsonify, request

from .. import config, horas, odoo_client

bp = Blueprint("timesheet_routes", __name__)


@bp.route("/api/tarjetas", methods=["GET"])
def listar_tarjetas():
    if not g.usuario.get("es_admin"):
        return jsonify([{"id": None, "name": g.usuario["tarjeta"]}])
    project_ids = odoo_client.obtener_project_ids()
    tarjetas = odoo_client.odoo_execute_kw(
        "project.task", "search_read",
        [[
            ["project_id", "in", project_ids],
            ["parent_id", "=", False],
        ]],
        {"fields": ["id", "name"]},
    )
    return jsonify(tarjetas)


@bp.route("/api/subtareas", methods=["GET"])
def listar_subtareas():
    tarjeta = horas.tarjeta_de_la_request(request.args)
    return jsonify(odoo_client.subtareas_de_tarjeta(tarjeta))


@bp.route("/api/campos", methods=["GET"])
def listar_campos():
    if not g.usuario.get("es_admin"):
        return jsonify({"error": "solo administradores"}), 403
    modelo = request.args.get("modelo", "account.analytic.line")
    campos = odoo_client.odoo_execute_kw(modelo, "fields_get", [], {"attributes": ["string", "type"]})
    q = request.args.get("q", "").strip().lower()
    if q:
        campos = {k: v for k, v in campos.items() if q in k.lower() or q in v.get("string", "").lower()}
    return jsonify(campos)


@bp.route("/api/timesheet/recientes", methods=["GET"])
def timesheet_recientes():
    tarjeta = horas.tarjeta_de_la_request(request.args)
    subtarea = request.args.get("subtarea")
    limite = int(request.args.get("limite", 8))

    if not subtarea:
        return jsonify({"error": "falta el parámetro subtarea"}), 400

    task_id = odoo_client.buscar_tarea_id(subtarea, tarjeta)
    if not task_id:
        return jsonify({"error": f"no se encontro la subtarea '{subtarea}' en la tarjeta '{tarjeta}'"}), 404

    lineas = odoo_client.odoo_execute_kw(
        "account.analytic.line", "search_read",
        [[["task_id", "=", task_id]]],
        {"fields": ["date", "name", "unit_amount", "employee_id"],
         "order": "date desc, id desc", "limit": limite},
    )
    todas = odoo_client.odoo_execute_kw(
        "account.analytic.line", "search_read",
        [[["task_id", "=", task_id]]],
        {"fields": ["unit_amount"]},
    )
    total_horas = sum(l["unit_amount"] for l in todas)

    return jsonify({"lineas": lineas, "total_horas": total_horas})


@bp.route("/api/timesheet/dia", methods=["GET"])
def timesheet_dia():
    """
    Uso: /api/timesheet/dia?fecha=2026-07-02
    Devuelve todas las líneas de esa fecha, de cualquier subtarea,
    para la tarjeta del usuario (o la indicada, si es admin).
    """
    tarjeta = horas.tarjeta_de_la_request(request.args)
    fecha = request.args.get("fecha")
    if not fecha:
        return jsonify({"error": "falta el parámetro fecha"}), 400

    task_ids = odoo_client.subtareas_ids_de_tarjeta(tarjeta)
    if not task_ids:
        return jsonify({"lineas": [], "total_horas": 0})

    lineas = odoo_client.odoo_execute_kw(
        "account.analytic.line", "search_read",
        [[["task_id", "in", task_ids], ["date", "=", fecha]]],
        {"fields": ["task_id", "name", "unit_amount"], "order": "id asc"},
    )

    ids_unicos = list({l["task_id"][0] for l in lineas})
    nombres = {}
    if ids_unicos:
        tareas = odoo_client.odoo_execute_kw("project.task", "read", [ids_unicos], {"fields": ["name"]})
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


@bp.route("/api/dias-faltantes", methods=["GET"])
def dias_faltantes():
    """
    Uso: /api/dias-faltantes?dias=10
      o: /api/dias-faltantes?desde=2026-08-01
    Con 'dias', revisa los últimos N días hábiles antes de hoy. Con
    'desde', revisa los días hábiles desde esa fecha hasta ayer (p.ej.
    para "días sin cargar horas en lo que va del mes"). Devuelve
    cuáles no tienen ninguna hora registrada, para la tarjeta del usuario.
    """
    tarjeta = horas.tarjeta_de_la_request(request.args)
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
        dias_a_revisar = horas.dias_habiles_atras(n)

    if not dias_a_revisar:
        return jsonify({"faltantes": []})

    task_ids = odoo_client.subtareas_ids_de_tarjeta(tarjeta)
    if not task_ids:
        return jsonify({"faltantes": [d.isoformat() for d in dias_a_revisar]})

    fecha_min = min(dias_a_revisar).isoformat()
    fecha_max = max(dias_a_revisar).isoformat()

    lineas = odoo_client.odoo_execute_kw(
        "account.analytic.line", "search_read",
        [[["task_id", "in", task_ids], ["date", ">=", fecha_min], ["date", "<=", fecha_max]]],
        {"fields": ["date"]},
    )
    dias_con_horas = {l["date"] for l in lineas}

    faltantes = [d.isoformat() for d in dias_a_revisar if d.isoformat() not in dias_con_horas]
    return jsonify({"faltantes": faltantes})


@bp.route("/api/recordatorio", methods=["GET"])
def recordatorio():
    """
    Avisa si el último día hábil no tiene ninguna hora registrada,
    para la tarjeta del usuario en sesión.
    """
    return jsonify(horas._calcular_recordatorio(g.usuario["tarjeta"]))


@bp.route("/api/recordatorio-cron", methods=["GET"])
def recordatorio_cron():
    """
    Igual que /api/recordatorio, pero pensada para un job automático
    (GitHub Actions) sin usuario logueado - protegida por un secreto
    compartido (header X-Cron-Secret) en vez de un token de sesión.
    Revisa la tarjeta de BOOTSTRAP_ADMIN_TARJETA. Si CRON_SECRET no
    está configurado, el endpoint queda deshabilitado (404) en vez de
    aceptar pedidos sin protección.
    """
    if not config.CRON_SECRET or request.headers.get("X-Cron-Secret") != config.CRON_SECRET:
        return jsonify({"error": "no encontrado"}), 404

    tarjeta = config.BOOTSTRAP_ADMIN_TARJETA
    if not tarjeta:
        return jsonify({"error": "no hay BOOTSTRAP_ADMIN_TARJETA configurada"}), 500

    return jsonify(horas._calcular_recordatorio(tarjeta))


@bp.route("/api/resumen", methods=["GET"])
def resumen_horas():
    return jsonify(horas._calcular_resumen(g.usuario["tarjeta"]))


@bp.route("/api/resumen-semanal-cron", methods=["GET"])
def resumen_semanal_cron():
    """
    Igual que /api/recordatorio-cron: pensada para un job automático
    (GitHub Actions) sin usuario logueado, protegida por X-Cron-Secret.
    Devuelve el total de horas de la semana actual para la tarjeta de
    BOOTSTRAP_ADMIN_TARJETA, para mandar un resumen semanal por
    Telegram (ver README).
    """
    if not config.CRON_SECRET or request.headers.get("X-Cron-Secret") != config.CRON_SECRET:
        return jsonify({"error": "no encontrado"}), 404

    tarjeta = config.BOOTSTRAP_ADMIN_TARJETA
    if not tarjeta:
        return jsonify({"error": "no hay BOOTSTRAP_ADMIN_TARJETA configurada"}), 500

    return jsonify(horas._calcular_resumen(tarjeta))


@bp.route("/api/dias-cargados", methods=["GET"])
def dias_cargados():
    """
    Uso: /api/dias-cargados?dias=30
    Total de horas cargadas por día calendario en los últimos N días
    (incluye hoy), para la tarjeta del usuario. Pensado para un
    heatmap tipo calendario - los días sin horas no vienen en la
    lista (se asumen 0 en el frontend).
    """
    tarjeta = horas.tarjeta_de_la_request(request.args)
    n = int(request.args.get("dias", 30))
    hoy = date.today()
    desde = hoy - timedelta(days=n - 1)

    task_ids = odoo_client.subtareas_ids_de_tarjeta(tarjeta)
    if not task_ids:
        return jsonify({"dias": []})

    lineas = odoo_client.odoo_execute_kw(
        "account.analytic.line", "search_read",
        [[["task_id", "in", task_ids], ["date", ">=", desde.isoformat()], ["date", "<=", hoy.isoformat()]]],
        {"fields": ["date", "unit_amount"]},
    )
    totales = {}
    for l in lineas:
        totales[l["date"]] = totales.get(l["date"], 0) + l["unit_amount"]

    return jsonify({"dias": [{"fecha": f, "horas": h} for f, h in totales.items()]})


@bp.route("/api/timesheet", methods=["POST"])
def crear_timesheet():
    data = request.get_json()
    tarjeta = horas.tarjeta_de_la_request(data)

    subtarea = data.get("subtarea")
    fecha = data.get("fecha")
    horas_pedidas = data.get("horas")
    detalle = data.get("detalle", "")

    if not (subtarea and fecha and horas_pedidas):
        return jsonify({"error": "faltan campos requeridos"}), 400
    if not horas._fecha_valida(fecha):
        return jsonify({"error": "fecha inválida (usar AAAA-MM-DD)"}), 400
    horas_validas = horas._validar_horas(horas_pedidas)
    if horas_validas is None:
        return jsonify({"error": "horas inválidas (debe ser un número mayor a 0)"}), 400

    task_id = odoo_client.buscar_tarea_id(subtarea, tarjeta)
    if not task_id:
        return jsonify({"error": f"no se encontro la subtarea '{subtarea}' en la tarjeta '{tarjeta}'"}), 404

    nuevo_id = horas._crear_linea_timesheet(task_id, fecha, horas_validas, detalle or subtarea)
    return jsonify({"ok": True, "id": nuevo_id})


@bp.route("/api/timesheet/<int:line_id>", methods=["PUT"])
def editar_timesheet(line_id):
    data = request.get_json()
    tarjeta = horas.tarjeta_de_la_request(data)

    error = horas._verificar_linea_de_tarjeta(line_id, tarjeta)
    if error:
        return error

    valores = {}

    nueva_subtarea = data.get("subtarea")
    if nueva_subtarea:
        nuevo_task_id = odoo_client.buscar_tarea_id(nueva_subtarea, tarjeta)
        if not nuevo_task_id:
            return jsonify({"error": f"no se encontro la subtarea '{nueva_subtarea}' en la tarjeta '{tarjeta}'"}), 404
        tarea = odoo_client.odoo_execute_kw("project.task", "read", [[nuevo_task_id]], {"fields": ["project_id"]})[0]
        valores["task_id"] = nuevo_task_id
        valores["project_id"] = tarea["project_id"][0]
        valores["employee_id"] = odoo_client.obtener_employee_de_tarea(nuevo_task_id)

    if data.get("fecha"):
        if not horas._fecha_valida(data["fecha"]):
            return jsonify({"error": "fecha inválida (usar AAAA-MM-DD)"}), 400
        valores["date"] = data["fecha"]
    if data.get("horas") is not None:
        horas_validas = horas._validar_horas(data["horas"])
        if horas_validas is None:
            return jsonify({"error": "horas inválidas (debe ser un número mayor a 0)"}), 400
        valores["unit_amount"] = horas_validas
    if "detalle" in data:
        valores["name"] = data["detalle"]

    if not valores:
        return jsonify({"error": "nada para actualizar"}), 400

    ok = odoo_client.odoo_execute_kw("account.analytic.line", "write", [[line_id], valores])
    return jsonify({"ok": ok})


@bp.route("/api/timesheet/<int:line_id>", methods=["DELETE"])
def borrar_timesheet(line_id):
    tarjeta = horas.tarjeta_de_la_request(request.args)

    error = horas._verificar_linea_de_tarjeta(line_id, tarjeta)
    if error:
        return error

    ok = odoo_client.odoo_execute_kw("account.analytic.line", "unlink", [[line_id]])
    return jsonify({"ok": ok})
