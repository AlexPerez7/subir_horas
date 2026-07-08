# Registro de Horas · Odoo

Herramienta personal para registrar horas de trabajo directo en Odoo (proyecto `GER_Producción Varios NF`), sin pasar por el flujo manual de anotar en Excel y después copiar uno por uno a la tarjeta correspondiente.

Consiste en un formulario web liviano conectado a un backend propio, que habla con la API JSON-RPC de Odoo. Se puede correr en modo desarrollo (Python) o como aplicación de escritorio empaquetada (`.exe`).

---

## Índice

- [Arquitectura](#arquitectura)
- [Requisitos](#requisitos)
- [Configuración inicial](#configuración-inicial)
- [Modo desarrollo](#modo-desarrollo)
- [Compilar el .exe](#compilar-el-exe)
- [Flujo de actualización](#flujo-de-actualización)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Seguridad](#seguridad)
- [Decisiones técnicas](#decisiones-técnicas)
- [Problemas comunes](#problemas-comunes)

---

## Arquitectura

```
┌─────────────────────┐        HTTP (localhost:5000)       ┌──────────────────┐        JSON-RPC        ┌──────┐
│  registro_horas.html │ ───────────────────────────────►  │  backend_odoo.py │ ──────────────────────► │ Odoo │
│  (formulario, JS)    │ ◄───────────────────────────────  │  (Flask)         │ ◄────────────────────── │      │
└─────────────────────┘                                    └──────────────────┘                         └──────┘
         ▲
         │  se abre en una ventana nativa
         │
┌─────────────────────┐
│  app_escritorio.py   │  (pywebview + hilo con el backend)
└─────────────────────┘
```

- **`registro_horas.html`** — formulario standalone (HTML + CSS + JS, sin frameworks ni build step). Permite elegir tarjeta, subtarea, fecha, horas y descripción; muestra en vivo el historial real de esa subtarea en Odoo.
- **`backend_odoo.py`** — API Flask que hace de intermediario con Odoo. Nunca se llama a Odoo directo desde el navegador (evita exponer el token de API y problemas de CORS).
- **`app_escritorio.py`** — lanzador de escritorio: levanta el backend en un hilo y abre el HTML en una ventana nativa (pywebview), sin necesidad de terminal ni navegador.
- **`build.bat`** — automatiza la recompilación del `.exe` y copia los archivos necesarios a `dist/`.

---

## Requisitos

- Python 3.10+
- Dependencias:
  ```
  pip install flask flask-cors python-dotenv requests pywebview pyinstaller
  ```
- Acceso a Odoo con un usuario/token que tenga permisos de lectura/escritura sobre `project.task`, `account.analytic.line` y `hr.employee`.

---

## Configuración inicial

1. Copia `.env.example` como `.env` (mismo nivel que `backend_odoo.py`).
2. Completa con tus credenciales reales:
   ```
   ODOO_URL=https://www.assertiva.biz/jsonrpc
   ODOO_DB=origami-soft-assertiva-main-...
   ODOO_UID=429
   ODOO_TOKEN=xxxxxxxxxxxxxxxx
   ```
3. **`.env` nunca se sube a git** (está en `.gitignore`). Si necesitas compartir la configuración con otra máquina tuya, copia el archivo a mano.

---

## Modo desarrollo

Para iterar rápido sin recompilar nada:

```powershell
python app_escritorio.py
```

Esto levanta el backend y abre la ventana. Cualquier cambio en `registro_horas.html` se ve recargando la ventana; cambios en `backend_odoo.py` requieren reiniciar el script.

Alternativa sin ventana nativa (para debug con las herramientas de desarrollador del navegador):
```powershell
python backend_odoo.py
```
y luego abrir `registro_horas.html` directo en el navegador (doble clic).

---

## Compilar el .exe

```powershell
pyinstaller --onefile --windowed --name "RegistroHoras" app_escritorio.py
```

Esto genera `dist/RegistroHoras.exe`. El HTML **no** queda embebido a propósito (ver [Flujo de actualización](#flujo-de-actualización)), así que hay que copiar a mano junto al `.exe`:

```
dist/
├── RegistroHoras.exe
├── registro_horas.html
└── .env
```

**`build.bat` hace estos pasos automáticamente** (compilar + copiar `.html` y `.env` a `dist/`). Es la forma recomendada de recompilar.

---

## Flujo de actualización

| Qué cambiaste | Qué hacer |
|---|---|
| `registro_horas.html` (diseño, JS, comportamiento del formulario) | Reemplaza el archivo junto al `.exe` en `dist/`. **No hace falta recompilar.** Solo cierra y vuelve a abrir `RegistroHoras.exe`. |
| `backend_odoo.py` (endpoints, lógica de Odoo) | Ejecuta `build.bat` (o el comando de PyInstaller a mano) para recompilar. |
| `app_escritorio.py` (el lanzador en sí) | Igual, hay que recompilar con `build.bat`. |
| `.env` (credenciales) | Se edita directo en `dist/.env`. No requiere recompilar ni tocar el repo (está ignorado por git). |

### Subir cambios a GitHub (con GitHub Desktop)

1. Abre GitHub Desktop, selecciona el repo `subir_horas`.
2. Pestaña **Changes** — revisa que la lista de archivos modificados tenga sentido (y que **nunca** aparezca `.env`, `dist/`, `build/`, `.venv/` o `__pycache__/`; si aparecen, algo falló con el `.gitignore`, no continúes).
3. Escribe un resumen del cambio y clic en **Commit to main**.
4. Clic en **Push origin** (arriba a la derecha) para subirlo a GitHub.

---

## Estructura del proyecto

```
subir_horas/
├── .env                  # credenciales (NO se sube a git)
├── .env.example           # plantilla sin datos reales
├── .gitignore
├── app_escritorio.py      # lanzador de escritorio (pywebview + backend en hilo)
├── backend_odoo.py        # API Flask, intermediario con Odoo
├── registro_horas.html    # formulario (frontend)
├── build.bat              # compila el .exe y copia archivos a dist/
├── RegistroHoras.spec     # config generada por PyInstaller (se puede versionar o ignorar)
├── build/                 # artefactos temporales de PyInstaller (ignorado)
├── dist/                  # build final: .exe + .html + .env (ignorado)
├── .venv/                 # entorno virtual (ignorado)
└── __pycache__/           # (ignorado)
```

---

## Seguridad

- El `.env` contiene un token de API real con permisos de escritura sobre Odoo. **Nunca** se commitea, ni se comparte por chat/capturas de pantalla sin taparlo.
- Si el token llegara a exponerse accidentalmente (capturas, commit erróneo, etc.), hay que **rotarlo** en Odoo lo antes posible.
- El repositorio de GitHub es **privado**, porque el código contiene nombres reales de clientes en comentarios y datos de ejemplo (Koandina, Redbanc, etc.).
- El backend Flask corre con `CORS(app)` abierto — pensado para uso 100% local (`localhost`). Si en algún momento se expone a una red o se hostea en otro lado, hay que restringir el origen permitido.
- El empleado de cada línea de horas se resuelve automáticamente según quién está **asignado a la subtarea** (`project.task.user_ids`), no según qué token de API se usó para llamar. Esto permite, técnicamente, cargar horas "a nombre de" cualquier persona con tarjeta en el proyecto — usar esa capacidad con criterio (ver conversación de diseño para el detalle de por qué esto es una consideración de auditoría, no solo técnica).

---

## Decisiones técnicas

Por si en unos meses hay que recordar el "por qué":

- **JSON-RPC, no XML-RPC**: el `ODOO_URL` de esta instancia de Assertiva ya apunta al endpoint `/jsonrpc`, así que el backend usa `requests` con el formato JSON-RPC 2.0 de Odoo (`service: "object"`, `method: "execute_kw"`) en vez de `xmlrpc.client`.
- **`ODOO_UID` fijo en vez de `authenticate()`**: se usa un UID ya resuelto (patrón heredado de un proyecto interno similar), evitando una llamada extra de autenticación en cada request.
- **Empleado resuelto por tarea, no por sesión**: inicialmente se intentó resolver el campo Empleado a partir del usuario autenticado en la API. Es incorrecto — Odoo lo determina según quién está asignado a la subtarea específica (`user_ids` de `project.task`), independientemente de qué credencial hizo la llamada API.
- **Filtro por tarjeta padre (`parent_id.name`) al buscar subtareas**: nombres de subtareas como "Carga de Horas" se repiten en las tarjetas de distintas personas dentro del mismo proyecto. Sin este filtro, la búsqueda podía devolver la subtarea de otra persona y cargar las horas en el lugar equivocado.
- **HTML fuera del `.exe`**: se decidió no empaquetar `registro_horas.html` dentro del ejecutable (`--add-data`) para poder iterar el frontend sin recompilar en cada cambio.

---

## Problemas comunes

**`xmlrpc.client.ProtocolError: 404 NOT FOUND`**
El `ODOO_URL` en `.env` ya incluye `/jsonrpc`. Si se ve este error, alguna parte del código está intentando hablar XML-RPC en vez de JSON-RPC — revisar que se esté usando `odoo_execute_kw()` y no `xmlrpc.client` directo.

**Las horas caen en la tarjeta de otra persona**
Revisar que `buscar_tarea_id()` esté filtrando por `parent_id.name` correctamente, y que el valor de `tarjeta` enviado desde el formulario sea el nombre exacto de la tarjeta en Odoo.

**Error de campo inexistente al crear el registro**
Correr `GET /api/campos?modelo=<modelo>&q=<palabra>` para confirmar el nombre técnico real del campo en esta instancia (varios campos están personalizados vía Odoo Studio, ej. `x_studio_*`).

**El `.exe` no abre / falta WebView2**
En Windows, pywebview usa el motor Edge WebView2. Si no está instalado (poco común en Windows 10/11 actualizados), se descarga gratis desde [Microsoft](https://developer.microsoft.com/microsoft-edge/webview2/).
