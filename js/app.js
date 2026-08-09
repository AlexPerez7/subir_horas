const API_BASE = 'https://subir-horas.onrender.com';
const TOKEN_KEY = 'registro_horas_token';
let ES_ADMIN = false;
let MI_TARJETA = '';

document.getElementById('fecha').valueAsDate = new Date();
document.getElementById('fechaConsulta').valueAsDate = new Date();

// Autenticación por token (no por cookie): frontend y backend viven en
// dominios distintos, y varios navegadores (Safari, Brave, Samsung
// Internet...) bloquean por defecto las cookies cross-site aunque
// tengan SameSite=None; Secure. Guardamos el token en localStorage y
// lo mandamos como header en cada pedido en vez de depender de cookies.
function getToken(){ return localStorage.getItem(TOKEN_KEY); }
function setToken(token){ localStorage.setItem(TOKEN_KEY, token); }
function clearToken(){ localStorage.removeItem(TOKEN_KEY); }

function api(path, options){
  options = options || {};
  const headers = Object.assign({}, options.headers);
  const token = getToken();
  if(token) headers['Authorization'] = 'Bearer ' + token;
  return fetch(API_BASE + path, Object.assign({}, options, { headers }));
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
  document.getElementById('appRoot').style.display = 'block';

  document.getElementById('userbox').innerHTML =
    '<b>' + yo.username + '</b> · ' + yo.tarjeta +
    '<a onclick="toggleCambiarPassword(true)">Cambiar contraseña</a>' +
    '<a onclick="cerrarSesion()">Cerrar sesión</a>';

  if(ES_ADMIN){
    document.getElementById('labelTarjeta').style.display = '';
    document.getElementById('tarjeta').style.display = '';
    document.getElementById('tarjetaFija').style.display = 'none';
    document.getElementById('panelUsuarios').style.display = 'block';
    cargarTarjetas();
    cargarUsuarios();
  } else {
    document.getElementById('labelTarjeta').style.display = 'none';
    document.getElementById('tarjeta').style.display = 'none';
    const fija = document.getElementById('tarjetaFija');
    fija.style.display = 'block';
    fija.textContent = yo.tarjeta;
    cargarSubtareas();
  }

  verificarRecordatorio();
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

function toggleCambiarPassword(mostrar){
  document.getElementById('cambiarPasswordBackdrop').style.display = mostrar ? 'flex' : 'none';
  if(mostrar){
    document.getElementById('cpActual').value = '';
    document.getElementById('cpNueva').value = '';
    document.getElementById('cpConfirmar').value = '';
    document.getElementById('cpStatus').textContent = '';
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

function renderChipsDescripcion(lineas){
  const cont = document.getElementById('chipsDescripcion');
  const unicas = [...new Set(lineas.map(l => l.name).filter(Boolean))].slice(0, 5);
  if(unicas.length === 0){ cont.innerHTML = ''; return; }
  cont.innerHTML = unicas.map(desc =>
    `<button type="button" class="chip" title="${desc.replace(/"/g,'&quot;')}" onclick="usarDescripcion(this)">${desc}</button>`
  ).join('');
}

function usarDescripcion(btn){
  document.getElementById('detalle').value = btn.title;
}

async function cargarTarjetas(){
  const sel = document.getElementById('tarjeta');
  try{
    const res = await api('/api/tarjetas');
    const tarjetas = await res.json();
    const opciones = tarjetas.map(t => `<option value="${t.name}">${t.name}</option>`).join('');
    sel.innerHTML = opciones;
    // Como admin, el desplegable trae todas las tarjetas del proyecto:
    // preseleccionamos la propia en vez de dejar que quede la primera
    // de la lista (que es de quien sea que Odoo devuelva primero).
    if(MI_TARJETA && tarjetas.some(t => t.name === MI_TARJETA)){
      sel.value = MI_TARJETA;
    }
    const selNuevoUsuario = document.getElementById('nuevoUsuarioTarjeta');
    if(selNuevoUsuario) selNuevoUsuario.innerHTML = opciones;
    cargarSubtareas();
  } catch(e){
    sel.innerHTML = '<option>Error cargando tarjetas</option>';
    mostrarStatus('No se pudo conectar al backend (' + e.message + '). ¿Está corriendo backend_odoo.py?', 'err');
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
        <td class="desc">${u.username}</td>
        <td class="desc">${u.tarjeta}</td>
        <td>${u.es_admin ? '<span class="tag">admin</span>' : ''}</td>
        <td style="white-space:nowrap;">
          <button type="button" class="del" onclick="resetearPasswordUsuario('${u.username}')" title="Resetear contraseña">🔑</button>
          <button type="button" class="del" onclick="eliminarUsuarioAdmin('${u.username}')" title="Eliminar">🗑</button>
        </td>
      </tr>`).join('');
  } catch(e){
    tbody.innerHTML = '<tr><td colspan="4" class="empty">Error cargando usuarios.</td></tr>';
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
  } catch(e){
    statusEl.className = 'status err';
    statusEl.textContent = 'No se pudo conectar al backend: ' + e.message;
  }
}

async function resetearPasswordUsuario(username){
  const nueva = prompt('Nueva contraseña para "' + username + '" (mínimo 6 caracteres):');
  if(!nueva) return;
  try{
    const res = await api('/api/usuarios/' + encodeURIComponent(username) + '/resetear-password', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({nueva})
    });
    const data = await res.json();
    if(!res.ok || data.error){
      alert('Error: ' + (data.error || res.statusText));
      return;
    }
    alert('Contraseña actualizada.');
  } catch(e){
    alert('No se pudo conectar al backend: ' + e.message);
  }
}

async function eliminarUsuarioAdmin(username){
  if(!confirm('¿Eliminar el usuario "' + username + '"? Esta acción no se puede deshacer.')) return;
  try{
    const res = await api('/api/usuarios/' + encodeURIComponent(username), { method: 'DELETE' });
    const data = await res.json();
    if(!res.ok || data.error){
      alert('Error: ' + (data.error || res.statusText));
      return;
    }
    cargarUsuarios();
  } catch(e){
    alert('No se pudo conectar al backend: ' + e.message);
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
    selSub.innerHTML = subtareas.map(s => `<option value="${s.name}">${s.name}</option>`).join('');
    selSub.disabled = false;
    cargarHistorial();
  } catch(e){
    selSub.innerHTML = '<option>Error cargando subtareas</option>';
  }
}

document.getElementById('tarjeta').addEventListener('change', cargarSubtareas);
document.getElementById('subtarea').addEventListener('change', cargarHistorial);

function mostrarStatus(html, tipo){
  const el = document.getElementById('status');
  el.className = 'status' + (tipo ? ' ' + tipo : '');
  el.innerHTML = html;
}

async function cargarHistorial(){
  const tarjeta = tarjetaActual();
  const subtarea = document.getElementById('subtarea').value;
  document.getElementById('subtareaActual').textContent = subtarea || '—';
  const tbody = document.getElementById('tbodyOdoo');
  const totalEl = document.getElementById('totalSubtarea');
  if(!subtarea){ return; }

  tbody.innerHTML = '<tr><td colspan="3" class="empty">Cargando...</td></tr>';
  totalEl.textContent = '';
  try{
    const url = '/api/timesheet/recientes?tarjeta=' + encodeURIComponent(tarjeta) + '&subtarea=' + encodeURIComponent(subtarea);
    const res = await api(url);
    const data = await res.json();
    if(data.error){
      tbody.innerHTML = '<tr><td colspan="3" class="empty">' + data.error + '</td></tr>';
      return;
    }
    if(data.lineas.length === 0){
      tbody.innerHTML = '<tr><td colspan="3" class="empty">Sin registros todavía en esta subtarea.</td></tr>';
    } else {
      tbody.innerHTML = data.lineas.map(l => `
        <tr>
          <td class="desc">${formatearFecha(l.date)}</td>
          <td class="hrs">${l.unit_amount.toFixed(2)}h</td>
          <td class="desc">${l.name || '—'}</td>
        </tr>`).join('');
    }
    totalEl.innerHTML = 'Total <b style="color:var(--accent)">' + data.total_horas.toFixed(1) + 'h</b>';
    renderChipsDescripcion(data.lineas);
  } catch(e){
    tbody.innerHTML = '<tr><td colspan="3" class="empty">Error cargando historial.</td></tr>';
  }
}

function formatearFecha(iso){
  const [y,m,d] = iso.split('-');
  return d + '/' + m + '/' + y;
}

async function registrarEnOdoo(){
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
      mostrarStatus('Error: ' + (data.error || res.statusText), 'err');
      btn.disabled = false;
      return;
    }

    mostrarStatus('Registrado en Odoo (id ' + data.id + '). <a href="#" onclick="deshacer(' + data.id + '); return false;" style="color:var(--accent)">Deshacer</a>', 'ok');
    document.getElementById('horas').value = '';
    document.getElementById('detalle').value = '';
    cargarHistorial();
  } catch(e){
    mostrarStatus('No se pudo conectar al backend: ' + e.message, 'err');
  } finally {
    btn.disabled = false;
  }
}

async function deshacer(id){
  mostrarStatus('Deshaciendo...');
  try{
    const res = await api('/api/timesheet/' + id, { method: 'DELETE' });
    const data = await res.json();
    if(data.ok){
      mostrarStatus('Entrada eliminada.', 'ok');
      cargarHistorial();
    } else {
      mostrarStatus('No se pudo deshacer.', 'err');
    }
  } catch(e){
    mostrarStatus('Error al deshacer: ' + e.message, 'err');
  }
}

function exportarCSV(){
  mostrarStatus('El respaldo CSV ahora se basa en el historial visible arriba; usa Odoo directamente para exportes completos.', '');
}

let LINEAS_DIA_ACTUAL = [];
let SUBTAREAS_CACHE = null;

async function subtareasParaEditor(){
  if(SUBTAREAS_CACHE) return SUBTAREAS_CACHE;
  const res = await api('/api/subtareas?tarjeta=' + encodeURIComponent(tarjetaActual()));
  SUBTAREAS_CACHE = await res.json();
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
      tbody.innerHTML = '<tr><td colspan="4" class="empty">' + data.error + '</td></tr>';
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
      <td><span class="tag">${l.subtarea}</span></td>
      <td class="hrs">${l.horas.toFixed(2)}h</td>
      <td class="desc">${l.descripcion || '—'}</td>
      <td><button type="button" class="del" style="color:var(--text-dim);" onclick="activarEdicion(${l.id})" title="Editar">✎</button></td>
    </tr>`).join('');
}

async function activarEdicion(id){
  const linea = LINEAS_DIA_ACTUAL.find(l => l.id === id);
  if(!linea) return;
  const subtareas = await subtareasParaEditor();
  const fila = document.getElementById('fila-dia-' + id);

  fila.innerHTML = `
    <td>
      <select id="edit-subtarea-${id}" style="font-size:12.5px; padding:5px 6px;">
        ${subtareas.map(s => `<option value="${s.name}" ${s.name === linea.subtarea ? 'selected' : ''}>${s.name}</option>`).join('')}
      </select>
    </td>
    <td><input type="number" id="edit-horas-${id}" value="${linea.horas}" step="0.25" min="0.25" style="font-size:12.5px; padding:5px 6px;"></td>
    <td><input type="text" id="edit-detalle-${id}" value="${(linea.descripcion || '').replace(/"/g,'&quot;')}" style="font-size:12.5px; padding:5px 6px;"></td>
    <td style="white-space:nowrap;">
      <button type="button" class="del" style="color:var(--ok);" onclick="guardarEdicion(${id})" title="Guardar">✓</button>
      <button type="button" class="del" onclick="renderTablaDia()" title="Cancelar">✕</button>
    </td>`;
}

async function guardarEdicion(id){
  const subtarea = document.getElementById('edit-subtarea-' + id).value;
  const horas = parseFloat(document.getElementById('edit-horas-' + id).value);
  const detalle = document.getElementById('edit-detalle-' + id).value.trim();

  try{
    const res = await api('/api/timesheet/' + id, {
      method: 'PUT',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({tarjeta: tarjetaActual(), subtarea, horas, detalle})
    });
    const data = await res.json();
    if(!res.ok || data.error){
      alert('Error al guardar: ' + (data.error || res.statusText));
      return;
    }
    consultarDia();
  } catch(e){
    alert('No se pudo conectar al backend: ' + e.message);
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
      cont.innerHTML = '<div class="status err">' + data.error + '</div>';
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
