"""
Lógica de negocio sobre horas/días hábiles/tarjetas: qué días revisar,
validación de horas y fechas, resúmenes y recordatorios, y creación de
líneas de timesheet. Depende de odoo_client para los datos, y de
flask.g/jsonify solo donde hace falta resolver la tarjeta de la sesión
actual o devolver un error de ruta ya armado.
"""

from datetime import date, timedelta

from flask import g, jsonify

from . import odoo_client


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


def parsear_fecha_busqueda(texto):
    """Si 'texto' tiene forma dd/mm o dd/mm/aaaa (como lo escribe alguien en
    el buscador), la interpreta con el año actual por defecto y la devuelve
    en formato ISO (AAAA-MM-DD). Devuelve None si no matchea ese patrón."""
    partes = texto.strip().split("/")
    if len(partes) not in (2, 3) or not all(p.isdigit() for p in partes):
        return None
    dia = int(partes[0])
    mes = int(partes[1])
    anio = int(partes[2]) if len(partes) == 3 else date.today().year
    if len(partes) == 3 and anio < 100:
        anio += 2000
    try:
        return date(anio, mes, dia).isoformat()
    except ValueError:
        return None


def _calcular_recordatorio(tarjeta):
    fecha_revisar = dia_habil_anterior(date.today())
    task_ids = odoo_client.subtareas_ids_de_tarjeta(tarjeta)
    if not task_ids:
        return {"pendiente": False, "fecha": fecha_revisar.isoformat()}

    lineas = odoo_client.odoo_execute_kw(
        "account.analytic.line", "search_read",
        [[["task_id", "in", task_ids], ["date", "=", fecha_revisar.isoformat()]]],
        {"fields": ["id"], "limit": 1},
    )
    return {"pendiente": len(lineas) == 0, "fecha": fecha_revisar.isoformat()}


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

    task_ids = odoo_client.subtareas_ids_de_tarjeta(tarjeta)
    if not task_ids:
        return {"semana": 0, "mes": 0, "por_subtarea": []}

    fecha_min = min(inicio_semana, inicio_mes).isoformat()
    fecha_max = max(fin_semana, fin_mes).isoformat()

    lineas = odoo_client.odoo_execute_kw(
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
        tareas = odoo_client.odoo_execute_kw("project.task", "read", [ids_unicos], {"fields": ["name"]})
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


def _crear_linea_timesheet(task_id, fecha, horas, detalle):
    """Crea la línea en account.analytic.line para una subtarea ya resuelta
    (por id). Usado tanto por /api/timesheet (que resuelve el id a partir
    de un nombre de subtarea) como por el bot de Telegram (que ya tiene el
    id porque lo sacó de un botón)."""
    tarea = odoo_client.odoo_execute_kw("project.task", "read", [[task_id]], {"fields": ["project_id", "name"]})[0]
    employee_id = odoo_client.obtener_employee_de_tarea(task_id)
    return odoo_client.odoo_execute_kw(
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


def _verificar_linea_de_tarjeta(line_id, tarjeta):
    """Devuelve None si la línea existe y pertenece a la tarjeta dada, o una
    respuesta de error (mismo patrón que auth.requiere_admin()) si no - para
    que editar_timesheet y borrar_timesheet no dupliquen este chequeo."""
    task_ids_tarjeta = odoo_client.subtareas_ids_de_tarjeta(tarjeta)
    linea_actual = odoo_client.odoo_execute_kw(
        "account.analytic.line", "read",
        [[line_id]], {"fields": ["task_id"]},
    )
    if not linea_actual:
        return jsonify({"error": "no existe esa línea"}), 404
    if linea_actual[0]["task_id"][0] not in task_ids_tarjeta:
        return jsonify({"error": "esa línea no pertenece a tu tarjeta"}), 403
    return None
