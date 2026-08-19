"""
Bot interactivo de Telegram.

A diferencia de los endpoints -cron (empujan un mensaje por un job
periódico de GitHub Actions, ver .github/workflows/), esto es un webhook:
Telegram le pega un POST a /api/telegram-webhook cada vez que alguien le
escribe al bot, o toca un botón (configurado una única vez con el método
setWebhook de la Bot API, ver README). No usa sesión de la app ni
CRON_SECRET - se protege con el secret_token propio de Telegram (chequeado
en la ruta, ver routes/telegram_routes.py), más el vínculo chat_id→cuenta
que cada usuario crea con /vincular (ver más abajo), para que nadie más
pueda hacerle preguntas al bot ni cargar horas a nombre de otra cuenta.

El bot es multiusuario: cualquier cuenta de la app puede vincular su chat
de Telegram con /vincular <usuario> <contraseña> y usar el bot para su
propia tarjeta.

Además de responder preguntas, el bot deja registrar horas por chat ("2h
hoy: reunión con cliente") - Telegram no tiene forma de mandar un <select>,
así que la subtarea se elige con botones inline en un segundo paso.
Mientras se espera esa elección (o las horas, si el registro arrancó
tocando un botón de "día sin cargar"), el estado pendiente vive en
PENDIENTES_TELEGRAM, en memoria del proceso - igual que los diccionarios de
auth.py: el Procfile corre un único worker de gunicorn, así que no hace
falta compartir esto entre procesos. Si el server se reinicia a mitad de un
registro simplemente se pierde y hay que volver a escribir, no es grave.

Este módulo no depende del contexto de Flask (request/g): procesar_webhook
recibe el diccionario `update` ya parseado, y devuelve None - toda la
comunicación con el usuario sale por las funciones telegram_*.
"""

import re
import sys
import unicodedata
from datetime import date, datetime, timedelta

import requests
from werkzeug.security import check_password_hash

from . import auth, config, db, horas, odoo_client

PENDIENTES_TELEGRAM = {}
_intentos_vincular_telegram = {}


def telegram_enviar_mensaje(chat_id, texto, teclado=None):
    if not config.TELEGRAM_BOT_TOKEN or not chat_id:
        return
    payload = {"chat_id": chat_id, "text": texto}
    if teclado:
        payload["reply_markup"] = {"inline_keyboard": teclado}
    try:
        requests.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
            json=payload,
            timeout=10,
        )
    except requests.RequestException:
        pass  # el peor caso es que no llegue el mensaje, no vale la pena reintentar acá


def telegram_editar_mensaje(chat_id, message_id, texto):
    if not config.TELEGRAM_BOT_TOKEN or not message_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/editMessageText",
            json={"chat_id": chat_id, "message_id": message_id, "text": texto},
            timeout=10,
        )
    except requests.RequestException:
        pass


def telegram_responder_callback(callback_id, texto=None):
    if not config.TELEGRAM_BOT_TOKEN:
        return
    payload = {"callback_query_id": callback_id, **({"text": texto} if texto else {})}
    try:
        requests.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
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
    horas_valor = float(m.group(1).replace(",", "."))
    resto = (texto[:m.start()] + texto[m.end():]).strip(" :-,.")
    return horas_valor, resto


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
        for t in odoo_client.subtareas_de_tarjeta(tarjeta)
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
    r = horas._calcular_resumen(tarjeta)
    detalle = "\n".join(f"- {s['subtarea']}: {s['horas']:.1f}h" for s in r["por_subtarea"])
    detalle = detalle or "(sin horas cargadas esta semana)"
    return f"📊 Esta semana: {r['semana']:.1f}h · Este mes: {r['mes']:.1f}h\n\n{detalle}"


def _texto_faltantes(tarjeta):
    """Devuelve (texto, teclado): un botón por día sin cargar que arranca
    el registro con esa fecha ya puesta (ver _manejar_callback_telegram)."""
    dias = horas.dias_habiles_atras(10)
    task_ids = odoo_client.subtareas_ids_de_tarjeta(tarjeta)
    if not task_ids:
        return "No encontré subtareas para tu tarjeta.", None
    fecha_min = min(dias).isoformat()
    fecha_max = max(dias).isoformat()
    lineas = odoo_client.odoo_execute_kw(
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
        horas_valor, detalle = _parsear_horas(texto)
        if horas_valor is None:
            return "No entendí las horas. Mandame algo como '2h reunión con cliente'.", None
        teclado = _teclado_subtareas(tarjeta)
        if not teclado:
            del PENDIENTES_TELEGRAM[chat_id]
            return "No encontré subtareas para tu tarjeta.", None
        PENDIENTES_TELEGRAM[chat_id] = {
            "etapa": "elegir_subtarea", "fecha": pendiente["fecha"], "horas": horas_valor, "detalle": detalle,
        }
        return "¿En qué subtarea? 👇", teclado

    normalizado = _quitar_acentos(texto.lower().strip())

    if _RE_HORAS.search(texto):
        fecha, resto = _parsear_fecha(texto)
        horas_valor, detalle = _parsear_horas(resto)
        if horas_valor is None:  # la fecha se comió el número por algún solape raro - probamos de nuevo sobre el texto original
            horas_valor, detalle = _parsear_horas(texto)
        teclado = _teclado_subtareas(tarjeta)
        if not teclado:
            return "No encontré subtareas para tu tarjeta.", None
        PENDIENTES_TELEGRAM[chat_id] = {"etapa": "elegir_subtarea", "fecha": fecha, "horas": horas_valor, "detalle": detalle}
        return "¿En qué subtarea? 👇", teclado

    if any(p in normalizado for p in _DISPARADORES_FALTANTES):
        return _texto_faltantes(tarjeta)

    if any(p in normalizado for p in _DISPARADORES_RESUMEN):
        return _texto_resumen(tarjeta), None

    return "No entendí. " + _texto_ayuda(), None


def _manejar_vincular(texto, chat_id):
    """Comando /vincular <usuario> <contraseña>: valida contra la tabla de
    usuarios (igual que /api/login) y guarda el mapeo chat_id→username. Protegido
    contra fuerza bruta con el mismo mecanismo de bloqueo que el login web,
    en un diccionario separado keyado por chat_id."""
    restante = auth._segundos_bloqueado(_intentos_vincular_telegram, chat_id)
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
    usuario = db.obtener_usuario(username)
    if not usuario or not check_password_hash(usuario["password_hash"], password):
        auth._registrar_intento_fallido(_intentos_vincular_telegram, chat_id)
        telegram_enviar_mensaje(chat_id, "Usuario o contraseña incorrectos.")
        return

    auth._limpiar_intentos(_intentos_vincular_telegram, chat_id)
    db.vincular_telegram(chat_id, username)
    PENDIENTES_TELEGRAM.pop(chat_id, None)  # por si el chat ya tenía un registro a medio hacer con la cuenta anterior
    telegram_enviar_mensaje(chat_id, f"Listo, vinculé este chat a la cuenta '{username}' ✅\n\n" + _texto_ayuda())


def _manejar_callback_telegram(callback):
    mensaje = callback.get("message") or {}
    chat_id = str((mensaje.get("chat") or {}).get("id", ""))
    message_id = mensaje.get("message_id")
    callback_id = callback.get("id")
    data = callback.get("data") or ""

    usuario = db.usuario_vinculado(chat_id)
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
            horas._crear_linea_timesheet(int(data.split(":", 1)[1]), pendiente["fecha"], pendiente["horas"], pendiente["detalle"])
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


def procesar_webhook(update):
    """Punto de entrada para POST /api/telegram-webhook (ya validado el
    secret_token en la ruta). No devuelve nada - toda la respuesta al
    usuario sale por las funciones telegram_*."""
    if "callback_query" in update:
        _manejar_callback_telegram(update["callback_query"])
        return

    mensaje = update.get("message") or update.get("edited_message") or {}
    texto = (mensaje.get("text") or "").strip()
    chat_id = str((mensaje.get("chat") or {}).get("id", ""))

    if not texto or not chat_id:
        return

    comando = texto.lower().strip().split("@")[0]  # "/resumen@mi_bot" -> "/resumen", por si Telegram lo agrega

    if comando.startswith("/vincular"):
        _manejar_vincular(texto, chat_id)
        return

    if comando in ("/desvincular", "/unlink"):
        ok = db.desvincular_telegram(chat_id)
        PENDIENTES_TELEGRAM.pop(chat_id, None)
        telegram_enviar_mensaje(chat_id, "Cuenta desvinculada." if ok else "No tenías ninguna cuenta vinculada.")
        return

    usuario = db.usuario_vinculado(chat_id)
    if not usuario:
        telegram_enviar_mensaje(chat_id, "No vinculé este chat a ninguna cuenta todavía. Mandá /vincular <usuario> <contraseña> primero.")
        return
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
