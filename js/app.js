const API_BASE = 'https://subir-horas.onrender.com';
const TOKEN_KEY = 'registro_horas_token';
const ULTIMA_TARJETA_KEY = 'registro_horas_ultima_tarjeta';
const UMBRAL_HORAS_ALTAS = 9;
let ES_ADMIN = false;
let MI_TARJETA = '';

function ultimaSubtareaKey(tarjeta){ return 'registro_horas_ultima_subtarea:' + tarjeta; }

// Habilita "Instalar app" / "Agregar a pantalla de inicio" (PWA).
if('serviceWorker' in navigator){
  navigator.serviceWorker.register('sw.js').catch(() => { /* no bloquea el uso normal si falla */ });
}

document.getElementById('fecha').valueAsDate = new Date();
document.getElementById('fechaConsulta').valueAsDate = new Date();

// Autenticación por token (no por cookie): frontend y backend viven en
// dominios distintos, y varios navegadores (Safari, Brave, Samsung
// Internet...) bloquean por defecto las cookies cross-site aunque
// tengan SameSite=None; Secure. Guardamos el token en localStorage y
// lo mandamos como header en cada pedido en vez de depender de cookies.
const EXPIRA_KEY = 'registro_horas_expira_ts';
const AVISO_EXPIRACION_MS = 10 * 60 * 1000;

function getToken(){ return localStorage.getItem(TOKEN_KEY); }
function setToken(token){ localStorage.setItem(TOKEN_KEY, token); }
function clearToken(){ localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(EXPIRA_KEY); }

function api(path, options){
  options = options || {};
  const headers = Object.assign({}, options.headers);
  const token = getToken();
  if(token) headers['Authorization'] = 'Bearer ' + token;
  return fetch(API_BASE + path, Object.assign({}, options, { headers }));
}

// Datos que vienen de Odoo/la DB (descripciones, nombres de tarjeta/subtarea,
// usernames) o mensajes de error del backend (que a veces reflejan un valor
// pedido, ej. un nombre de subtarea) se insertan en varios lugares vía
// innerHTML - hay que escaparlos para que no puedan inyectar HTML/JS.
function escapeHTML(str){
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// Para valores interpolados dentro de un onclick="funcion('...')": el
// navegador decodifica las entidades HTML del atributo *antes* de
// interpretarlo como JS, así que escapar solo caracteres HTML (escapeHTML)
// no evita que un valor con una comilla simple rompa el string JS. Usamos
// JSON.stringify para un literal JS válido y escapamos nada más las
// comillas dobles resultantes, para poder embeberlo en el atributo HTML
// (que también usa comillas dobles) sin cerrarlo antes de tiempo.
function jsAttr(valor){
  return JSON.stringify(String(valor)).replace(/"/g, '&quot;');
}

async function inicializar(){
  if(!getToken()){
    document.getElementById('appRoot').style.display = 'none';
    document.getElementById('loginBox').style.display = 'block';
    return;
  }

  let res;
  try{
    res = await api('/api/whoami');
  } catch(e){
    // Backend caído o dormido (Render free): mostramos el login igual,
    // con el motivo, en vez de dejar la página en blanco.
    document.getElementById('appRoot').style.display = 'none';
    document.getElementById('loginBox').style.display = 'block';
    const statusEl = document.getElementById('loginStatus');
    statusEl.className = 'status err';
    statusEl.textContent = 'No se pudo conectar al backend (' + e.message + '). Puede estar "despertando" (Render free) - probá reintentar en unos segundos.';
    return;
  }
  if(res.status === 401){
    clearToken();
    document.getElementById('appRoot').style.display = 'none';
    document.getElementById('loginBox').style.display = 'block';
    return;
  }
  const yo = await res.json();
  ES_ADMIN = !!yo.es_admin;
  MI_TARJETA = yo.tarjeta;

  document.getElementById('loginBox').style.display = 'none';
  // '' (no 'block'): un valor inline fijo le ganaría en especificidad al
  // "display:flex" que el media query de desktop le pone a #appRoot para
  // el layout con sidebar, y la app quedaría en blanco en pantallas anchas.
  document.getElementById('appRoot').style.display = '';
  iniciarMonitoreoBackend();

  document.getElementById('userbox').innerHTML =
    '<button type="button" class="avatar-btn" id="avatarBtn" aria-haspopup="true" aria-expanded="false">' +
      escapeHTML((yo.username || '?').charAt(0).toUpperCase()) +
    '</button>' +
    '<div class="acc-menu" id="accMenu">' +
      '<div class="acc-who"><b>' + escapeHTML(yo.username) + '</b> · ' + escapeHTML(yo.tarjeta) + '</div>' +
      '<div class="acc-actions">' +
        '<button type="button" class="acc-item" onclick="toggleCambiarPassword(true)">Cambiar contraseña</button>' +
        '<button type="button" class="acc-item" onclick="cerrarSesion()">Cerrar sesión</button>' +
      '</div>' +
    '</div>';
  wireAvatarMenu();

  if(ES_ADMIN){
    document.getElementById('labelTarjeta').style.display = '';
    document.getElementById('tarjeta').style.display = '';
    document.getElementById('tarjetaFija').style.display = 'none';
    cargarTarjetas();
    cargarUsuarios();
    cargarAuditoria();
  } else {
    document.getElementById('labelTarjeta').style.display = 'none';
    document.getElementById('tarjeta').style.display = 'none';
    const fija = document.getElementById('tarjetaFija');
    fija.style.display = 'block';
    fija.textContent = yo.tarjeta;
    cargarSubtareas();
  }

  actualizarVisibilidadTabs();
  verificarRecordatorio();
  cargarResumen();
  cargarHeatmap();
  // cargarFaltantesMes() no se llama acá: ya la dispara cargarSubtareas()
  // (llamada arriba, directo o vía cargarTarjetas()) para evitar un
  // segundo pedido duplicado en cada carga de la app.
}

// En mobile el avatar abre un menú desplegable con las acciones de cuenta
// (en desktop el CSS lo muestra siempre expandido y el botón queda oculto).
function wireAvatarMenu(){
  const btn = document.getElementById('avatarBtn');
  const menu = document.getElementById('accMenu');
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    const abierto = menu.classList.toggle('abierto');
    btn.setAttribute('aria-expanded', abierto ? 'true' : 'false');
  });
  if(window._accMenuGlobalWired) return;
  window._accMenuGlobalWired = true;
  const cerrarMenu = () => {
    const m = document.getElementById('accMenu');
    const b = document.getElementById('avatarBtn');
    if(m) m.classList.remove('abierto');
    if(b) b.setAttribute('aria-expanded', 'false');
  };
  document.addEventListener('click', cerrarMenu);
  document.addEventListener('keydown', (e) => { if(e.key === 'Escape') cerrarMenu(); });
}

let TAB_ACTIVA = 'registrar';

function actualizarVisibilidadTabs(){
  const paneles = {
    resumen: document.getElementById('tabResumen'),
    registrar: document.getElementById('tabRegistrar'),
    dias: document.getElementById('tabDias'),
    admin: document.getElementById('panelUsuarios'),
  };
  Object.keys(paneles).forEach(nombre => {
    const el = paneles[nombre];
    if(!el) return;
    if(nombre === 'admin' && !ES_ADMIN){ el.style.display = 'none'; return; }
    el.style.display = (nombre === TAB_ACTIVA) ? '' : 'none';
  });
  document.getElementById('tabbarAdminBtn').style.display = ES_ADMIN ? '' : 'none';
  document.getElementById('sidebarAdminBtn').style.display = ES_ADMIN ? '' : 'none';
  document.querySelectorAll('#tabbar button, .sidebar-nav button').forEach(b => {
    b.classList.toggle('activo', b.dataset.tab === TAB_ACTIVA);
  });
}

function mostrarTab(nombre){
  TAB_ACTIVA = nombre;
  actualizarVisibilidadTabs();
  window.scrollTo(0, 0);
}

let _intervaloBackend = null;
let _timeoutDespertando = null;

function _setEstadoBackend(clase, texto, titulo){
  const el = document.getElementById('estadoBackend');
  el.className = 'estado-backend ' + clase;
  el.innerHTML = '<span class="punto"></span>' + texto;
  if(titulo) el.title = titulo; else el.removeAttribute('title');
}

async function verificarBackend(){
  clearTimeout(_timeoutDespertando);
  _setEstadoBackend('estado-conectando', 'Conectando...');
  _timeoutDespertando = setTimeout(() => {
    _setEstadoBackend('estado-conectando', 'Despertando servidor...', 'El backend gratuito (Render) duerme tras 15 min sin uso y puede tardar hasta un minuto en responder.');
  }, 3000);

  try{
    const res = await api('/');
    clearTimeout(_timeoutDespertando);
    _setEstadoBackend(res.ok ? 'estado-ok' : 'estado-error', res.ok ? 'Conectado' : 'Sin conexión', res.ok ? '' : 'El backend respondió con error ' + res.status + '.');
  } catch(e){
    clearTimeout(_timeoutDespertando);
    _setEstadoBackend('estado-error', 'Sin conexión', e.message);
  }
}

function iniciarMonitoreoBackend(){
  verificarBackend();
  verificarExpiracionSesion();
  if(_intervaloBackend) return;
  _intervaloBackend = setInterval(() => {
    verificarBackend();
    verificarExpiracionSesion();
  }, 45000);
}

function verificarExpiracionSesion(){
  const banner = document.getElementById('bannerExpiracion');
  const expiraTs = parseInt(localStorage.getItem(EXPIRA_KEY), 10);
  if(!expiraTs){ banner.style.display = 'none'; return; }

  const restanteMs = expiraTs - Date.now();
  if(restanteMs <= 0){
    cerrarSesion();
    const statusEl = document.getElementById('loginStatus');
    statusEl.className = 'status err';
    statusEl.textContent = 'Tu sesión expiró. Volvé a ingresar.';
    return;
  }
  if(restanteMs <= AVISO_EXPIRACION_MS){
    banner.style.display = 'block';
    banner.textContent = '⏰ Tu sesión expira en ' + Math.ceil(restanteMs / 60000) + ' min. Guardá lo que estés haciendo y volvé a ingresar para renovarla.';
  } else {
    banner.style.display = 'none';
  }
}

async function cargarResumen(){
  try{
    const res = await api('/api/resumen');
    const data = await res.json();
    document.getElementById('resumenSemana').textContent = data.semana.toFixed(1) + 'h';
    document.getElementById('resumenMes').textContent = data.mes.toFixed(1) + 'h';
    renderGraficoSubtareas(data.por_subtarea || []);
    renderMetaSemanal(data.semana);
  } catch(e){
    document.getElementById('resumenSemana').textContent = '—';
    document.getElementById('resumenMes').textContent = '—';
  }
}

// Meta semanal de horas: puramente local (localStorage) - no hay concepto
// de objetivo en Odoo, así que cada quien la fija a su gusto en su propio
// navegador. Default 40h si nunca se configuró.
const META_SEMANAL_KEY = 'registro_horas_meta_semanal';

function metaSemanalActual(){
  const v = parseFloat(localStorage.getItem(META_SEMANAL_KEY));
  return (v && v > 0) ? v : 40;
}

function renderMetaSemanal(horasSemana){
  const meta = metaSemanalActual();
  const pct = Math.max(0, Math.min(100, (horasSemana / meta) * 100));
  const fill = document.getElementById('metaFill');
  fill.style.width = pct.toFixed(0) + '%';
  fill.classList.toggle('completa', horasSemana >= meta);
  document.getElementById('metaSemanalValor').textContent = horasSemana.toFixed(1) + ' / ' + meta + 'h';
}

async function editarMetaSemanal(){
  const nueva = await pedirTexto('Horas objetivo por semana:', {
    titulo: 'Meta semanal', valorInicial: String(metaSemanalActual()), tipo: 'number', textoAceptar: 'Guardar'
  });
  if(nueva === null) return;
  const n = parseFloat(nueva);
  if(!n || n <= 0) return;
  localStorage.setItem(META_SEMANAL_KEY, String(n));
  cargarResumen();
}

function renderGraficoSubtareas(porSubtarea){
  const cont = document.getElementById('graficoSubtareas');
  if(porSubtarea.length === 0){
    cont.innerHTML = '<p class="empty">Sin horas cargadas esta semana todavía.</p>';
    return;
  }
  const maxHoras = Math.max(...porSubtarea.map(s => s.horas));
  cont.innerHTML = porSubtarea.map(s => `
    <div class="barra-fila">
      <span class="barra-label" title="${escapeHTML(s.subtarea)}">${escapeHTML(s.subtarea)}</span>
      <div class="barra-track"><div class="barra-fill" style="width:${(s.horas / maxHoras * 100).toFixed(0)}%"></div></div>
      <span class="barra-valor">${s.horas.toFixed(1)}h</span>
    </div>`).join('');
}

async function cargarHeatmap(){
  const cont = document.getElementById('heatmapDias');
  try{
    const res = await api('/api/dias-cargados?dias=30');
    const data = await res.json();
    const porFecha = {};
    data.dias.forEach(d => { porFecha[d.fecha] = d.horas; });

    const hoy = new Date();
    const celdas = [];
    for(let i = 29; i >= 0; i--){
      const d = new Date(Date.UTC(hoy.getFullYear(), hoy.getMonth(), hoy.getDate()));
      d.setUTCDate(d.getUTCDate() - i);
      const iso = d.toISOString().slice(0, 10);
      const horas = porFecha[iso] || 0;
      const nivel = horas === 0 ? 0 : horas < 2 ? 1 : horas < 4 ? 2 : horas < 6 ? 3 : 4;
      celdas.push(`<span class="heatmap-celda nivel-${nivel}" title="${formatearFecha(iso)}: ${horas.toFixed(1)}h"></span>`);
    }
    cont.innerHTML = celdas.join('');
  } catch(e){
    cont.innerHTML = '<p class="empty">No se pudo cargar la actividad reciente.</p>';
  }
}

function primerDiaDelMesISO(){
  const hoy = new Date();
  return hoy.getFullYear() + '-' + String(hoy.getMonth() + 1).padStart(2, '0') + '-01';
}

async function cargarFaltantesMes(){
  const cont = document.getElementById('listaFaltantesMes');
  try{
    const tarjeta = tarjetaActual();
    const url = '/api/dias-faltantes?desde=' + primerDiaDelMesISO() + '&tarjeta=' + encodeURIComponent(tarjeta);
    const res = await api(url);
    const data = await res.json();

    if(data.error){
      cont.innerHTML = '<p class="empty">' + escapeHTML(data.error) + '</p>';
      return;
    }
    if(data.faltantes.length === 0){
      cont.innerHTML = '<p class="status ok">Al día con este mes ✔</p>';
      return;
    }
    cont.innerHTML = '<div class="status err">' + data.faltantes.length + ' día(s) sin horas cargadas este mes:</div><div class="chips" style="margin-top:8px;">' +
      data.faltantes.map(f => `<button type="button" class="chip" onclick="irACargarFecha('${f}')">${formatearFecha(f)}</button>`).join('') + '</div>';
  } catch(e){
    cont.innerHTML = '<p class="empty">No se pudo revisar los días del mes.</p>';
  }
}

async function iniciarSesion(){
  const username = document.getElementById('loginUsername').value.trim();
  const password = document.getElementById('loginPassword').value;
  const btn = document.getElementById('btnLogin');
  const statusEl = document.getElementById('loginStatus');

  if(!username || !password){
    statusEl.className = 'status err';
    statusEl.textContent = 'Completa usuario y contraseña.';
    return;
  }

  btn.disabled = true;
  statusEl.className = 'status';
  statusEl.textContent = 'Ingresando...';

  try{
    const res = await api('/api/login', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({username, password})
    });
    const data = await res.json();
    if(!res.ok || data.error){
      statusEl.className = 'status err';
      statusEl.textContent = data.error || 'No se pudo ingresar.';
      return;
    }
    setToken(data.token);
    if(data.expira_en_segundos){
      localStorage.setItem(EXPIRA_KEY, String(Date.now() + data.expira_en_segundos * 1000));
    }
    document.getElementById('loginPassword').value = '';
    inicializar();
  } catch(e){
    statusEl.className = 'status err';
    statusEl.textContent = 'No se pudo conectar al backend: ' + e.message;
  } finally {
    btn.disabled = false;
  }
}

function cerrarSesion(){
  // Token sin estado del lado del servidor: "cerrar sesión" es
  // simplemente olvidarlo acá. Expira solo de todas formas (ver
  // SESSION_LIFETIME_HORAS en el backend).
  clearToken();
  document.getElementById('appRoot').style.display = 'none';
  document.getElementById('loginBox').style.display = 'block';
  document.getElementById('loginUsername').value = '';
  document.getElementById('loginPassword').value = '';
  document.getElementById('loginStatus').textContent = '';
}

// Atrapa el foco de teclado (Tab/Shift+Tab) dentro de un modal abierto, y
// cierra con Escape - sin esto, Tab puede escapar al contenido de atrás y
// un lector de pantalla no sabe que hay un diálogo modal activo. Devuelve
// una función para soltar el trap cuando se cierra el modal.
function _elementosFocuseables(contenedor){
  return Array.from(contenedor.querySelectorAll(
    'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
  )).filter(el => el.offsetParent !== null);
}

function _atraparFoco(contenedor, alCerrar){
  function onKeydown(e){
    if(e.key === 'Escape'){
      alCerrar();
      return;
    }
    if(e.key !== 'Tab') return;
    const focuseables = _elementosFocuseables(contenedor);
    if(focuseables.length === 0) return;
    const primero = focuseables[0];
    const ultimo = focuseables[focuseables.length - 1];
    if(e.shiftKey && document.activeElement === primero){
      e.preventDefault();
      ultimo.focus();
    } else if(!e.shiftKey && document.activeElement === ultimo){
      e.preventDefault();
      primero.focus();
    }
  }
  document.addEventListener('keydown', onKeydown);
  return () => document.removeEventListener('keydown', onKeydown);
}

function _modalGenerico({titulo, mensaje, conInput, valorInicial, tipoInput, textoAceptar, soloAceptar}){
  return new Promise(resolve => {
    const backdrop = document.getElementById('modalGenericoBackdrop');
    const inputEl = document.getElementById('modalGenericoInput');
    const btnAceptar = document.getElementById('modalGenericoAceptar');
    const btnCancelar = document.getElementById('modalGenericoCancelar');

    document.getElementById('modalGenericoTitulo').textContent = titulo;
    document.getElementById('modalGenericoMensaje').textContent = mensaje;
    btnAceptar.textContent = textoAceptar || 'Aceptar';
    btnCancelar.style.display = soloAceptar ? 'none' : '';

    if(conInput){
      inputEl.style.display = '';
      inputEl.type = tipoInput || 'text';
      inputEl.value = valorInicial || '';
    } else {
      inputEl.style.display = 'none';
    }

    let soltarFoco = null;
    function limpiar(){
      backdrop.style.display = 'none';
      btnAceptar.onclick = null;
      btnCancelar.onclick = null;
      inputEl.onkeydown = null;
      if(soltarFoco){ soltarFoco(); soltarFoco = null; }
    }
    function aceptar(){
      const valor = conInput ? inputEl.value : true;
      limpiar();
      resolve(valor);
    }
    function cancelar(){
      limpiar();
      resolve(conInput ? null : false);
    }

    btnAceptar.onclick = aceptar;
    btnCancelar.onclick = cancelar;
    inputEl.onkeydown = e => { if(e.key === 'Enter') aceptar(); };

    backdrop.style.display = 'flex';
    soltarFoco = _atraparFoco(backdrop.querySelector('.modal'), cancelar);
    setTimeout(() => (conInput ? inputEl : btnAceptar).focus(), 30);
  });
}

function confirmarAccion(mensaje, titulo, textoAceptar){
  return _modalGenerico({titulo: titulo || 'Confirmar', mensaje, textoAceptar: textoAceptar || 'Confirmar'});
}

function pedirTexto(mensaje, opciones){
  opciones = opciones || {};
  return _modalGenerico({
    titulo: opciones.titulo || 'Ingresar dato', mensaje, conInput: true,
    valorInicial: opciones.valorInicial, tipoInput: opciones.tipo || 'text',
    textoAceptar: opciones.textoAceptar || 'Aceptar'
  });
}

function mostrarAlerta(mensaje, titulo){
  return _modalGenerico({titulo: titulo || 'Aviso', mensaje, soloAceptar: true, textoAceptar: 'Aceptar'});
}

let _soltarFocoCP = null;

function toggleCambiarPassword(mostrar){
  const backdrop = document.getElementById('cambiarPasswordBackdrop');
  backdrop.style.display = mostrar ? 'flex' : 'none';
  if(mostrar){
    document.getElementById('cpActual').value = '';
    document.getElementById('cpNueva').value = '';
    document.getElementById('cpConfirmar').value = '';
    document.getElementById('cpStatus').textContent = '';
    _soltarFocoCP = _atraparFoco(backdrop.querySelector('.modal'), () => toggleCambiarPassword(false));
    setTimeout(() => document.getElementById('cpActual').focus(), 30);
  } else if(_soltarFocoCP){
    _soltarFocoCP();
    _soltarFocoCP = null;
  }
}

async function guardarCambioPassword(){
  const actual = document.getElementById('cpActual').value;
  const nueva = document.getElementById('cpNueva').value;
  const confirmar = document.getElementById('cpConfirmar').value;
  const statusEl = document.getElementById('cpStatus');

  statusEl.className = 'status';
  statusEl.textContent = 'Guardando...';

  try{
    const res = await api('/api/cambiar-password', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({actual, nueva, confirmar})
    });
    const data = await res.json();
    if(!res.ok || data.error){
      statusEl.className = 'status err';
      statusEl.textContent = data.error || 'No se pudo cambiar la contraseña.';
      return;
    }
    statusEl.className = 'status ok';
    statusEl.textContent = 'Contraseña actualizada.';
    setTimeout(() => toggleCambiarPassword(false), 1200);
  } catch(e){
    statusEl.className = 'status err';
    statusEl.textContent = 'No se pudo conectar al backend: ' + e.message;
  }
}

async function verificarRecordatorio(){
  try{
    const res = await api('/api/recordatorio');
    const data = await res.json();
    if(data.pendiente){
      const [y,m,d] = data.fecha.split('-');
      const banner = document.getElementById('banner');
      banner.style.display = 'block';
      banner.textContent = 'No tienes horas registradas el ' + d + '/' + m + '/' + y + '. ¿Se te olvidó cargarlas?';
    }
  } catch(e){ /* silencioso: no bloquea el uso normal si falla */ }
}

function tarjetaActual(){
  return ES_ADMIN ? document.getElementById('tarjeta').value : document.getElementById('tarjetaFija').textContent;
}

function setFecha(offsetDias){
  const d = new Date();
  d.setDate(d.getDate() + offsetDias);
  document.getElementById('fecha').valueAsDate = d;
}

function diasHabilesEnRango(desde, hasta){
  const [y1, m1, d1] = desde.split('-').map(Number);
  const [y2, m2, d2] = hasta.split('-').map(Number);
  const cur = new Date(Date.UTC(y1, m1 - 1, d1));
  const fin = new Date(Date.UTC(y2, m2 - 1, d2));
  const dias = [];
  while(cur <= fin){
    const dow = cur.getUTCDay();
    if(dow !== 0 && dow !== 6) dias.push(cur.toISOString().slice(0, 10));
    cur.setUTCDate(cur.getUTCDate() + 1);
  }
  return dias;
}

function alternarModoLote(){
  const activo = document.getElementById('loteToggle').checked;
  document.getElementById('fechaUnica').style.display = activo ? 'none' : '';
  document.getElementById('fechaRango').style.display = activo ? '' : 'none';
  document.getElementById('horasLote').style.display = activo ? '' : 'none';
  document.getElementById('btnRegistrar').textContent = activo ? 'Registrar en lote (Odoo)' : 'Registrar en Odoo';
  actualizarPreviewLote();
}

function actualizarPreviewLote(){
  const el = document.getElementById('previewLote');
  const desde = document.getElementById('fechaDesde').value;
  const hasta = document.getElementById('fechaHasta').value;
  if(!desde || !hasta){ el.textContent = ''; el.className = 'status'; return; }
  if(hasta < desde){ el.textContent = '"Hasta" debe ser igual o posterior a "Desde".'; el.className = 'status err'; return; }
  const dias = diasHabilesEnRango(desde, hasta);
  el.className = 'status';
  el.textContent = dias.length === 0
    ? 'No hay días hábiles (lun-vie) en ese rango.'
    : dias.length + ' día' + (dias.length === 1 ? '' : 's') + ' hábil' + (dias.length === 1 ? '' : 'es') + ': ' + dias.map(formatearFecha).join(', ');
}

function renderChipsDescripcion(lineas){
  const cont = document.getElementById('chipsDescripcion');
  const unicas = [...new Set(lineas.map(l => l.name).filter(Boolean))].slice(0, 5);
  if(unicas.length === 0){ cont.innerHTML = ''; return; }
  cont.innerHTML = unicas.map(desc =>
    `<button type="button" class="chip" title="${escapeHTML(desc)}" onclick="usarDescripcion(this)">${escapeHTML(desc)}</button>`
  ).join('');
}

function usarDescripcion(btn){
  document.getElementById('detalle').value = btn.title;
}

// Prellena subtarea/horas/descripción con la última línea cargada en
// cualquier subtarea de la tarjeta (no solo la seleccionada) - para no
// tener que rearmar a mano un registro que se repite día a día. La fecha
// del formulario se deja como está (normalmente "hoy"): la idea es repetir
// el contenido, no la fecha vieja.
async function repetirUltimoRegistro(btn){
  if(btn) btn.disabled = true;
  try{
    const tarjeta = tarjetaActual();
    const res = await api('/api/timesheet/ultimo?tarjeta=' + encodeURIComponent(tarjeta));
    const data = await res.json();
    if(!data.ultimo){
      mostrarStatus('No hay registros previos en esta tarjeta para repetir.', 'err');
      return;
    }
    const sel = document.getElementById('subtarea');
    const existe = Array.from(sel.options).some(o => o.value === data.ultimo.subtarea);
    if(existe && sel.value !== data.ultimo.subtarea){
      sel.value = data.ultimo.subtarea;
      localStorage.setItem(ultimaSubtareaKey(tarjeta), data.ultimo.subtarea);
      cargarHistorial();
    }
    document.getElementById('horas').value = data.ultimo.horas;
    document.getElementById('detalle').value = data.ultimo.detalle || '';
    mostrarStatus('Prellenado con tu último registro (' + formatearFecha(data.ultimo.fecha) + '). Revisá la fecha y guardá.', 'ok');
  } catch(e){
    mostrarStatus('No se pudo conectar al backend: ' + escapeHTML(e.message), 'err');
  } finally {
    if(btn) btn.disabled = false;
  }
}

async function cargarTarjetas(){
  const sel = document.getElementById('tarjeta');
  try{
    const res = await api('/api/tarjetas');
    const tarjetas = await res.json();
    const opciones = tarjetas.map(t => `<option value="${escapeHTML(t.name)}">${escapeHTML(t.name)}</option>`).join('');
    sel.innerHTML = opciones;
    // Como admin, el desplegable trae todas las tarjetas del proyecto:
    // preseleccionamos la última usada (si seguís logueado en el mismo
    // navegador) o si no, la propia - en vez de dejar que quede la
    // primera de la lista (que es de quien sea que Odoo devuelva primero).
    const ultimaTarjeta = localStorage.getItem(ULTIMA_TARJETA_KEY);
    if(ultimaTarjeta && tarjetas.some(t => t.name === ultimaTarjeta)){
      sel.value = ultimaTarjeta;
    } else if(MI_TARJETA && tarjetas.some(t => t.name === MI_TARJETA)){
      sel.value = MI_TARJETA;
    }
    const selNuevoUsuario = document.getElementById('nuevoUsuarioTarjeta');
    if(selNuevoUsuario) selNuevoUsuario.innerHTML = opciones;
    cargarSubtareas();
  } catch(e){
    sel.innerHTML = '<option>Error cargando tarjetas</option>';
    mostrarStatus('No se pudo conectar al backend (' + escapeHTML(e.message) + '). ¿Está corriendo backend_odoo.py?', 'err');
  }
}

async function cargarUsuarios(){
  const tbody = document.getElementById('tbodyUsuarios');
  try{
    const res = await api('/api/usuarios');
    const usuarios = await res.json();
    if(usuarios.length === 0){
      tbody.innerHTML = '<tr><td colspan="4" class="empty">Sin usuarios todavía.</td></tr>';
      return;
    }
    tbody.innerHTML = usuarios.map(u => `
      <tr>
        <td class="desc">${escapeHTML(u.username)}</td>
        <td class="desc">${escapeHTML(u.tarjeta)}</td>
        <td>${u.es_admin ? '<span class="tag">admin</span>' : ''}</td>
        <td style="white-space:nowrap;">
          <button type="button" class="del" onclick="resetearPasswordUsuario(${jsAttr(u.username)})" title="Resetear contraseña" aria-label="Resetear contraseña">🔑</button>
          <button type="button" class="del" onclick="eliminarUsuarioAdmin(${jsAttr(u.username)})" title="Eliminar" aria-label="Eliminar">🗑</button>
        </td>
      </tr>`).join('');
  } catch(e){
    tbody.innerHTML = '<tr><td colspan="4" class="empty">Error cargando usuarios.</td></tr>';
  }
}

function formatearFechaHora(iso){
  return iso.replace('T', ' ').replace('Z', '');
}

async function cargarAuditoria(){
  const tbody = document.getElementById('tbodyAuditoria');
  try{
    const res = await api('/api/auditoria');
    const entradas = await res.json();
    if(entradas.length === 0){
      tbody.innerHTML = '<tr><td colspan="4" class="empty">Sin acciones registradas todavía.</td></tr>';
      return;
    }
    tbody.innerHTML = entradas.map(a => `
      <tr>
        <td class="desc">${escapeHTML(formatearFechaHora(a.ts))}</td>
        <td class="desc">${escapeHTML(a.actor)}</td>
        <td class="desc">${escapeHTML(a.accion)}</td>
        <td class="desc">${escapeHTML(a.detalle || '—')}</td>
      </tr>`).join('');
  } catch(e){
    tbody.innerHTML = '<tr><td colspan="4" class="empty">Error cargando auditoría.</td></tr>';
  }
}

async function crearUsuarioNuevo(){
  const username = document.getElementById('nuevoUsuarioNombre').value.trim();
  const tarjeta = document.getElementById('nuevoUsuarioTarjeta').value;
  const password = document.getElementById('nuevoUsuarioPassword').value;
  const esAdmin = document.getElementById('nuevoUsuarioAdmin').checked;
  const statusEl = document.getElementById('usuariosStatus');

  if(!username || !password){
    statusEl.className = 'status err';
    statusEl.textContent = 'Completa usuario y contraseña.';
    return;
  }

  statusEl.className = 'status';
  statusEl.textContent = 'Creando...';

  try{
    const res = await api('/api/usuarios', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({username, tarjeta, password, es_admin: esAdmin})
    });
    const data = await res.json();
    if(!res.ok || data.error){
      statusEl.className = 'status err';
      statusEl.textContent = data.error || 'No se pudo crear el usuario.';
      return;
    }
    statusEl.className = 'status ok';
    statusEl.textContent = 'Usuario creado.';
    document.getElementById('nuevoUsuarioNombre').value = '';
    document.getElementById('nuevoUsuarioPassword').value = '';
    document.getElementById('nuevoUsuarioAdmin').checked = false;
    cargarUsuarios();
    cargarAuditoria();
  } catch(e){
    statusEl.className = 'status err';
    statusEl.textContent = 'No se pudo conectar al backend: ' + e.message;
  }
}

async function resetearPasswordUsuario(username){
  const nueva = await pedirTexto('Nueva contraseña para "' + username + '" (mínimo 6 caracteres):', {
    titulo: 'Restablecer contraseña', tipo: 'password', textoAceptar: 'Restablecer'
  });
  if(!nueva) return;
  try{
    const res = await api('/api/usuarios/' + encodeURIComponent(username) + '/resetear-password', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({nueva})
    });
    const data = await res.json();
    if(!res.ok || data.error){
      await mostrarAlerta('Error: ' + (data.error || res.statusText));
      return;
    }
    await mostrarAlerta('Contraseña actualizada.');
    cargarAuditoria();
  } catch(e){
    await mostrarAlerta('No se pudo conectar al backend: ' + e.message);
  }
}

async function eliminarUsuarioAdmin(username){
  const ok = await confirmarAccion('¿Eliminar el usuario "' + username + '"? Esta acción no se puede deshacer.', 'Eliminar usuario', 'Eliminar');
  if(!ok) return;
  try{
    const res = await api('/api/usuarios/' + encodeURIComponent(username), { method: 'DELETE' });
    const data = await res.json();
    if(!res.ok || data.error){
      await mostrarAlerta('Error: ' + (data.error || res.statusText));
      return;
    }
    cargarUsuarios();
    cargarAuditoria();
  } catch(e){
    await mostrarAlerta('No se pudo conectar al backend: ' + e.message);
  }
}

async function cargarSubtareas(){
  const tarjeta = tarjetaActual();
  const selSub = document.getElementById('subtarea');
  selSub.disabled = true;
  selSub.innerHTML = '<option>Cargando...</option>';
  try{
    const res = await api('/api/subtareas?tarjeta=' + encodeURIComponent(tarjeta));
    const subtareas = await res.json();
    selSub.innerHTML = subtareas.map(s => `<option value="${escapeHTML(s.name)}">${escapeHTML(s.name)}</option>`).join('');
    selSub.disabled = false;
    const ultimaSubtarea = localStorage.getItem(ultimaSubtareaKey(tarjeta));
    if(ultimaSubtarea && subtareas.some(s => s.name === ultimaSubtarea)){
      selSub.value = ultimaSubtarea;
    }
    cargarHistorial();
    cargarFaltantesMes();
  } catch(e){
    selSub.innerHTML = '<option>Error cargando subtareas</option>';
  }
}

document.getElementById('tarjeta').addEventListener('change', () => {
  localStorage.setItem(ULTIMA_TARJETA_KEY, document.getElementById('tarjeta').value);
  cargarSubtareas();
});
document.getElementById('subtarea').addEventListener('change', () => {
  localStorage.setItem(ultimaSubtareaKey(tarjetaActual()), document.getElementById('subtarea').value);
  cargarHistorial();
});

function mostrarStatus(html, tipo){
  const el = document.getElementById('status');
  el.className = 'status' + (tipo ? ' ' + tipo : '');
  el.innerHTML = html;
}

let HISTORIAL_ACTUAL = [];

async function cargarHistorial(){
  const tarjeta = tarjetaActual();
  const subtarea = document.getElementById('subtarea').value;
  document.getElementById('subtareaActual').textContent = subtarea || '—';
  const tbody = document.getElementById('tbodyOdoo');
  const totalEl = document.getElementById('totalSubtarea');
  HISTORIAL_ACTUAL = [];
  if(!subtarea){ return; }

  tbody.innerHTML = '<tr><td colspan="3" class="empty">Cargando...</td></tr>';
  totalEl.textContent = '';
  try{
    const url = '/api/timesheet/recientes?tarjeta=' + encodeURIComponent(tarjeta) + '&subtarea=' + encodeURIComponent(subtarea);
    const res = await api(url);
    const data = await res.json();
    if(data.error){
      tbody.innerHTML = '<tr><td colspan="3" class="empty">' + escapeHTML(data.error) + '</td></tr>';
      return;
    }
    if(data.lineas.length === 0){
      tbody.innerHTML = '<tr><td colspan="3" class="empty">Sin registros todavía en esta subtarea.</td></tr>';
    } else {
      renderFilasHistorial(data.lineas);
    }
    totalEl.innerHTML = 'Total <b style="color:var(--accent)">' + data.total_horas.toFixed(1) + 'h</b>';
    renderChipsDescripcion(data.lineas);
    HISTORIAL_ACTUAL = data.lineas;
    document.getElementById('buscarHistorial').value = '';
  } catch(e){
    tbody.innerHTML = '<tr><td colspan="3" class="empty">Error cargando historial.</td></tr>';
  }
}

function formatearFecha(iso){
  const [y,m,d] = iso.split('-');
  return d + '/' + m + '/' + y;
}

function renderFilasHistorial(lineas){
  document.getElementById('tbodyOdoo').innerHTML = lineas.map(l => `
    <tr>
      <td class="desc">${formatearFecha(l.date)}</td>
      <td class="hrs">${l.unit_amount.toFixed(2)}h</td>
      <td class="desc">${escapeHTML(l.name || '—')}</td>
    </tr>`).join('');
}

function filtrarHistorial(){
  if(HISTORIAL_ACTUAL.length === 0) return;
  const q = document.getElementById('buscarHistorial').value.trim().toLowerCase();
  const filtradas = !q ? HISTORIAL_ACTUAL : HISTORIAL_ACTUAL.filter(l =>
    (l.name || '').toLowerCase().includes(q) || formatearFecha(l.date).includes(q)
  );
  if(filtradas.length === 0){
    document.getElementById('tbodyOdoo').innerHTML = '<tr><td colspan="3" class="empty">Sin coincidencias.</td></tr>';
  } else {
    renderFilasHistorial(filtradas);
  }
}

async function registrarEnOdoo(){
  if(document.getElementById('loteToggle').checked){
    return registrarEnLote();
  }

  const tarjeta = tarjetaActual();
  const subtarea = document.getElementById('subtarea').value;
  const fecha = document.getElementById('fecha').value;
  const horas = parseFloat(document.getElementById('horas').value);
  const detalle = document.getElementById('detalle').value.trim();
  const btn = document.getElementById('btnRegistrar');

  if(!fecha || !horas || horas <= 0){
    mostrarStatus('Completa fecha y horas (> 0) antes de registrar.', 'err');
    return;
  }
  if(horas > UMBRAL_HORAS_ALTAS){
    const seguro = await confirmarAccion('Vas a registrar ' + horas + ' horas el ' + formatearFecha(fecha) + '. ¿Es correcto? (parece un valor alto, revisá que no sea un error de tipeo)', 'Confirmar horas');
    if(!seguro) return;
  }

  btn.disabled = true;
  mostrarStatus('Enviando a Odoo...');

  try{
    const res = await api('/api/timesheet', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({tarjeta, subtarea, fecha, horas, detalle})
    });
    const data = await res.json();

    if(!res.ok || data.error){
      mostrarStatus('Error: ' + escapeHTML(data.error || res.statusText), 'err');
      btn.disabled = false;
      return;
    }

    mostrarStatus('Registrado en Odoo (id ' + data.id + '). <a href="#" onclick="deshacer(' + data.id + '); return false;" style="color:var(--accent)">Deshacer</a>', 'ok');
    document.getElementById('horas').value = '';
    document.getElementById('detalle').value = '';
    cargarHistorial();
    cargarResumen();
    cargarHeatmap();
    cargarFaltantesMes();
  } catch(e){
    mostrarStatus('No se pudo conectar al backend: ' + escapeHTML(e.message), 'err');
  } finally {
    btn.disabled = false;
  }
}

async function registrarEnLote(){
  const tarjeta = tarjetaActual();
  const subtarea = document.getElementById('subtarea').value;
  const desde = document.getElementById('fechaDesde').value;
  const hasta = document.getElementById('fechaHasta').value;
  const horas = parseFloat(document.getElementById('horasLoteInput').value);
  const detalle = document.getElementById('detalle').value.trim();
  const btn = document.getElementById('btnRegistrar');

  if(!desde || !hasta || hasta < desde){
    mostrarStatus('Completa un rango de fechas válido (Desde ≤ Hasta).', 'err');
    return;
  }
  const dias = diasHabilesEnRango(desde, hasta);
  if(dias.length === 0){
    mostrarStatus('No hay días hábiles (lun-vie) en ese rango.', 'err');
    return;
  }
  if(!horas || horas <= 0){
    mostrarStatus('Completa las horas por día (> 0) antes de registrar.', 'err');
    return;
  }
  if(horas > UMBRAL_HORAS_ALTAS){
    const seguro = await confirmarAccion('Vas a registrar ' + horas + ' horas en cada uno de los ' + dias.length + ' días del rango. ¿Es correcto? (parece un valor alto, revisá que no sea un error de tipeo)', 'Confirmar horas');
    if(!seguro) return;
  }

  btn.disabled = true;
  let creados = 0;

  for(const fecha of dias){
    mostrarStatus('Registrando ' + (creados + 1) + ' de ' + dias.length + ' (' + formatearFecha(fecha) + ')...');
    try{
      const res = await api('/api/timesheet', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({tarjeta, subtarea, fecha, horas, detalle})
      });
      const data = await res.json();
      if(!res.ok || data.error){
        mostrarStatus('Se registraron ' + creados + ' de ' + dias.length + ' días. Error en ' + formatearFecha(fecha) + ': ' + escapeHTML(data.error || res.statusText), 'err');
        btn.disabled = false;
        cargarHistorial();
        cargarResumen();
        cargarHeatmap();
        cargarFaltantesMes();
        return;
      }
      creados++;
    } catch(e){
      mostrarStatus('Se registraron ' + creados + ' de ' + dias.length + ' días. Se cortó la conexión: ' + escapeHTML(e.message), 'err');
      btn.disabled = false;
      cargarHistorial();
      cargarResumen();
      cargarHeatmap();
      cargarFaltantesMes();
      return;
    }
  }

  mostrarStatus('Registrados ' + creados + ' días en Odoo.', 'ok');
  document.getElementById('horasLoteInput').value = '';
  document.getElementById('detalle').value = '';
  btn.disabled = false;
  cargarHistorial();
  cargarResumen();
  cargarHeatmap();
  cargarFaltantesMes();
}

async function deshacer(id){
  mostrarStatus('Deshaciendo...');
  try{
    const res = await api('/api/timesheet/' + id + '?tarjeta=' + encodeURIComponent(tarjetaActual()), { method: 'DELETE' });
    const data = await res.json();
    if(data.ok){
      mostrarStatus('Entrada eliminada.', 'ok');
      cargarHistorial();
      cargarResumen();
      cargarHeatmap();
      cargarFaltantesMes();
    } else {
      mostrarStatus('No se pudo deshacer.', 'err');
    }
  } catch(e){
    mostrarStatus('Error al deshacer: ' + escapeHTML(e.message), 'err');
  }
}

function escaparCSV(valor){
  const str = String(valor);
  return /[",\r\n]/.test(str) ? '"' + str.replace(/"/g, '""') + '"' : str;
}

function exportarCSV(){
  if(HISTORIAL_ACTUAL.length === 0){
    mostrarStatus('No hay historial cargado para exportar todavía — elegí una subtarea con registros primero.', 'err');
    return;
  }

  const subtarea = document.getElementById('subtareaActual').textContent;
  const filas = [['Fecha', 'Horas', 'Descripción']];
  HISTORIAL_ACTUAL.forEach(l => filas.push([l.date, l.unit_amount, l.name || '']));
  const csv = '﻿' + filas.map(f => f.map(escaparCSV).join(',')).join('\r\n');

  const blob = new Blob([csv], {type: 'text/csv;charset=utf-8;'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'horas_' + subtarea.replace(/[^a-z0-9]+/gi, '_').toLowerCase() + '.csv';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);

  mostrarStatus('CSV descargado (' + HISTORIAL_ACTUAL.length + ' fila' + (HISTORIAL_ACTUAL.length === 1 ? '' : 's') + ' — el historial visible arriba, no el total completo).', 'ok');
}

function escaparXML(valor){
  return String(valor).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&apos;');
}

function exportarExcel(){
  if(HISTORIAL_ACTUAL.length === 0){
    mostrarStatus('No hay historial cargado para exportar todavía — elegí una subtarea con registros primero.', 'err');
    return;
  }

  const subtarea = document.getElementById('subtareaActual').textContent;
  const filas = HISTORIAL_ACTUAL.map(l => `
   <Row>
    <Cell><Data ss:Type="String">${escaparXML(formatearFecha(l.date))}</Data></Cell>
    <Cell><Data ss:Type="Number">${l.unit_amount}</Data></Cell>
    <Cell><Data ss:Type="String">${escaparXML(l.name || '')}</Data></Cell>
   </Row>`).join('');

  // Formato SpreadsheetML 2003: XML plano que Excel abre nativamente,
  // sin necesitar ninguna librería externa para armar un .xlsx real.
  const xml = '<?xml version="1.0"?>\n' +
    '<?mso-application progid="Excel.Sheet"?>\n' +
    '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">\n' +
    ' <Worksheet ss:Name="Horas">\n' +
    '  <Table>\n' +
    '   <Row>\n' +
    '    <Cell><Data ss:Type="String">Fecha</Data></Cell>\n' +
    '    <Cell><Data ss:Type="String">Horas</Data></Cell>\n' +
    '    <Cell><Data ss:Type="String">Descripción</Data></Cell>\n' +
    '   </Row>' + filas + '\n' +
    '  </Table>\n' +
    ' </Worksheet>\n' +
    '</Workbook>';

  const blob = new Blob([xml], {type: 'application/vnd.ms-excel;charset=utf-8;'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'horas_' + subtarea.replace(/[^a-z0-9]+/gi, '_').toLowerCase() + '.xls';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);

  mostrarStatus('Excel descargado (' + HISTORIAL_ACTUAL.length + ' fila' + (HISTORIAL_ACTUAL.length === 1 ? '' : 's') + '). Excel puede avisar que el formato no coincide con la extensión — es normal, dale "Sí, abrir de todas formas".', 'ok');
}

let LINEAS_DIA_ACTUAL = [];
let SUBTAREAS_CACHE = null;
let SUBTAREAS_CACHE_TARJETA = null;

async function subtareasParaEditor(){
  const tarjeta = tarjetaActual();
  // Cacheado junto con la tarjeta a la que corresponde: si un admin cambia
  // de tarjeta, no queremos mostrarle (ni dejarle guardar) subtareas de la
  // tarjeta anterior.
  if(SUBTAREAS_CACHE && SUBTAREAS_CACHE_TARJETA === tarjeta) return SUBTAREAS_CACHE;
  const res = await api('/api/subtareas?tarjeta=' + encodeURIComponent(tarjeta));
  SUBTAREAS_CACHE = await res.json();
  SUBTAREAS_CACHE_TARJETA = tarjeta;
  return SUBTAREAS_CACHE;
}

async function consultarDia(){
  const fecha = document.getElementById('fechaConsulta').value;
  const tbody = document.getElementById('tbodyDia');
  const totalEl = document.getElementById('totalDia');

  if(!fecha){
    totalEl.className = 'status err';
    totalEl.textContent = 'Elige una fecha primero.';
    return;
  }

  tbody.innerHTML = '<tr><td colspan="4" class="empty">Consultando...</td></tr>';
  totalEl.className = 'status';
  totalEl.textContent = '';

  try{
    const tarjeta = tarjetaActual();
    const url = '/api/timesheet/dia?fecha=' + encodeURIComponent(fecha) + '&tarjeta=' + encodeURIComponent(tarjeta);
    const res = await api(url);
    const data = await res.json();

    if(data.error){
      tbody.innerHTML = '<tr><td colspan="4" class="empty">' + escapeHTML(data.error) + '</td></tr>';
      return;
    }
    if(data.lineas.length === 0){
      tbody.innerHTML = '<tr><td colspan="4" class="empty">Sin horas registradas ese día.</td></tr>';
      totalEl.textContent = '';
      return;
    }

    LINEAS_DIA_ACTUAL = data.lineas;
    renderTablaDia();

    totalEl.className = 'status ok';
    totalEl.textContent = 'Total del día: ' + data.total_horas.toFixed(2) + 'h';
  } catch(e){
    tbody.innerHTML = '<tr><td colspan="4" class="empty">Error consultando el día.</td></tr>';
  }
}

function renderTablaDia(){
  const tbody = document.getElementById('tbodyDia');
  tbody.innerHTML = LINEAS_DIA_ACTUAL.map(l => `
    <tr id="fila-dia-${l.id}">
      <td><span class="tag">${escapeHTML(l.subtarea)}</span></td>
      <td class="hrs">${l.horas.toFixed(2)}h</td>
      <td class="desc">${escapeHTML(l.descripcion || '—')}</td>
      <td style="white-space:nowrap;">
        <button type="button" class="del" style="color:var(--text-dim);" onclick="activarEdicion(${l.id})" title="Editar" aria-label="Editar">✎</button>
        <button type="button" class="del" onclick="eliminarLineaDia(${l.id})" title="Eliminar" aria-label="Eliminar">🗑</button>
      </td>
    </tr>`).join('');
}

function mostrarStatusDia(html, tipo){
  const el = document.getElementById('statusDia');
  el.className = 'status' + (tipo ? ' ' + tipo : '');
  el.innerHTML = html;
}

// No se puede "des-borrar" del lado de Odoo: deshacer una eliminación
// significa volver a crear la línea con los mismos datos. Guardamos acá lo
// necesario para recrearla (incluida la tarjeta, para no terminar creándola
// en la tarjeta equivocada si el admin la cambió mientras tanto).
let ULTIMA_LINEA_ELIMINADA_DIA = null;

async function eliminarLineaDia(id){
  const linea = LINEAS_DIA_ACTUAL.find(l => l.id === id);
  const ok = await confirmarAccion('¿Eliminar esta entrada?', 'Eliminar entrada', 'Eliminar');
  if(!ok) return;
  try{
    const res = await api('/api/timesheet/' + id + '?tarjeta=' + encodeURIComponent(tarjetaActual()), { method: 'DELETE' });
    const data = await res.json();
    if(!res.ok || data.error){
      await mostrarAlerta('Error al eliminar: ' + (data.error || res.statusText));
      return;
    }
    ULTIMA_LINEA_ELIMINADA_DIA = linea ? {
      tarjeta: tarjetaActual(),
      subtarea: linea.subtarea,
      fecha: document.getElementById('fechaConsulta').value,
      horas: linea.horas,
      detalle: linea.descripcion,
    } : null;
    await consultarDia();
    cargarResumen();
    cargarHeatmap();
    cargarFaltantesMes();
    if(ULTIMA_LINEA_ELIMINADA_DIA){
      mostrarStatusDia('Entrada eliminada. <a href="#" onclick="deshacerEliminacionDia(); return false;" style="color:var(--accent)">Deshacer</a>', 'ok');
    }
  } catch(e){
    await mostrarAlerta('No se pudo conectar al backend: ' + e.message);
  }
}

async function deshacerEliminacionDia(){
  const datos = ULTIMA_LINEA_ELIMINADA_DIA;
  if(!datos) return;
  ULTIMA_LINEA_ELIMINADA_DIA = null;
  mostrarStatusDia('Restaurando...');
  try{
    const res = await api('/api/timesheet', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(datos)
    });
    const data = await res.json();
    if(!res.ok || data.error){
      mostrarStatusDia('No se pudo restaurar: ' + escapeHTML(data.error || res.statusText), 'err');
      return;
    }
    mostrarStatusDia('Entrada restaurada.', 'ok');
    consultarDia();
    cargarResumen();
    cargarHeatmap();
    cargarFaltantesMes();
  } catch(e){
    mostrarStatusDia('No se pudo conectar al backend: ' + escapeHTML(e.message), 'err');
  }
}

async function activarEdicion(id){
  const linea = LINEAS_DIA_ACTUAL.find(l => l.id === id);
  if(!linea) return;
  const subtareas = await subtareasParaEditor();
  const fila = document.getElementById('fila-dia-' + id);

  fila.innerHTML = `
    <td>
      <select id="edit-subtarea-${id}" style="font-size:12.5px; padding:5px 6px;">
        ${subtareas.map(s => `<option value="${escapeHTML(s.name)}" ${s.name === linea.subtarea ? 'selected' : ''}>${escapeHTML(s.name)}</option>`).join('')}
      </select>
    </td>
    <td><input type="number" id="edit-horas-${id}" value="${linea.horas}" step="0.25" min="0.25" style="font-size:12.5px; padding:5px 6px;"></td>
    <td><input type="text" id="edit-detalle-${id}" value="${escapeHTML(linea.descripcion || '')}" style="font-size:12.5px; padding:5px 6px;"></td>
    <td style="white-space:nowrap;">
      <button type="button" class="del" style="color:var(--ok);" onclick="guardarEdicion(${id})" title="Guardar" aria-label="Guardar">✓</button>
      <button type="button" class="del" onclick="renderTablaDia()" title="Cancelar" aria-label="Cancelar edición">✕</button>
    </td>`;
}

let ULTIMA_EDICION_DIA = null;

async function guardarEdicion(id){
  const linea = LINEAS_DIA_ACTUAL.find(l => l.id === id);
  const subtarea = document.getElementById('edit-subtarea-' + id).value;
  const horas = parseFloat(document.getElementById('edit-horas-' + id).value);
  const detalle = document.getElementById('edit-detalle-' + id).value.trim();

  if(!horas || horas <= 0){
    await mostrarAlerta('Las horas deben ser un número mayor a 0.');
    return;
  }
  if(horas > UMBRAL_HORAS_ALTAS){
    const seguro = await confirmarAccion('Vas a dejar esta entrada en ' + horas + ' horas. ¿Es correcto? (parece un valor alto, revisá que no sea un error de tipeo)', 'Confirmar horas');
    if(!seguro) return;
  }

  try{
    const res = await api('/api/timesheet/' + id, {
      method: 'PUT',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({tarjeta: tarjetaActual(), subtarea, horas, detalle})
    });
    const data = await res.json();
    if(!res.ok || data.error){
      await mostrarAlerta('Error al guardar: ' + (data.error || res.statusText));
      return;
    }
    ULTIMA_EDICION_DIA = linea ? {
      id, tarjeta: tarjetaActual(),
      subtarea: linea.subtarea, horas: linea.horas, detalle: linea.descripcion,
    } : null;
    await consultarDia();
    cargarResumen();
    cargarHeatmap();
    cargarFaltantesMes();
    if(ULTIMA_EDICION_DIA){
      mostrarStatusDia('Cambios guardados. <a href="#" onclick="deshacerEdicionDia(); return false;" style="color:var(--accent)">Deshacer</a>', 'ok');
    }
  } catch(e){
    await mostrarAlerta('No se pudo conectar al backend: ' + e.message);
  }
}

async function deshacerEdicionDia(){
  const datos = ULTIMA_EDICION_DIA;
  if(!datos) return;
  ULTIMA_EDICION_DIA = null;
  mostrarStatusDia('Deshaciendo...');
  try{
    const res = await api('/api/timesheet/' + datos.id, {
      method: 'PUT',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({tarjeta: datos.tarjeta, subtarea: datos.subtarea, horas: datos.horas, detalle: datos.detalle})
    });
    const data = await res.json();
    if(!res.ok || data.error){
      mostrarStatusDia('No se pudo deshacer: ' + escapeHTML(data.error || res.statusText), 'err');
      return;
    }
    mostrarStatusDia('Cambios revertidos.', 'ok');
    consultarDia();
    cargarResumen();
    cargarHeatmap();
    cargarFaltantesMes();
  } catch(e){
    mostrarStatusDia('No se pudo conectar al backend: ' + escapeHTML(e.message), 'err');
  }
}

async function revisarDiasFaltantes(){
  const n = parseInt(document.getElementById('cantidadDias').value) || 10;
  const cont = document.getElementById('listaFaltantes');
  cont.innerHTML = '<div class="status">Revisando...</div>';

  try{
    const tarjeta = tarjetaActual();
    const url = '/api/dias-faltantes?dias=' + n + '&tarjeta=' + encodeURIComponent(tarjeta);
    const res = await api(url);
    const data = await res.json();

    if(data.error){
      cont.innerHTML = '<div class="status err">' + escapeHTML(data.error) + '</div>';
      return;
    }
    if(data.faltantes.length === 0){
      cont.innerHTML = '<div class="status ok">Sin días pendientes en los últimos ' + n + ' días hábiles. Al día ✔</div>';
      return;
    }

    cont.innerHTML = '<div class="status err">' + data.faltantes.length + ' día(s) sin horas cargadas:</div><div class="chips" style="margin-top:8px;">' +
      data.faltantes.map(f => {
        const [y,m,d] = f.split('-');
        return `<button type="button" class="chip" onclick="irACargarFecha('${f}')">${d}/${m}/${y}</button>`;
      }).join('') + '</div>';
  } catch(e){
    cont.innerHTML = '<div class="status err">Error revisando días.</div>';
  }
}

function irACargarFecha(fechaIso){
  document.getElementById('fecha').value = fechaIso;
  document.getElementById('fecha').scrollIntoView({behavior:'smooth', block:'center'});
  document.getElementById('subtarea').focus();
}

document.getElementById('loginPassword').addEventListener('keydown', (e) => {
  if(e.key === 'Enter') iniciarSesion();
});

inicializar();
