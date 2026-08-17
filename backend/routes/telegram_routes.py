"""Webhook de Telegram - ruta delgada, la lógica vive en telegram_bot.py."""

from flask import Blueprint, jsonify, request

from .. import config, telegram_bot

bp = Blueprint("telegram_routes", __name__)


@bp.route("/api/telegram-webhook", methods=["POST"])
def telegram_webhook():
    if not config.TELEGRAM_WEBHOOK_SECRET or request.headers.get("X-Telegram-Bot-Api-Secret-Token") != config.TELEGRAM_WEBHOOK_SECRET:
        return jsonify({"error": "no encontrado"}), 404

    update = request.get_json(silent=True) or {}
    telegram_bot.procesar_webhook(update)
    return jsonify({"ok": True})
