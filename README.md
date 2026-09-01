# Registro de Horas · Odoo

Herramienta personal para registrar horas de trabajo directo en Odoo (proyecto `GER_Producción Varios NF`), sin pasar por el flujo manual de anotar en Excel y después copiar uno por uno a la tarjeta correspondiente.

Consiste en un formulario web estático (publicado en **GitHub Pages**) conectado a un backend propio desplegado aparte (en una **VM Linux propia**, expuesta a internet vía **Tailscale Funnel**), que habla con la API JSON-RPC de Odoo. Cada persona entra con su propio usuario y contraseña; el backend resuelve automáticamente qué tarjeta de Odoo le corresponde.

---

## Índice

- [Arquitectura](#arquitectura)
- [Funcionalidades del formulario](#funcionalidades-del-formulario)
- [Requisitos](#requisitos)
- [Configurar Supabase (base de datos persistente)](#configurar-supabase-base-de-datos-persistente)
- [Configuración inicial](#configuración-inicial)
- [Modo desarrollo (local)](#modo-desarrollo-local)
- [Desplegar el backend en tu propia VM](#desplegar-el-backend-en-tu-propia-vm)
- [Deploy automático](#deploy-automático)
- [CI](#ci)
- [Publicar el frontend en GitHub Pages](#publicar-el-frontend-en-github-pages)
- [Instalar como app (PWA)](#instalar-como-app-pwa)
- [Gestión de usuarios](#gestión-de-usuarios)
- [Flujo de actualización](#flujo-de-actualización)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Seguridad](#seguridad)
- [Decisiones técnicas](#decisiones-técnicas)
- [Monitoreo](#monitoreo)
- [Recuperación ante desastres](#recuperación-ante-desastres)
- [Problemas comunes](#problemas-comunes)

---

## Arquitectura

```
┌────────────────┐  HTTPS (fetch,   ┌───────────────┐  127.0.0.1:8000  ┌──────────────────┐   JSON-RPC   ┌──────┐
│  index.html      │  JSON, token     │  Tailscale      │ ───────────────► │  backend_odoo.py │ ────────────► │ Odoo │
│  (GitHub Pages)  │ ───────────────► │  Funnel /       │                  │  (Flask+gunicorn)│ ◄──────────── │      │
│                  │ ◄─────────────── │  Render.com     │ ◄─────────────── │                  │                └──────┘
└────────────────┘                  └───────────────┘                  └─────────┬────────┘
                                                                                 │
                                                                        Postgres (Supabase)
                                                                        login / tarjeta por usuario
```

- **`index.html`** — formulario standalone (HTML + CSS + JS, sin frameworks ni build step). Permite elegir tarjeta, subtarea, fecha, horas y descripción; muestra en vivo el historial real de esa subtarea en Odoo. No tiene ningún secreto embebido — solo la URL pública del backend. Se publica tal cual en GitHub Pages.
- **`backend_odoo.py` + el paquete `backend/`** — API Flask (JSON puro) que hace de intermediaria con Odoo y gestiona el login propio de la app (usuario/contraseña, token de sesión firmado, tabla `usuarios` en Postgres). Nunca se llama a Odoo directo desde el navegador (evita exponer el token de API). Corre como servicio en Render.com o en una VM propia. Ver [Estructura del proyecto](#estructura-del-proyecto) para cómo está dividido el paquete.
- **`scripts/crear_usuario.py`** — CLI para crear cuentas o resetear contraseñas. Se corre desde tu máquina local, apuntando a la misma base de Supabase que usa producción (ver [Gestión de usuarios](#gestión-de-usuarios)).

Como el frontend y el backend viven en dominios distintos (`*.github.io` vs backend host), la comunicación es cross-origin. La autenticación **no usa cookies**: muchos navegadores (Safari, Brave, Samsung Internet, y cada vez más) bloquean por defecto las cookies "de terceros" aunque tengan `SameSite=None; Secure`, lo que rompería el login. En cambio, `/api/login` devuelve un token firmado que el frontend guarda en `localStorage` y manda como header `Authorization: Bearer <token>` en cada pedido — no depende de ninguna política de cookies del navegador. El backend restringe CORS al origen exacto del sitio de GitHub Pages.

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
- Una cuenta de GitHub (para Pages) y una cuenta de Supabase (para la base de datos de usuarios) — ambas gratuitas.
- Un host para el backend (Render.com, VM Linux propia, etc.).

---

## Configurar Supabase (base de datos persistente)

Los usuarios de la app (login y auditoría) se guardan en Postgres, en un proyecto de [Supabase](https://supabase.com) — free tier, con almacenamiento persistente y gestionado.

El backend le habla a Supabase por su **API REST** (HTTPS/443, paquete `supabase` de Python), no por conexión directa al protocolo de Postgres (puertos 5432/6543) — pensado para redes que solo dejan salir tráfico HTTPS.

1. Crea una cuenta en [supabase.com](https://supabase.com) y un proyecto nuevo.
2. **Crear las tablas (una sola vez):** en el proyecto, ve a **SQL Editor → New query**, pega esto y ejecútalo:
   ```sql
   CREATE TABLE IF NOT EXISTS usuarios (
       username TEXT PRIMARY KEY,
       password_hash TEXT NOT NULL,
       tarjeta TEXT NOT NULL,
       es_admin INTEGER NOT NULL DEFAULT 0
   );
   CREATE TABLE IF NOT EXISTS auditoria (
       id SERIAL PRIMARY KEY,
       ts TEXT NOT NULL,
       actor TEXT NOT NULL,
       accion TEXT NOT NULL,
       detalle TEXT
   );

   -- Row Level Security: activado sin políticas => nadie puede leer/escribir
   -- estas tablas por la API REST con la anon key. El backend usa la
   -- service_role key, que bypassea RLS, así que no cambia nada del flujo.
   ALTER TABLE public.usuarios  ENABLE ROW LEVEL SECURITY;
   ALTER TABLE public.auditoria ENABLE ROW LEVEL SECURITY;
   ```
   Es seguro volver a correrlo (`IF NOT EXISTS` / `ENABLE ROW LEVEL SECURITY` es idempotente) — si ya tenías estas tablas de una migración anterior, solo agrega el RLS.
3. **Sacar las credenciales de la API:** **Project Settings → API**. Copia la **Project URL** (`SUPABASE_URL`) y la **`service_role` key** (`SUPABASE_SERVICE_ROLE_KEY`) — **no** la `anon`/`public` key.
4. Esas dos van en tus variables de entorno locales y en las de producción.

---

## Configuración inicial

1. Copia [`.env.example`](.env.example) como `.env` (mismo nivel que `backend_odoo.py`).
2. Completa con tus credenciales reales de Odoo, una `SECRET_KEY` propia, `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` (ver arriba) y, más adelante, la URL de tu sitio de GitHub Pages en `FRONTEND_ORIGINS`.
3. **`.env` nunca se sube a git** (está en `.gitignore`) — contiene el token de Odoo y la contraseña de la base de datos.

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

En `js/app.js`, cambia temporalmente `API_BASE` a `http://127.0.0.1:5000` mientras desarrollas (y vuelve a poner la URL pública de tu backend antes de publicar — la de Render.com `https://*.onrender.com` o la de tu VM vía Tailscale Funnel `https://*.ts.net`, según dónde lo tengas desplegado).

Cualquier cambio en `index.html` se ve recargando la pestaña; cambios en `backend_odoo.py` requieren reiniciar el script.

---

## Desplegar el backend en tu propia VM

Backend corriendo como servicio systemd en una VM Ubuntu/Debian propia (siempre encendida), expuesto a internet sin abrir puertos vía [Tailscale Funnel](https://tailscale.com/kb/1223/funnel). A diferencia de un PaaS (Render, Koyeb, etc.), acá no hay auto-deploy ni build gestionado — los pasos de clonar, actualizar e instalar dependencias los corres tú mismo por SSH/VPN a la VM.

**1. Preparar el código en la VM**

```bash
sudo apt update && sudo apt install -y python3-venv git
git clone https://github.com/tu-usuario/subir_horas.git
cd subir_horas
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
nano .env   # completa ODOO_URL, ODOO_DB, ODOO_UID, ODOO_TOKEN, SECRET_KEY,
            # SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, FRONTEND_ORIGINS,
            # BOOTSTRAP_ADMIN_*, etc.
```

**2. Correrlo como servicio systemd** (para que sobreviva reinicios de la VM y se reinicie solo si crashea)

El `ExecStart` de [`deploy/subir-horas.service`](deploy/subir-horas.service) usa `--worker-class gthread --threads 4`: gunicorn por default atiende **un solo pedido a la vez** (worker `sync`), así que si el frontend dispara varios pedidos en paralelo al cargar la página (resumen, subtareas, historial, heatmap...), se encolan y se atienden de a uno aunque el navegador los haya mandado juntos. Con un pool de threads en el mismo proceso, el backend puede atenderlos en simultáneo mientras cada uno espera su propia respuesta de Odoo por red — sigue siendo un solo proceso, así que los diccionarios en memoria (bloqueo de login, caché de Odoo) se comparten igual que antes.

```bash
sudo cp deploy/subir-horas.service /etc/systemd/system/subir-horas.service
sudo nano /etc/systemd/system/subir-horas.service   # ajusta User= y las dos rutas /ruta/a/subir_horas
sudo systemctl daemon-reload
sudo systemctl enable --now subir-horas
sudo systemctl status subir-horas   # debería decir "active (running)"
```

El servicio queda escuchando solo en `127.0.0.1:8000` (no expuesto a la red local ni a internet directamente) — Tailscale Funnel es quien lo publica hacia afuera en el paso siguiente.

**3. Instalar Tailscale y activar Funnel**

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up   # abre un link para autenticar la VM en tu cuenta de Tailscale
sudo tailscale funnel 8000
```

Esto último te da una URL fija tipo `https://tu-maquina.tu-tailnet.ts.net` — cópiala, la vas a necesitar en `index.html`. `tailscale funnel status` muestra el estado en cualquier momento; queda activo aunque cierres la sesión SSH (corre como daemon del sistema).

**Cosas a tener en cuenta con este esquema:**
- Sin cold starts ni sleep: al ser una VM propia siempre encendida, el backend responde igual de rápido a cualquier hora — no hace falta ningún workflow tipo "keep-warm".
- El disco **no es efímero** (a diferencia de un PaaS free): los datos locales sobreviven reinicios de la VM. Los usuarios de todas formas viven en Postgres/Supabase (ver [Configurar Supabase](#configurar-supabase-base-de-datos-persistente)), así que esto no cambia nada del diseño.
- Tienes acceso `sudo` completo a la VM, así que `scripts/crear_usuario.py` se puede correr directo ahí (`sudo -u CAMBIAR_USUARIO .venv/bin/python scripts/crear_usuario.py ...`) además de desde tu máquina local — igual dejamos el bootstrap por variables de entorno (`BOOTSTRAP_ADMIN_*`, ver [Gestión de usuarios](#gestión-de-usuarios)) como la forma más simple de tener el primer admin sin loguearte a la VM.
- Actualizar el backend tras un cambio de código puede ser automático (ver [Deploy automático](#deploy-automático)) o manual: `git pull && sudo systemctl restart subir-horas` en la VM. Ver [Flujo de actualización](#flujo-de-actualización).
- Es una VM compartida con otros usos de oficina — confirma con quien la administre que está bien correr un servicio expuesto públicamente ahí antes de activar el Funnel.

---

## Deploy automático

Por defecto, actualizar el backend es manual (`git pull && sudo systemctl restart subir-horas` en la VM, ver [Flujo de actualización](#flujo-de-actualización)). El workflow [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml) lo automatiza: cada push a `main` hace ese mismo `git pull` + reinstalar dependencias + reiniciar el servicio, sin que tengas que entrar a la VM.

Como la VM no tiene IP pública ni puerto entrante abierto (solo Tailscale Funnel, saliente), no puede usar un runner normal de GitHub — en cambio corre en un **runner propio** instalado en la misma VM, que se conecta hacia afuera a GitHub (igual que Tailscale) para pedir trabajo, sin necesidad de abrir nada en el firewall de la oficina.

**1. Instalar el runner en la VM**

En GitHub: **Settings → Actions → Runners → New self-hosted runner**, elige **Linux**, y copia/pega en la VM los comandos exactos que te muestra ahí (cambian de versión con el tiempo, por eso no se listan acá tal cual). En general es:

```bash
mkdir ~/actions-runner && cd ~/actions-runner
curl -o actions-runner.tar.gz -L <URL que te da GitHub>
tar xzf actions-runner.tar.gz
./config.sh --url https://github.com/<tu-usuario>/subir_horas --token <TOKEN que te da GitHub>
```

En el paso `./config.sh`, cuando pregunte por labels/grupo, los valores por default están bien (el workflow usa `runs-on: self-hosted`, sin label extra).

**2. Correrlo como servicio** (para que sobreviva reinicios de la VM, igual que `subir-horas`)

```bash
sudo ./svc.sh install
sudo ./svc.sh start
sudo ./svc.sh status   # debería decir "active (running)"
```

**3. Permitir que el runner reinicie el servicio sin pedir contraseña**

El runner corre con el mismo usuario del sistema con el que lo configuraste (normalmente el mismo que usa `subir-horas`, ver `User=` en [`deploy/subir-horas.service`](deploy/subir-horas.service)). Ese usuario necesita poder correr `systemctl restart subir-horas` sin que el workflow se quede colgado esperando una contraseña de `sudo` que nadie va a tipear:

```bash
sudo visudo -f /etc/sudoers.d/subir-horas-deploy
```

Agrega esta línea (cambia `administrator` por el usuario real que corre el runner):

```
administrator ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart subir-horas
```

Guarda y sal (`visudo` valida la sintaxis solo; si hay un error de tipeo, avisa y no guarda — evita dejar `sudoers` roto). Esta regla es **acotada a un solo comando**, no da `sudo` general al usuario del runner.

**4. Probar**

Commitea y pushea cualquier cambio a `main` (o **Actions → Deploy automático a la VM → Run workflow**). Debería aparecer una corrida usando tu runner (lo identifica por nombre en vez de "GitHub-hosted"), y terminar en verde. Si falla, revisa los logs del job — suele ser el sudoers mal cargado o una ruta distinta a `~/subir_horas`.

**Nota de seguridad:** un runner self-hosted en un repo público es sensible en general (cualquiera podría, en teoría, mandar un PR que corra código arbitrario en tu runner) — pero acá el trigger es solo `push` a `main` (nadie más que tú puede pushear ahí) y `workflow_dispatch`, no `pull_request`, así que un tercero no puede disparar una corrida.

---

## CI

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) corre en cada push y pull request (en runners normales de GitHub, no en el self-hosted), con tres jobs en paralelo:
- **Sintaxis Python**: `python -m py_compile` sobre todos los `.py` de `backend/`, `scripts/` y `backend_odoo.py` — solo chequea sintaxis, no importa nada, así que no necesita ninguna variable de entorno.
- **Sintaxis JS**: `node --check js/app.js`.
- **Tests**: corre [`tests/test_horas.py`](tests/test_horas.py) con `pytest` — cubre las funciones puras de `backend/horas.py` (días hábiles, parseo de fechas del buscador, validación de horas), sin hablar con Odoo ni Supabase de verdad. [`conftest.py`](conftest.py) (en la raíz) le da a `backend/config.py` variables de entorno dummy solo para que el paquete se pueda importar en el test runner.

Atrapa errores tontos (typos, paréntesis sin cerrar) y de lógica (ej. un cambio que rompa el cálculo de "día hábil anterior") antes de que un push dispare el deploy automático a la VM. No cubre el resto del backend (rutas, Odoo, Supabase) — eso requeriría mockear esas dependencias, con más esfuerzo y menos beneficio inmediato.

---

## Publicar el frontend en GitHub Pages

1. Edita `js/app.js`: reemplaza la constante `API_BASE` (primera línea) por la URL real de tu backend, sin barra final — la de Render.com (`https://tu-app.onrender.com`) o la de tu VM vía Tailscale Funnel (`https://tu-maquina.tu-tailnet.ts.net`), según dónde lo tengas desplegado. Tiene que coincidir con el host real: si el frontend apunta a un backend que ya no está corriendo, el login falla sin un error claro.
2. Commitea y pushea.
3. En GitHub: **Settings → Pages → Build and deployment → Deploy from a branch**, elige `main` y carpeta `/ (root)`.
4. GitHub te da una URL tipo `https://tu-usuario.github.io/subir_horas/`. Cópiala en `FRONTEND_ORIGINS` en el `.env` de la VM y reinicia el servicio (`sudo systemctl restart subir-horas`) para que el CORS la acepte.

> **Nota sobre cuentas Free:** GitHub Pages publica el sitio en una URL pública en internet aunque el repositorio origen sea privado — no hay control de acceso a nivel de Pages en cuentas Free/Pro (eso requiere GitHub Enterprise). Verifica en tu cuenta si Pages está habilitado para repos privados; si no, la alternativa es pasar el repo a público (el código no debería tener datos sensibles hardcodeados, pero repasalo antes). El acceso real a los datos de horas siempre queda detrás del login, así que exponer la página de login no es en sí un problema de seguridad — pero es bueno saberlo de antemano.

---

## Instalar como app (PWA)

El sitio trae `manifest.json` + un service worker mínimo (`sw.js`) para poder instalarse como app, sin pasar por ninguna tienda:

- **Android / Chrome de escritorio**: menú del navegador → "Instalar app" (o el ícono ⊕ en la barra de direcciones).
- **iPhone (Safari)**: botón compartir → "Agregar a pantalla de inicio".

Queda con ícono propio y abre en su propia ventana, sin barra de navegador — el reemplazo directo del `.exe` viejo, pero sin instalar nada de verdad. El service worker **no cachea datos** a propósito (`sw.js` solo existe para cumplir el requisito técnico de instalabilidad) — los datos de Odoo siempre se piden en vivo, nunca vas a ver algo desactualizado por caché.

Los íconos están en `icons/` (generados una vez, no hace falta regenerarlos salvo que quieras cambiar el diseño).

---

## Gestión de usuarios

**No hay registro abierto a propósito**: cualquiera con el link de GitHub Pages podría crearse una cuenta y elegir a qué tarjeta de Odoo cargarle horas si el alta fuera pública. En cambio, hay un panel de administración dentro de la propia app.

### Día a día: panel "Usuarios" (dentro de la app)

Si tu cuenta es admin, al loguearte ves una sección **Usuarios** con:
- Listado de usuarios existentes (tarjeta asignada, si es admin), con botones para **resetear contraseña** (🔑) o **eliminar** (🗑).
- Formulario para crear un usuario nuevo: usuario, tarjeta (eliges de la misma lista que ve el selector principal), contraseña inicial, y un checkbox "Es administrador".

Por detrás usa los endpoints `GET/POST /api/usuarios`, `POST /api/usuarios/<user>/resetear-password` y `DELETE /api/usuarios/<user>` — todos devuelven 403 si la sesión no es admin. Un admin no puede eliminarse a sí mismo (para no quedarse afuera por accidente).

Debajo del panel hay una tabla de **auditoría** (`GET /api/auditoria`, también solo admin) con las últimas 50 acciones: quién creó/eliminó un usuario o reseteó una contraseña, y cuándo. Vive en Postgres (Supabase) junto con el resto de los usuarios — persistente, no se pierde en cada redeploy.

### Bootstrap: el primer admin

El panel necesita que ya exista al menos un admin logueado. Aunque en la VM propia sí tienes acceso `sudo` y podrías correr [`scripts/crear_usuario.py`](scripts/crear_usuario.py) a mano, es más simple resolver el primer admin con tres variables de entorno (evita tener que loguearte a la VM solo para esto):

```
BOOTSTRAP_ADMIN_USERNAME=tu-usuario
BOOTSTRAP_ADMIN_PASSWORD=una-contraseña-inicial
BOOTSTRAP_ADMIN_TARJETA=Alex Perez
```

Al arrancar, el backend se fija si ya existe un usuario con ese `username`; si no existe, lo crea como admin con esa contraseña y tarjeta. Si ya existe, no hace nada — no pisa una contraseña que hayas cambiado después desde el panel. Cárgalas en el `.env` de la VM y reinicia el servicio (`sudo systemctl restart subir-horas`); con eso ya puedes loguearte en el sitio de GitHub Pages y usar el panel **Usuarios** para todo lo demás.

**Déjalas cargadas permanentemente** en el `.env` de todas formas (no las borres después del primer login): con Postgres persistente no hace falta que "recreen" el admin en cada reinicio del servicio, pero siguen siendo una red de seguridad útil, por ejemplo si en algún momento se recrea el proyecto de Supabase desde cero. Ojo con un detalle: si cambias la contraseña de `BOOTSTRAP_ADMIN_USERNAME` desde el panel y **después** el usuario se borra y se vuelve a crear (por ese escenario de recrear la base desde cero), vuelve a la contraseña que esté en `BOOTSTRAP_ADMIN_PASSWORD` (no la que hayas cambiado) — si quieres que el cambio sea permanente, actualiza también la variable de entorno.

[`scripts/crear_usuario.py`](scripts/crear_usuario.py) sigue siendo una alternativa por línea de comandos, corriéndolo desde tu máquina local (con `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` en tu `.env` apuntando al mismo proyecto de Supabase que usa producción):

```powershell
python scripts/crear_usuario.py <username> "<Nombre exacto de la tarjeta en Odoo>" --admin
python scripts/crear_usuario.py <username> --reset-password
```

---

## Flujo de actualización

| Qué cambiaste | Qué hacer |
|---|---|
| `index.html` (diseño, JS, comportamiento del formulario) | Commit + push a `main`. GitHub Pages lo redespliega solo en un minuto o dos. |
| `backend/` (endpoints, lógica de Odoo, auth) | Commit + push a `main`: se actualiza automáticamente si usas Render.com (o vía `git pull` en VM). Los usuarios viven en Supabase, así que un reinicio del servicio no los borra. |
| `.env` / variables de entorno del backend | Se editan en el panel del hosting (ej. Render.com Environment Variables) o en la VM. No requiere tocar el repo. |

### Subir cambios a GitHub (con GitHub Desktop)

1. Abre GitHub Desktop, selecciona el repo `subir_horas`.
2. Pestaña **Changes** — revisa que la lista de archivos modificados tenga sentido (y que **nunca** aparezca `.env`, `.venv/` o `__pycache__/`; si aparecen, algo falló con el `.gitignore`, no continúes).
3. Escribe un resumen del cambio y clic en **Commit to main**.
4. Clic en **Push origin** (arriba a la derecha) para subirlo a GitHub.

---

## Estructura del proyecto

```
subir_horas/
├── .env                  # credenciales locales (NO se sube a git)
├── .env.example           # plantilla sin datos reales
├── .gitignore
├── conftest.py             # env vars dummy para que pytest pueda importar backend/
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
│       ├── respaldo-supabase.yml        # backup semanal de auditoria
│       ├── ci.yml                       # chequeo de sintaxis Python/JS en cada push/PR
│       └── deploy.yml                   # deploy automático a VM (opcional)
├── deploy/
│   └── subir-horas.service # unit de systemd para correr el backend en VM
├── backend_odoo.py        # punto de entrada para gunicorn - solo crea la app
├── backend/                # paquete con toda la lógica del backend
│   ├── __init__.py          # create_app(): registra rutas y el guard de autenticación
│   ├── config.py             # variables de entorno y constantes
│   ├── db.py                 # Postgres (Supabase): usuarios y auditoría
│   ├── auth.py                # token de sesión y bloqueo por intentos fallidos
│   ├── odoo_client.py          # cliente JSON-RPC de Odoo + caché
│   ├── horas.py                # días hábiles, validaciones, resumen/recordatorio
│   └── routes/                 # un blueprint por área de la API
├── scripts/
│   └── crear_usuario.py    # CLI para crear/resetear usuarios
├── tests/
│   └── test_horas.py       # tests de las funciones puras de backend/horas.py
├── requirements.txt       # dependencias del backend
├── requirements-dev.txt   # requirements.txt + pytest (solo para desarrollo/CI)
├── .venv/                 # entorno virtual local (ignorado)
└── __pycache__/           # (ignorado)
```

Los usuarios ya no viven en un archivo local sino en Postgres, en Supabase — no hay ningún archivo de datos que gitignorar ni que se pierda al reiniciar el servicio.

`index.html` queda en la raíz porque GitHub Pages sirve ese nombre por convención en la raíz del sitio; `css/` y `js/` se referencian con rutas relativas (`css/style.css`, `js/app.js`). `backend_odoo.py` queda como un archivo mínimo en la raíz (`from backend import create_app; app = create_app()`) para que el comando de gunicorn (`gunicorn backend_odoo:app`) funcione limpiamente.

---

## Seguridad

- El `.env` contiene un token de API real con permisos de escritura sobre Odoo, y la `SUPABASE_SERVICE_ROLE_KEY` (acceso total a la base, bypassea Row Level Security). **Nunca** se commitea, ni se comparte por chat/capturas de pantalla sin tapar esos valores.
- Las tablas `usuarios` y `auditoria` tienen **Row Level Security activado sin políticas**: con eso, la API REST de Supabase con la `anon` key (pública) no puede leer ni escribir nada. Solo el backend, que usa la `service_role` key (bypassea RLS), accede a los datos. Si Supabase avisa `rls_disabled_in_public` en **Advisors → Security**, correr el `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` del bloque SQL de arriba.
- Si el token de Odoo llegara a exponerse accidentalmente, hay que **rotarlo** en Odoo lo antes posible. Si se expone `SUPABASE_SERVICE_ROLE_KEY`, regenerala desde el dashboard de Supabase (**Project Settings → API → Reset service_role key**).
- Las contraseñas de los usuarios de la app se guardan **hasheadas** (`werkzeug.security`), nunca en texto plano, en la tabla `usuarios` de Postgres.
- CORS en el backend está restringido a los orígenes listados en `FRONTEND_ORIGINS` (no `CORS(app)` abierto). Si en algún momento agregas otro dominio desde el que se sirva el frontend, hay que sumarlo ahí.
- El login se bloquea 5 minutos para un usuario tras 5 intentos fallidos seguidos (mitiga fuerza bruta básica). El contador vive en memoria del proceso.
- El login usa un **token firmado** (`itsdangerous`, con `SECRET_KEY`), no una cookie — se eligió así porque las cookies cross-site (`SameSite=None; Secure`) quedan bloqueadas por defecto en varios navegadores (Safari, Brave, Samsung Internet). El token vive en `localStorage` del navegador y viaja en el header `Authorization`. Expira solo a las `SESSION_LIFETIME_HORAS` de haberse emitido.
- El sitio publicado en GitHub Pages es **público en internet** aunque el repositorio sea privado. El login es lo único que protege el acceso a los datos de horas.
- El empleado de cada línea de horas se resuelve automáticamente según quién está **asignado a la subtarea** (`project.task.user_ids`), no según qué usuario de la app hizo el request.

---

## Decisiones técnicas

Por si en unos meses hay que recordar el "por qué":

- **JSON-RPC, no XML-RPC**: el `ODOO_URL` de esta instancia de Assertiva ya apunta al endpoint `/jsonrpc`, así que el backend usa `requests` con el formato JSON-RPC 2.0 de Odoo (`service: "object"`, `method: "execute_kw"`) en vez de `xmlrpc.client`.
- **`ODOO_UID` fijo en vez de `authenticate()`**: se usa un UID ya resuelto, evitando una llamada extra de autenticación en cada request.
- **Empleado resuelto por tarea, no por sesión**: Odoo lo determina según quién está asignado a la subtarea específica (`user_ids` de `project.task`), independientemente de qué credencial hizo la llamada API.
- **Filtro por tarjeta padre (`parent_id.name`) al buscar subtareas**: nombres de subtareas como "Carga de Horas" se repiten en las tarjetas de distintas personas dentro del mismo proyecto. Sin este filtro, la búsqueda podía devolver la subtarea de otra persona y cargar las horas en el lugar equivocado.
- **Login propio en vez de credenciales de Odoo**: cada usuario de la app tiene su cuenta (usuario/contraseña + tarjeta asignada) en Postgres, separada de cualquier login de Odoo.
- **Backend y frontend separados**: GitHub Pages no puede correr Flask; se necesitaba un servicio aparte para la lógica con estado (usuarios, base de datos) y el secreto de Odoo.
- **Postgres en Supabase en vez de SQLite local**: garantiza almacenamiento persistente e independiente de los reinicios y despliegues del backend.
- **Token en `localStorage` en vez de cookie de sesión**: evita bloqueos de cookies cross-site en Safari, Brave y Samsung Internet.
- **`requests.Session()` compartida en `odoo_client.py`**: reusa la conexión TCP/TLS a Odoo entre llamadas para mejorar el rendimiento.

---

## Monitoreo

Recomendado: [UptimeRobot](https://uptimerobot.com) (free tier alcanza de sobra), un monitor HTTP pegándole a tu backend (ej. `https://tu-app.onrender.com/`) cada 10 minutos para mantenerlo despierto y alertar por email si deja de responder.

---

## Recuperación ante desastres

**Lo que no se pierde:** los datos (usuarios y auditoría) viven en Supabase, totalmente independiente del host del backend.

**Respaldo adicional de Supabase:** el workflow [`.github/workflows/respaldo-supabase.yml`](.github/workflows/respaldo-supabase.yml) exporta `auditoria` como artifact de GitHub Actions todos los lunes. La tabla `usuarios` (tiene `password_hash`) queda afuera de ese workflow a propósito. Para respaldarla manualmente:
```bash
curl -fsS "$SUPABASE_URL/rest/v1/usuarios?select=*" \
  -H "apikey: $SUPABASE_SERVICE_ROLE_KEY" \
  -H "Authorization: Bearer $SUPABASE_SERVICE_ROLE_KEY" \
  -o usuarios-backup-$(date +%Y%m%d).json
```

---

## Problemas comunes

**`xmlrpc.client.ProtocolError: 404 NOT FOUND`**
El `ODOO_URL` en `.env` ya incluye `/jsonrpc`. Si se ve este error, alguna parte del código está intentando hablar XML-RPC en vez de JSON-RPC — revisar que se esté usando `odoo_execute_kw()` y no `xmlrpc.client` directo.

**Las horas caen en la tarjeta de otra persona**
Revisar que `buscar_tarea_id()` esté filtrando por `parent_id.name` correctamente, y que el valor de `tarjeta` enviado desde el formulario sea el nombre exacto de la tarjeta en Odoo.

**Error de campo inexistente al crear el registro**
Correr `GET /api/campos?modelo=<modelo>&q=<palabra>` (como admin) para confirmar el nombre técnico real del campo en esta instancia (varios campos están personalizados vía Odoo Studio, ej. `x_studio_*`).

**El navegador bloquea las llamadas al backend (error de CORS) / la página queda en negro**
`FRONTEND_ORIGINS` en el backend no incluye el origen exacto desde el que estás sirviendo `index.html`: tiene que ser **solo protocolo + dominio** (ej. `https://tu-usuario.github.io`), sin la ruta del repo (`/subir_horas`) ni barra final — el navegador manda el header `Origin` sin la ruta, así que si la dejas puesta no matchea nunca. Revisar el `.env` en la VM y `sudo systemctl restart subir-horas`. (Si la página queda completamente en blanco/negro sin mostrar ni el login, confirma que estás en la versión más reciente de `js/app.js` — versiones viejas no manejaban este error y se quedaban sin mostrar nada).

**Me loguea bien pero después cada request da 401 ("no autenticado")**
El token puede haber expirado (dura `SESSION_LIFETIME_HORAS`, default 8) — vuelve a loguearte. Si pasa inmediatamente después de loguearte, revisa en las herramientas de desarrollador (Network) que el pedido a `/api/whoami` esté mandando el header `Authorization: Bearer ...` — si no lo manda, puede ser que `localStorage` esté deshabilitado o bloqueado (modo incógnito estricto, alguna extensión).

**No puedo loguearme después de reiniciar el backend**
Con Postgres en Supabase esto no debería pasar (los usuarios persisten entre reinicios del servicio). Si pasa: revisa que `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` en el `.env` de la VM sean exactamente los mismos que venías usando (un typo o apuntar a otro proyecto de Supabase por error crea/usa una base vacía). Si además tienes `BOOTSTRAP_ADMIN_*` cargadas, al menos ese admin se recrea solo — reinicia el servicio y reintenta con esas credenciales.

**El backend tarda muchísimo o nunca responde (incluso `curl http://127.0.0.1:8000/` local se cuelga)**
Si el arranque del proceso se queda colgado silenciosamente (sin error, pero tampoco responde ningún request), sospecha primero de la conexión a Supabase — con la API REST esto no debería pasar (es HTTPS/443, igual que cualquier navegación web normal), pero si por error quedó configurado algo que intenta una conexión directa a Postgres (puertos 5432/6543) en una red que bloquea esos puertos, el proceso se cuelga esperando un timeout de TCP que puede tardar minutos. Revisa `journalctl -u subir-horas -n 50 --no-pager` y confirma que `SUPABASE_URL` (no `DATABASE_URL`) esté cargada.

**El servicio no arranca / `sudo systemctl status subir-horas` muestra `failed`**
`journalctl -u subir-horas -n 50 --no-pager` muestra el error real (falta una variable de entorno obligatoria, rutas mal puestas en el `.service`, el venv no tiene las dependencias instaladas, etc.). Los errores de configuración faltante (`config.py`) salen ahí con un mensaje explícito de qué variable falta.

**La URL de Tailscale Funnel no responde desde afuera**
`sudo tailscale funnel status` confirma que el Funnel sigue activo (se desactiva si reinicias la VM y no configuraste que arranque solo — revisar `tailscale up` con las flags de persistencia, o simplemente volver a correr `sudo tailscale funnel 8000` tras un reinicio). También confirma que el servicio de systemd esté `active (running)` — Funnel solo expone lo que ya está escuchando en `127.0.0.1:8000`, no lo levanta él.
