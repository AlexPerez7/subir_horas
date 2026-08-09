# Registro de Horas · Odoo

Herramienta personal para registrar horas de trabajo directo en Odoo (proyecto `GER_Producción Varios NF`), sin pasar por el flujo manual de anotar en Excel y después copiar uno por uno a la tarjeta correspondiente.

Consiste en un formulario web estático (publicado en **GitHub Pages**) conectado a un backend propio desplegado aparte (en **Render**), que habla con la API JSON-RPC de Odoo. Cada persona entra con su propio usuario y contraseña; el backend resuelve automáticamente qué tarjeta de Odoo le corresponde.

---

## Índice

- [Arquitectura](#arquitectura)
- [Requisitos](#requisitos)
- [Configuración inicial](#configuración-inicial)
- [Modo desarrollo (local)](#modo-desarrollo-local)
- [Desplegar el backend en Render](#desplegar-el-backend-en-render)
- [Publicar el frontend en GitHub Pages](#publicar-el-frontend-en-github-pages)
- [Gestión de usuarios](#gestión-de-usuarios)
- [Flujo de actualización](#flujo-de-actualización)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Seguridad](#seguridad)
- [Decisiones técnicas](#decisiones-técnicas)
- [Problemas comunes](#problemas-comunes)

---

## Arquitectura

```
┌──────────────────────┐   HTTPS (fetch, JSON,   ┌──────────────────┐        JSON-RPC        ┌──────┐
│  index.html            │   cookie de sesión)     │  backend_odoo.py │ ──────────────────────► │ Odoo │
│  (GitHub Pages,        │ ───────────────────────► │  (Flask, en      │ ◄────────────────────── │      │
│  estático, sin build)  │ ◄─────────────────────── │  Render)         │                          └──────┘
└──────────────────────┘                           └──────────────────┘
                                                              │
                                                       SQLite (usuarios.db)
                                                     login / tarjeta por usuario
```

- **`index.html`** — formulario standalone (HTML + CSS + JS, sin frameworks ni build step). Permite elegir tarjeta, subtarea, fecha, horas y descripción; muestra en vivo el historial real de esa subtarea en Odoo. No tiene ningún secreto embebido — solo la URL pública del backend. Se publica tal cual en GitHub Pages.
- **`backend_odoo.py`** — API Flask (JSON puro) que hace de intermediaria con Odoo y gestiona el login propio de la app (usuario/contraseña, sesión por cookie, tabla `usuarios` en SQLite). Nunca se llama a Odoo directo desde el navegador (evita exponer el token de API). Se despliega en Render, separado del frontend.
- **`crear_usuario.py`** — CLI para crear cuentas o resetear contraseñas en `usuarios.db`. Se corre del lado del backend desplegado (ver [Gestión de usuarios](#gestión-de-usuarios)).

Como el frontend y el backend viven en dominios distintos (`*.github.io` vs `*.onrender.com`), la comunicación es cross-origin: el backend habilita CORS solo para el origen exacto del sitio de GitHub Pages, y la cookie de sesión usa `SameSite=None; Secure` para poder viajar entre ambos dominios por HTTPS.

---

## Requisitos

- Python 3.10+
- Dependencias (ver [`requirements.txt`](requirements.txt)):
  ```
  pip install -r requirements.txt
  ```
- Acceso a Odoo con un usuario/token que tenga permisos de lectura/escritura sobre `project.task`, `account.analytic.line` y `hr.employee`.
- Una cuenta de GitHub (para Pages) y una cuenta de Render (para el backend) — ambas gratuitas.

---

## Configuración inicial

1. Copia [`.env.example`](.env.example) como `.env` (mismo nivel que `backend_odoo.py`).
2. Completa con tus credenciales reales de Odoo, una `SECRET_KEY` propia y, más adelante, la URL de tu sitio de GitHub Pages en `FRONTEND_ORIGINS`.
3. **`.env` nunca se sube a git** (está en `.gitignore`), tampoco `usuarios.db` (contiene hashes de contraseñas reales).

---

## Modo desarrollo (local)

Para iterar rápido sin desplegar nada, backend y frontend corren en `localhost` en puertos distintos:

```powershell
python backend_odoo.py
```

Esto levanta el backend en `http://127.0.0.1:5000`. En otra terminal, servir el HTML:

```powershell
python -m http.server 5500
```

Y abrir `http://127.0.0.1:5500/index.html`. Como ambos son `localhost` (mismo *site*, distinto puerto), alcanza con relajar la cookie en tu `.env` local:

```
COOKIE_SECURE=false
COOKIE_SAMESITE=Lax
```

En `index.html`, cambiá temporalmente `API_BASE` a `http://127.0.0.1:5000` mientras desarrollás (y volvé a poner la URL de Render antes de publicar).

Cualquier cambio en `index.html` se ve recargando la pestaña; cambios en `backend_odoo.py` requieren reiniciar el script.

---

## Desplegar el backend en Render

1. Subí el repo a GitHub (puede ser privado, ver [Seguridad](#seguridad)).
2. En Render: **New → Web Service**, conectá el repo.
3. Build command: `pip install -r requirements.txt`. Start command: lo toma de [`Procfile`](Procfile) automáticamente (`gunicorn backend_odoo:app --bind 0.0.0.0:$PORT`); si no lo detecta, pegalo a mano en Start Command.
4. Variables de entorno: cargá todas las de `.env.example` (`ODOO_URL`, `ODOO_DB`, `ODOO_UID`, `ODOO_TOKEN`, `SECRET_KEY`, `SESSION_LIFETIME_HORAS`, `FRONTEND_ORIGINS`, `COOKIE_SECURE=true`, `COOKIE_SAMESITE=None`, y las tres `BOOTSTRAP_ADMIN_*` — ver [Gestión de usuarios](#gestión-de-usuarios), las necesitás para poder loguearte la primera vez). `FRONTEND_ORIGINS` tiene que ser la URL exacta de tu sitio de GitHub Pages (la sabrás después del paso siguiente; se puede editar y volver a desplegar).
5. Deploy. Render te da una URL tipo `https://tu-servicio.onrender.com` — copiala, la vas a necesitar en `index.html`.

**Limitaciones del plan free de Render a tener en cuenta:**
- El servicio "duerme" tras ~15 minutos sin tráfico; el primer request después de eso tarda unos segundos en responder (arranque en frío). Normal para un uso personal.
- El disco es **efímero**: `usuarios.db` se recrea vacía en cada redeploy del backend. Ver [Gestión de usuarios](#gestión-de-usuarios).
- **No incluye acceso a Shell** (eso es del plan pago Starter en adelante), así que no se puede correr `crear_usuario.py` a mano ahí — el bootstrap del primer admin se resuelve con variables de entorno, no con la Shell (ver más abajo).

---

## Publicar el frontend en GitHub Pages

1. Editá `index.html`: reemplazá la constante `API_BASE` por la URL real de tu backend en Render (con `https://`, sin barra final).
2. Commiteá y pusheá.
3. En GitHub: **Settings → Pages → Build and deployment → Deploy from a branch**, elegí `main` y carpeta `/ (root)`.
4. GitHub te da una URL tipo `https://tu-usuario.github.io/subir_horas/`. Copiala en `FRONTEND_ORIGINS` en las variables de entorno de Render (sin barra final) y volvé a desplegar el backend para que el CORS la acepte.

> **Nota sobre cuentas Free:** GitHub Pages publica el sitio en una URL pública en internet aunque el repositorio origen sea privado — no hay control de acceso a nivel de Pages en cuentas Free/Pro (eso requiere GitHub Enterprise). Verificá en tu cuenta si Pages está habilitado para repos privados; si no, la alternativa es pasar el repo a público (el código no debería tener datos sensibles hardcodeados, pero repasalo antes). El acceso real a los datos de horas siempre queda detrás del login, así que exponer la página de login no es en sí un problema de seguridad — pero es bueno saberlo de antemano.

---

## Gestión de usuarios

**No hay registro abierto a propósito**: cualquiera con el link de GitHub Pages podría crearse una cuenta y elegir a qué tarjeta de Odoo cargarle horas si el alta fuera pública. En cambio, hay un panel de administración dentro de la propia app.

### Día a día: panel "Usuarios" (dentro de la app)

Si tu cuenta es admin, al loguearte ves una sección **Usuarios** con:
- Listado de usuarios existentes (tarjeta asignada, si es admin), con botones para **resetear contraseña** (🔑) o **eliminar** (🗑).
- Formulario para crear un usuario nuevo: usuario, tarjeta (elegís de la misma lista que ve el selector principal), contraseña inicial, y un checkbox "Es administrador".

Por detrás usa los endpoints `GET/POST /api/usuarios`, `POST /api/usuarios/<user>/resetear-password` y `DELETE /api/usuarios/<user>` — todos devuelven 403 si la sesión no es admin. Un admin no puede eliminarse a sí mismo (para no quedarse afuera por accidente).

### Bootstrap: el primer admin

El panel necesita que ya exista al menos un admin logueado. En el plan free de Render **no hay Shell** para correr comandos a mano (es una función paga desde el plan Starter), así que el primer admin se crea con tres variables de entorno:

```
BOOTSTRAP_ADMIN_USERNAME=tu-usuario
BOOTSTRAP_ADMIN_PASSWORD=una-contraseña-inicial
BOOTSTRAP_ADMIN_TARJETA=Alex Perez
```

Al arrancar, `backend_odoo.py` se fija si ya existe un usuario con ese `username`; si no existe, lo crea como admin con esa contraseña y tarjeta. Si ya existe, no hace nada — no pisa una contraseña que hayas cambiado después desde el panel. Cargalas en Render (**Environment**) y esperá el redeploy; con eso ya podés loguearte en el sitio de GitHub Pages y usar el panel **Usuarios** para todo lo demás.

**Dejalas cargadas en Render permanentemente** (no las borres después del primer login): como el disco de Render free es efímero, `usuarios.db` se resetea en cada redeploy del backend — estas tres variables son justamente la red de seguridad que recrea ese admin automáticamente cada vez que hace falta, sin que tengas que hacer nada manual. Ojo con un detalle: si cambiás la contraseña de `BOOTSTRAP_ADMIN_USERNAME` desde el panel y **después** hay un redeploy, al recrearse el usuario vuelve a la contraseña que esté en `BOOTSTRAP_ADMIN_PASSWORD` en Render (no la que hayas cambiado) — si querés que el cambio sea permanente, actualizá también la variable de entorno.

Si en algún momento corrés esto localmente o en un host con Shell disponible, [`crear_usuario.py`](crear_usuario.py) sigue siendo una alternativa por línea de comandos:

```powershell
python crear_usuario.py <username> "<Nombre exacto de la tarjeta en Odoo>" --admin
python crear_usuario.py <username> --reset-password
```

---

## Flujo de actualización

| Qué cambiaste | Qué hacer |
|---|---|
| `index.html` (diseño, JS, comportamiento del formulario) | Commit + push a `main`. GitHub Pages lo redespliega solo en un minuto o dos. |
| `backend_odoo.py` (endpoints, lógica de Odoo, auth) | Commit + push. Si tenés auto-deploy activado en Render, se redespliega solo; si no, disparalo a mano desde el dashboard. Recordá que esto resetea `usuarios.db` (ver arriba). |
| `.env` / variables de entorno del backend | Se editan directo en el dashboard de Render (pestaña Environment). No requiere tocar el repo. |

### Subir cambios a GitHub (con GitHub Desktop)

1. Abre GitHub Desktop, selecciona el repo `subir_horas`.
2. Pestaña **Changes** — revisa que la lista de archivos modificados tenga sentido (y que **nunca** aparezca `.env`, `usuarios.db`, `.venv/` o `__pycache__/`; si aparecen, algo falló con el `.gitignore`, no continúes).
3. Escribe un resumen del cambio y clic en **Commit to main**.
4. Clic en **Push origin** (arriba a la derecha) para subirlo a GitHub.

---

## Estructura del proyecto

```
subir_horas/
├── .env                  # credenciales locales (NO se sube a git)
├── .env.example           # plantilla sin datos reales
├── .gitignore
├── index.html              # esqueleto del frontend (se publica tal cual en GitHub Pages)
├── css/
│   └── style.css          # estilos de index.html
├── js/
│   └── app.js              # lógica del frontend (fetch al backend, UI)
├── backend_odoo.py        # API Flask, login + intermediario con Odoo (se despliega en Render)
├── crear_usuario.py       # CLI para crear/resetear usuarios
├── requirements.txt       # dependencias del backend
├── Procfile                # start command para Render
├── usuarios.db             # SQLite con usuarios (generado en runtime, NO se sube a git)
├── .venv/                 # entorno virtual local (ignorado)
└── __pycache__/           # (ignorado)
```

`index.html` queda en la raíz porque GitHub Pages sirve ese nombre por convención en la raíz del sitio; `css/` y `js/` se referencian con rutas relativas (`css/style.css`, `js/app.js`), así que si en algún momento se sirve desde una subcarpeta hay que revisar esas rutas.

---

## Seguridad

- El `.env` contiene un token de API real con permisos de escritura sobre Odoo. **Nunca** se commitea, ni se comparte por chat/capturas de pantalla sin taparlo. Lo mismo aplica a las variables de entorno cargadas en Render.
- Si el token llegara a exponerse accidentalmente (capturas, commit erróneo, etc.), hay que **rotarlo** en Odoo lo antes posible.
- `usuarios.db` guarda contraseñas **hasheadas** (`werkzeug.security`), nunca en texto plano — aun así, nunca se sube a git.
- CORS en el backend está restringido a los orígenes listados en `FRONTEND_ORIGINS` (no `CORS(app)` abierto). Si en algún momento agregás otro dominio desde el que se sirva el frontend, hay que sumarlo ahí.
- La cookie de sesión usa `SameSite=None; Secure`, o sea que **requiere HTTPS** en ambos extremos — no funciona si accedés al backend por `http://` en producción.
- El sitio publicado en GitHub Pages es **público en internet** aunque el repositorio sea privado (ver nota en [Publicar el frontend](#publicar-el-frontend-en-github-pages)). El login es lo único que protege el acceso a los datos de horas.
- El empleado de cada línea de horas se resuelve automáticamente según quién está **asignado a la subtarea** (`project.task.user_ids`), no según qué usuario de la app hizo el request. Esto permite, técnicamente, cargar horas "a nombre de" cualquier persona con tarjeta en el proyecto si sos admin — usar esa capacidad con criterio.

---

## Decisiones técnicas

Por si en unos meses hay que recordar el "por qué":

- **JSON-RPC, no XML-RPC**: el `ODOO_URL` de esta instancia de Assertiva ya apunta al endpoint `/jsonrpc`, así que el backend usa `requests` con el formato JSON-RPC 2.0 de Odoo (`service: "object"`, `method: "execute_kw"`) en vez de `xmlrpc.client`.
- **`ODOO_UID` fijo en vez de `authenticate()`**: se usa un UID ya resuelto (patrón heredado de un proyecto interno similar), evitando una llamada extra de autenticación en cada request.
- **Empleado resuelto por tarea, no por sesión**: inicialmente se intentó resolver el campo Empleado a partir del usuario autenticado en la API. Es incorrecto — Odoo lo determina según quién está asignado a la subtarea específica (`user_ids` de `project.task`), independientemente de qué credencial hizo la llamada API.
- **Filtro por tarjeta padre (`parent_id.name`) al buscar subtareas**: nombres de subtareas como "Carga de Horas" se repiten en las tarjetas de distintas personas dentro del mismo proyecto. Sin este filtro, la búsqueda podía devolver la subtarea de otra persona y cargar las horas en el lugar equivocado.
- **Login propio en vez de credenciales de Odoo**: cada usuario de la app tiene su cuenta (usuario/contraseña + tarjeta asignada) en `usuarios.db`, separada de cualquier login de Odoo. Así no hace falta darle a cada persona un usuario de Odoo solo para cargar horas.
- **Backend y frontend separados (Render + GitHub Pages) en vez de un solo proceso**: GitHub Pages no puede correr Flask; se necesitaba un servicio aparte para la lógica con estado (sesión, SQLite) y el secreto de Odoo. Esto obligó a que el login pase de páginas server-rendered a una API JSON pura, y a manejar cookies cross-site (`SameSite=None; Secure`) y CORS restringido por origen.
- **Sin app de escritorio**: la versión anterior se distribuía como `.exe` (pywebview + PyInstaller). Se descartó en favor de un sitio web accesible desde cualquier navegador, sin instalar nada.

---

## Problemas comunes

**`xmlrpc.client.ProtocolError: 404 NOT FOUND`**
El `ODOO_URL` en `.env` ya incluye `/jsonrpc`. Si se ve este error, alguna parte del código está intentando hablar XML-RPC en vez de JSON-RPC — revisar que se esté usando `odoo_execute_kw()` y no `xmlrpc.client` directo.

**Las horas caen en la tarjeta de otra persona**
Revisar que `buscar_tarea_id()` esté filtrando por `parent_id.name` correctamente, y que el valor de `tarjeta` enviado desde el formulario sea el nombre exacto de la tarjeta en Odoo.

**Error de campo inexistente al crear el registro**
Correr `GET /api/campos?modelo=<modelo>&q=<palabra>` (como admin) para confirmar el nombre técnico real del campo en esta instancia (varios campos están personalizados vía Odoo Studio, ej. `x_studio_*`).

**El navegador bloquea las llamadas al backend (error de CORS)**
`FRONTEND_ORIGINS` en el backend no incluye el origen exacto desde el que estás sirviendo `index.html` (protocolo + dominio, sin barra final). Revisar variables de entorno en Render y volver a desplegar.

**Me loguea bien pero después cada request da 401 ("no autenticado")**
Casi siempre es la cookie de sesión que no está viajando: confirmar que `index.html` usa `credentials: 'include'` en los `fetch` (ya lo trae por defecto vía la función `api()`), que el backend tiene `COOKIE_SECURE=true` y `COOKIE_SAMESITE=None`, y que estás accediendo a todo por HTTPS (no HTTP) en producción.

**No puedo loguearme después de un redeploy del backend**
Esperado en el plan free de Render: `usuarios.db` se resetea en cada redeploy. Volvé a correr `crear_usuario.py` desde la Shell de Render (ver [Gestión de usuarios](#gestión-de-usuarios)).
