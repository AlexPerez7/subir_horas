# Registro de Horas · Odoo

Herramienta personal para registrar horas de trabajo directo en Odoo (proyecto `GER_Producción Varios NF`), sin pasar por el flujo manual de anotar en Excel y después copiar uno por uno a la tarjeta correspondiente.

Consiste en un formulario web estático (publicado en **GitHub Pages**) conectado a un backend propio desplegado aparte (en **Render**), que habla con la API JSON-RPC de Odoo. Cada persona entra con su propio usuario y contraseña; el backend resuelve automáticamente qué tarjeta de Odoo le corresponde.

---

## Índice

- [Arquitectura](#arquitectura)
- [Funcionalidades del formulario](#funcionalidades-del-formulario)
- [Requisitos](#requisitos)
- [Configuración inicial](#configuración-inicial)
- [Modo desarrollo (local)](#modo-desarrollo-local)
- [Desplegar el backend en Render](#desplegar-el-backend-en-render)
- [Publicar el frontend en GitHub Pages](#publicar-el-frontend-en-github-pages)
- [Instalar como app (PWA)](#instalar-como-app-pwa)
- [Mantener el backend despierto](#mantener-el-backend-despierto)
- [Recordatorio y resumen por Telegram](#recordatorio-y-resumen-por-telegram)
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
│  index.html            │   token en header)      │  backend_odoo.py │ ──────────────────────► │ Odoo │
│  (GitHub Pages,        │ ───────────────────────► │  (Flask, en      │ ◄────────────────────── │      │
│  estático, sin build)  │ ◄─────────────────────── │  Render)         │                          └──────┘
└──────────────────────┘                           └──────────────────┘
                                                              │
                                                       SQLite (usuarios.db)
                                                     login / tarjeta por usuario
```

- **`index.html`** — formulario standalone (HTML + CSS + JS, sin frameworks ni build step). Permite elegir tarjeta, subtarea, fecha, horas y descripción; muestra en vivo el historial real de esa subtarea en Odoo. No tiene ningún secreto embebido — solo la URL pública del backend. Se publica tal cual en GitHub Pages.
- **`backend_odoo.py`** — API Flask (JSON puro) que hace de intermediaria con Odoo y gestiona el login propio de la app (usuario/contraseña, token de sesión firmado, tabla `usuarios` en SQLite). Nunca se llama a Odoo directo desde el navegador (evita exponer el token de API). Se despliega en Render, separado del frontend.
- **`crear_usuario.py`** — CLI para crear cuentas o resetear contraseñas en `usuarios.db`. Se corre del lado del backend desplegado (ver [Gestión de usuarios](#gestión-de-usuarios)).

Como el frontend y el backend viven en dominios distintos (`*.github.io` vs `*.onrender.com`), la comunicación es cross-origin. La autenticación **no usa cookies**: muchos navegadores (Safari, Brave, Samsung Internet, y cada vez más) bloquean por defecto las cookies "de terceros" aunque tengan `SameSite=None; Secure`, lo que rompería el login. En cambio, `/api/login` devuelve un token firmado que el frontend guarda en `localStorage` y manda como header `Authorization: Bearer <token>` en cada pedido — no depende de ninguna política de cookies del navegador. El backend restringe CORS al origen exacto del sitio de GitHub Pages.

---

## Funcionalidades del formulario

Además de cargar/editar/eliminar horas y ver el historial en vivo:

- **Resumen y gráficos**: total de horas de la semana y del mes actual, un gráfico de barras con las horas por subtarea de la semana, y un heatmap de actividad de los últimos 30 días.
- **Carga en lote**: tildando "Cargar el mismo registro en varios días" se puede elegir un rango de fechas y repetir la misma subtarea/horas/descripción en cada día hábil del rango — útil para cargar retroactivamente una semana completa.
- **Buscador** en el historial de la subtarea, por descripción o fecha.
- **Aviso de horas altas**: si se registran o editan más de 9 horas en una entrada, pide confirmación extra antes de guardar (para atajar errores de tipeo).
- **Exportar** el historial visible a CSV o a Excel (`.xls`, formato SpreadsheetML — Excel puede avisar que el formato no coincide con la extensión, es normal, hay que abrirlo igual).
- **Se recuerda la última tarjeta/subtarea** usada (en `localStorage` del navegador), para no reseleccionar en cada sesión.
- **Indicador de conectividad** con el backend (conectando / despertando / conectado / sin conexión) en el header, y **aviso cuando la sesión está por expirar** (10 minutos antes), en vez de enterarte recién cuando falla una acción.
- Los diálogos de confirmación/alerta son **modales propios** de la app, no los nativos del navegador (`confirm()`/`alert()`).
- **Navegación por pestañas en mobile**: en pantallas chicas (≤820px), en vez de una sola página larga para deslizar, el contenido se agrupa en 4 pestañas con una barra fija abajo (Registrar / Resumen / Días / Admin) — patrón estándar de apps nativas. En desktop no cambia nada: se sigue viendo todo junto, sin pestañas.

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

Y abrir `http://127.0.0.1:5500/index.html`. En `.env` local, `FRONTEND_ORIGINS` tiene que incluir `http://127.0.0.1:5500`.

En `js/app.js`, cambiá temporalmente `API_BASE` a `http://127.0.0.1:5000` mientras desarrollás (y volvé a poner la URL de Render antes de publicar).

Cualquier cambio en `index.html` se ve recargando la pestaña; cambios en `backend_odoo.py` requieren reiniciar el script.

---

## Desplegar el backend en Render

1. Subí el repo a GitHub (puede ser privado, ver [Seguridad](#seguridad)).
2. En Render: **New → Web Service**, conectá el repo.
3. Build command: `pip install -r requirements.txt`. Start command: lo toma de [`Procfile`](Procfile) automáticamente (`gunicorn backend_odoo:app --bind 0.0.0.0:$PORT`); si no lo detecta, pegalo a mano en Start Command.
4. Variables de entorno: cargá todas las de `.env.example` (`ODOO_URL`, `ODOO_DB`, `ODOO_UID`, `ODOO_TOKEN`, `SECRET_KEY`, `SESSION_LIFETIME_HORAS`, `FRONTEND_ORIGINS`, y las tres `BOOTSTRAP_ADMIN_*` — ver [Gestión de usuarios](#gestión-de-usuarios), las necesitás para poder loguearte la primera vez). `FRONTEND_ORIGINS` tiene que ser **el origen exacto** de tu sitio de GitHub Pages: solo protocolo + dominio (ej. `https://tu-usuario.github.io`), **sin** la ruta del repo ni barra final — el navegador manda el header `Origin` sin la ruta, así que si dejás la ruta puesta el CORS no va a matchear y el sitio va a quedar bloqueado. (La sabrás después del paso siguiente; se puede editar y volver a desplegar).
5. Deploy. Render te da una URL tipo `https://tu-servicio.onrender.com` — copiala, la vas a necesitar en `index.html`.

**Limitaciones del plan free de Render a tener en cuenta:**
- El servicio "duerme" tras ~15 minutos sin tráfico; el primer request después de eso tarda unos segundos en responder (arranque en frío). Normal para un uso personal.
- El disco es **efímero**: `usuarios.db` se recrea vacía en cada redeploy del backend. Ver [Gestión de usuarios](#gestión-de-usuarios).
- **No incluye acceso a Shell** (eso es del plan pago Starter en adelante), así que no se puede correr `crear_usuario.py` a mano ahí — el bootstrap del primer admin se resuelve con variables de entorno, no con la Shell (ver más abajo).

---

## Publicar el frontend en GitHub Pages

1. Editá `js/app.js`: reemplazá la constante `API_BASE` (primera línea) por la URL real de tu backend en Render (con `https://`, sin barra final).
2. Commiteá y pusheá.
3. En GitHub: **Settings → Pages → Build and deployment → Deploy from a branch**, elegí `main` y carpeta `/ (root)`.
4. GitHub te da una URL tipo `https://tu-usuario.github.io/subir_horas/`. Copiala en `FRONTEND_ORIGINS` en las variables de entorno de Render (sin barra final) y volvé a desplegar el backend para que el CORS la acepte.

> **Nota sobre cuentas Free:** GitHub Pages publica el sitio en una URL pública en internet aunque el repositorio origen sea privado — no hay control de acceso a nivel de Pages en cuentas Free/Pro (eso requiere GitHub Enterprise). Verificá en tu cuenta si Pages está habilitado para repos privados; si no, la alternativa es pasar el repo a público (el código no debería tener datos sensibles hardcodeados, pero repasalo antes). El acceso real a los datos de horas siempre queda detrás del login, así que exponer la página de login no es en sí un problema de seguridad — pero es bueno saberlo de antemano.

---

## Instalar como app (PWA)

El sitio trae `manifest.json` + un service worker mínimo (`sw.js`) para poder instalarse como app, sin pasar por ninguna tienda:

- **Android / Chrome de escritorio**: menú del navegador → "Instalar app" (o el ícono ⊕ en la barra de direcciones).
- **iPhone (Safari)**: botón compartir → "Agregar a pantalla de inicio".

Queda con ícono propio y abre en su propia ventana, sin barra de navegador — el reemplazo directo del `.exe` viejo, pero sin instalar nada de verdad. El service worker **no cachea datos** a propósito (`sw.js` solo existe para cumplir el requisito técnico de instalabilidad) — los datos de Odoo siempre se piden en vivo, nunca vas a ver algo desactualizado por caché.

Los íconos están en `icons/` (generados una vez, no hace falta regenerarlos salvo que quieras cambiar el diseño).

---

## Mantener el backend despierto

Render free duerme el servicio tras ~15 min sin tráfico (ver [limitaciones](#desplegar-el-backend-en-render)). El workflow [`.github/workflows/keep-warm.yml`](.github/workflows/keep-warm.yml) le hace un ping a `/` cada 12 minutos en horario laboral aproximado (11:00-23:59 UTC, lunes a viernes) para que nunca llegue a dormirse mientras lo estás usando — se activa solo con GitHub Actions, no requiere ninguna cuenta externa.

- Si el horario no coincide con el tuyo, ajustá el rango de horas en el `cron:` del archivo (está en UTC, no en hora local).
- Diseñado para quedar por debajo de los 2000 minutos/mes gratis que da GitHub Actions en repos privados (~1400 min/mes con este esquema) — si lo hacés correr más seguido o más horas, revisá que no te pases.
- Podés dispararlo a mano desde la pestaña **Actions** del repo (`workflow_dispatch`) para probarlo sin esperar al próximo horario.

---

## Recordatorio y resumen por Telegram

El banner que aparece dentro de la app ("no cargaste ayer") solo lo ves si la abrís. Dos workflows lo complementan de forma proactiva, mandando mensajes por Telegram sin que tengas que abrir el sitio:

- [`.github/workflows/recordatorio-telegram.yml`](.github/workflows/recordatorio-telegram.yml) — todas las mañanas de un día hábil consulta al backend y, si falta cargar el día hábil anterior, manda un aviso.
- [`.github/workflows/resumen-semanal-telegram.yml`](.github/workflows/resumen-semanal-telegram.yml) — todos los viernes manda un resumen con el total de horas de la semana y el detalle por subtarea (usa `/api/resumen-semanal-cron`, protegido por el mismo `CRON_SECRET`).

Ambos reusan los mismos tres secrets (`CRON_SECRET`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) — configurando el primero, el segundo ya queda funcionando.

(Antes se probó con un webhook de Microsoft Teams, pero la plantilla de Power Automate falla con "Call made for a thread which is not a ChatThread" cuando el destino es un chat contigo mismo — es una limitación de esa plantilla, no del payload. Telegram evita todo ese problema: es un solo `curl` sin OAuth ni flujos intermedios.)

**1. Generar el secreto del cron**

Igual que `SECRET_KEY`:
```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```
Cargalo como `CRON_SECRET` en las variables de entorno de Render.

**2. Crear el bot de Telegram**

1. En Telegram, buscá **@BotFather** y mandale `/newbot`.
2. Elegí un nombre y un username (tiene que terminar en `bot`, ej. `subirhoras_bot`).
3. Te va a dar un **token** tipo `123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` — es el `TELEGRAM_BOT_TOKEN`.
4. Buscá a tu bot recién creado por su username y mandale cualquier mensaje (ej. "hola") — Telegram no deja que un bot te escriba primero, así que este paso es obligatorio.
5. Con el token, abrí en el navegador: `https://api.telegram.org/bot<TOKEN>/getUpdates` (reemplazando `<TOKEN>`). En la respuesta JSON buscá `"chat":{"id":...}` — ese número es el `TELEGRAM_CHAT_ID`.

**3. Cargar los secrets en GitHub**

En el repo: **Settings → Secrets and variables → Actions → New repository secret**, y agregá:
- `CRON_SECRET` — el mismo valor que pusiste en Render.
- `TELEGRAM_BOT_TOKEN` — el token que te dio BotFather.
- `TELEGRAM_CHAT_ID` — el id que sacaste de `getUpdates`.

**4. Probar**

Pestaña **Actions → Recordatorio de horas por Telegram → Run workflow** (y lo mismo para **Resumen semanal de horas por Telegram**). Si los tres secrets están bien cargados, el job debería pasar en verde. Mientras no los cargues, estos workflows van a fallar — es el comportamiento esperado hasta terminar de configurarlos, no un bug.

### Bot interactivo: preguntarle cosas al bot (y cargar horas)

Además de los avisos automáticos, le podés escribir directo al bot en Telegram. Esto es distinto de los workflows de arriba: en vez de un job periódico que empuja un mensaje, es un **webhook** — Telegram le pega un `POST` a tu backend en Render cada vez que le escribís (o tocás un botón), y el backend responde en el momento (`POST /api/telegram-webhook` en [`backend_odoo.py`](backend_odoo.py)).

Entiende:
- `/vincular <usuario> <contraseña>` → asocia ese chat de Telegram a tu cuenta de la app (las mismas credenciales del login web). Hace falta hacerlo una sola vez por chat antes de poder usar el resto de los comandos.
- `/resumen` o **"resumen de esta semana"** → total de horas de la semana y el mes, con el detalle por subtarea.
- `/faltantes` o **"¿qué días no he subido horas?"** → días hábiles sin cargar de los últimos 10, cada uno con un botón para arrancar la carga de ese día.
- **"2h hoy: reunión con cliente"** → registra horas directo desde el chat. El bot entiende `hoy`, `ayer` o una fecha `dd/mm`, y la cantidad de horas (`2h`, `1,5 horas`); como Telegram no tiene forma de mandar un desplegable, la subtarea se elige tocando uno de los botones que te ofrece después.
- `/desvincular` → olvida el vínculo de ese chat (por si vas a re-vincularlo a otra cuenta, o dejás de usar el bot).

El bot es **multiusuario**: cualquier cuenta de la app puede vincular su propio chat de Telegram con `/vincular` y usar el bot para su propia tarjeta — no hace falta ser el admin. Un chat sin vincular solo puede usar `/vincular`; para cualquier otro mensaje, el bot pide que te vincules primero. `/vincular` está protegido contra fuerza bruta igual que el login web (se bloquea 5 minutos tras 5 intentos fallidos desde el mismo chat).

**1. Variables de entorno en Render**

Además de `CRON_SECRET`, cargá en Render (no en GitHub — estas las usa el backend, no un workflow):
- `TELEGRAM_BOT_TOKEN` — el mismo token de BotFather.
- `TELEGRAM_WEBHOOK_SECRET` — una cadena aleatoria nueva (generarla igual que `SECRET_KEY`). Es el mecanismo con el que el backend verifica que el `POST` realmente viene de Telegram y no de cualquiera que le pegue a la URL.

(No hace falta `TELEGRAM_CHAT_ID` acá — esa variable la sigue necesitando, aparte, el paso 3 de más arriba, "Cargar los secrets en GitHub", para los workflows de recordatorio/resumen semanal.)

**2. Registrar el webhook en Telegram (una sola vez)**

Con tu token real y la URL de tu backend en Render:
```powershell
curl -X POST "https://api.telegram.org/bot<TOKEN>/setWebhook" `
  -d url="https://tu-servicio.onrender.com/api/telegram-webhook" `
  -d secret_token="<TELEGRAM_WEBHOOK_SECRET>"
```
Debería responder `{"ok":true,"result":true,...}`. A partir de ahí, cualquier mensaje que le mandes al bot (o botón que toques) dispara el webhook automáticamente — no hace falta volver a correr esto salvo que cambies de URL de Render o quieras rotar el secreto.

**3. Registrar los comandos en Telegram (opcional, una sola vez)**

Para que `/vincular`, `/resumen`, `/faltantes` y `/ayuda` aparezcan en el menú "/" del chat en vez de tener que acordarte de tipearlos:
```powershell
curl -X POST "https://api.telegram.org/bot<TOKEN>/setMyCommands" `
  -d "commands=[{\"command\":\"vincular\",\"description\":\"Vincular este chat a tu cuenta\"},{\"command\":\"resumen\",\"description\":\"Horas de esta semana y este mes\"},{\"command\":\"faltantes\",\"description\":\"Días hábiles sin cargar\"},{\"command\":\"registrar\",\"description\":\"Cómo cargar horas por chat\"},{\"command\":\"ayuda\",\"description\":\"Qué puede hacer el bot\"},{\"command\":\"desvincular\",\"description\":\"Olvidar el vínculo de este chat\"}]"
```

**4. Probar**

Escribile al bot `/vincular tu-usuario tu-contraseña` (las mismas credenciales del login web) y después "resumen" o "¿qué días no he subido horas?" desde Telegram. Si el backend estaba dormido (Render free), la primera respuesta puede tardar hasta un minuto en llegar (arranque en frío) — es normal.

---

## Gestión de usuarios

**No hay registro abierto a propósito**: cualquiera con el link de GitHub Pages podría crearse una cuenta y elegir a qué tarjeta de Odoo cargarle horas si el alta fuera pública. En cambio, hay un panel de administración dentro de la propia app.

### Día a día: panel "Usuarios" (dentro de la app)

Si tu cuenta es admin, al loguearte ves una sección **Usuarios** con:
- Listado de usuarios existentes (tarjeta asignada, si es admin), con botones para **resetear contraseña** (🔑) o **eliminar** (🗑).
- Formulario para crear un usuario nuevo: usuario, tarjeta (elegís de la misma lista que ve el selector principal), contraseña inicial, y un checkbox "Es administrador".

Por detrás usa los endpoints `GET/POST /api/usuarios`, `POST /api/usuarios/<user>/resetear-password` y `DELETE /api/usuarios/<user>` — todos devuelven 403 si la sesión no es admin. Un admin no puede eliminarse a sí mismo (para no quedarse afuera por accidente).

Debajo del panel hay una tabla de **auditoría** (`GET /api/auditoria`, también solo admin) con las últimas 50 acciones: quién creó/eliminó un usuario o reseteó una contraseña, y cuándo. Como el resto de `usuarios.db`, se resetea en cada redeploy de Render free — sirve para auditar entre deploys, no como historial permanente.

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
├── manifest.json           # metadata de la PWA (instalar como app)
├── sw.js                   # service worker mínimo, sin caché de datos
├── icons/                  # íconos de la PWA (192/512/180/32/16 px)
├── favicon.ico
├── .github/
│   └── workflows/
│       ├── keep-warm.yml                # ping periódico a Render para que no se duerma
│       ├── recordatorio-telegram.yml    # avisa por Telegram si falta cargar horas
│       └── resumen-semanal-telegram.yml # resumen semanal por Telegram (todos los viernes)
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
- El webhook del bot de Telegram (`POST /api/telegram-webhook`) valida el header `X-Telegram-Bot-Api-Secret-Token` contra `TELEGRAM_WEBHOOK_SECRET`, y además cada chat tiene que vincularse a una cuenta con `/vincular <usuario> <contraseña>` (protegido contra fuerza bruta igual que el login web) antes de poder ver horas o cargarlas — sin vincular, el bot solo responde pidiendo que te vincules. A diferencia del esquema anterior (un único `TELEGRAM_CHAT_ID` fijo, que ignoraba en silencio cualquier otro chat), el bot ahora es descubrible por cualquiera que encuentre su username, así que la única barrera es la contraseña de cada cuenta — no hace falta el username del bot para ser privado, hace falta la contraseña.
- El login se bloquea 5 minutos para un usuario tras 5 intentos fallidos seguidos (mitiga fuerza bruta básica). El contador vive en memoria del proceso — se resetea en cada redeploy, y solo funciona porque el `Procfile` corre un único worker de gunicorn (si en algún momento se agregan más workers, este esquema necesitaría un store compartido tipo Redis).
- El login usa un **token firmado** (`itsdangerous`, con `SECRET_KEY`), no una cookie — se eligió así porque las cookies cross-site (`SameSite=None; Secure`) quedan bloqueadas por defecto en varios navegadores (Safari, Brave, Samsung Internet). El token vive en `localStorage` del navegador y viaja en el header `Authorization`. Expira solo a las `SESSION_LIFETIME_HORAS` de haberse emitido (no hay forma de invalidarlo antes de tiempo del lado del servidor — es la contra de no guardar estado de sesión; "cerrar sesión" simplemente lo borra del navegador). Si se filtra un token, expira solo; si hace falta invalidar algo antes, hay que rotar `SECRET_KEY` (invalida *todos* los tokens activos, no solo uno).
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
- **Backend y frontend separados (Render + GitHub Pages) en vez de un solo proceso**: GitHub Pages no puede correr Flask; se necesitaba un servicio aparte para la lógica con estado (usuarios, SQLite) y el secreto de Odoo. Esto obligó a que el login pase de páginas server-rendered a una API JSON pura, con CORS restringido por origen.
- **Token en `localStorage` en vez de cookie de sesión**: el primer intento usó la cookie de sesión de Flask con `SameSite=None; Secure`. Funcionaba en pruebas con curl y en Chrome de escritorio, pero fallaba silenciosamente en Samsung Internet (y falla igual en Safari/Brave) porque esos navegadores bloquean cookies cross-site por política propia, sin importar los atributos de la cookie. Se cambió a un token firmado (`itsdangerous`) devuelto en el JSON del login, guardado en `localStorage` y mandado como header `Authorization: Bearer` — no depende de ninguna política de cookies.
- **Sin app de escritorio**: la versión anterior se distribuía como `.exe` (pywebview + PyInstaller). Se descartó en favor de un sitio web accesible desde cualquier navegador, sin instalar nada.

---

## Problemas comunes

**`xmlrpc.client.ProtocolError: 404 NOT FOUND`**
El `ODOO_URL` en `.env` ya incluye `/jsonrpc`. Si se ve este error, alguna parte del código está intentando hablar XML-RPC en vez de JSON-RPC — revisar que se esté usando `odoo_execute_kw()` y no `xmlrpc.client` directo.

**Las horas caen en la tarjeta de otra persona**
Revisar que `buscar_tarea_id()` esté filtrando por `parent_id.name` correctamente, y que el valor de `tarjeta` enviado desde el formulario sea el nombre exacto de la tarjeta en Odoo.

**Error de campo inexistente al crear el registro**
Correr `GET /api/campos?modelo=<modelo>&q=<palabra>` (como admin) para confirmar el nombre técnico real del campo en esta instancia (varios campos están personalizados vía Odoo Studio, ej. `x_studio_*`).

**El navegador bloquea las llamadas al backend (error de CORS) / la página queda en negro**
`FRONTEND_ORIGINS` en el backend no incluye el origen exacto desde el que estás sirviendo `index.html`: tiene que ser **solo protocolo + dominio** (ej. `https://tu-usuario.github.io`), sin la ruta del repo (`/subir_horas`) ni barra final — el navegador manda el header `Origin` sin la ruta, así que si la dejás puesta no matchea nunca. Revisar en Render → Environment y volver a desplegar. (Si la página queda completamente en blanco/negro sin mostrar ni el login, confirmá que estás en la versión más reciente de `js/app.js` — versiones viejas no manejaban este error y se quedaban sin mostrar nada).

**Me loguea bien pero después cada request da 401 ("no autenticado")**
El token puede haber expirado (dura `SESSION_LIFETIME_HORAS`, default 8) — volvé a loguearte. Si pasa inmediatamente después de loguearte, revisá en las herramientas de desarrollador (Network) que el pedido a `/api/whoami` esté mandando el header `Authorization: Bearer ...` — si no lo manda, puede ser que `localStorage` esté deshabilitado o bloqueado (modo incógnito estricto, alguna extensión).

**No puedo loguearme después de un redeploy del backend**
Esperado en el plan free de Render: `usuarios.db` se resetea en cada redeploy. Si tenés `BOOTSTRAP_ADMIN_*` cargadas en Render, ese admin se recrea solo — esperá el redeploy y reintentá. Si no las tenés, corré `crear_usuario.py` (ver [Gestión de usuarios](#gestión-de-usuarios)).
