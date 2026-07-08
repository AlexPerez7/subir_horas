"""
Lanzador de escritorio para el registro de horas.

Levanta el backend Flask (backend_odoo.py) en un hilo en segundo plano
y abre el formulario en una ventana nativa con pywebview - sin
necesidad de dejar una terminal abierta ni de abrir el navegador
manualmente.

Instalar dependencia adicional:
    pip install pywebview

En Windows, pywebview usa el motor Edge WebView2, que ya viene
preinstalado en Windows 10/11 actualizados. Si da error de que falta
el runtime, se descarga acá (gratis, de Microsoft):
    https://developer.microsoft.com/microsoft-edge/webview2/

Uso:
    python app_escritorio.py

(deja backend_odoo.py, registro_horas.html y .env en la misma carpeta)
"""

import os
import sys
import threading
import webview

from backend_odoo import app as flask_app

if getattr(sys, "frozen", False):
    # Corriendo como .exe (PyInstaller): usar la carpeta donde está el
    # .exe, no la carpeta temporal donde se descomprime el bundle.
    CARPETA = os.path.dirname(sys.executable)
else:
    CARPETA = os.path.dirname(os.path.abspath(__file__))

HTML_PATH = os.path.join(CARPETA, "registro_horas.html")


def iniciar_backend():
    # use_reloader=False es importante: el reloader de Flask abre un
    # segundo proceso, lo cual no funciona bien corriendo dentro de un hilo.
    flask_app.run(port=5000, debug=False, use_reloader=False)


if __name__ == "__main__":
    hilo_backend = threading.Thread(target=iniciar_backend, daemon=True)
    hilo_backend.start()

    if not os.path.exists(HTML_PATH):
        raise FileNotFoundError(
            f"No se encontró {HTML_PATH}. Asegúrate de que "
            "registro_horas.html esté en la misma carpeta que este script."
        )

    webview.create_window(
        "Registro de horas · Odoo",
        HTML_PATH,
        width=1080,
        height=760,
        min_size=(760, 560),
    )
    webview.start()
    # Al cerrar la ventana, el script termina y el hilo del backend
    # (al ser daemon) se cierra solo con él.
