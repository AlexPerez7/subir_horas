"""
Punto de entrada para gunicorn (ver Procfile: `gunicorn backend_odoo:app`).
La app real vive en el paquete backend/ (ver backend/__init__.py para la
documentación completa) - este archivo solo la crea y la expone como `app`
para no tener que tocar el Procfile ni el comando de arranque en Render.
"""

from backend import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
