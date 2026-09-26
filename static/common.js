export const $ = (id) => document.getElementById(id);
export const label = (value) => ({
  PENDING_ASSIGNMENT: 'Pending assignment',
  UNDER_REVIEW: 'Under review',
  CHANGES_REQUESTED: 'Changes requested',
  MORTGAGE_PREQUALIFICATION: 'Mortgage prequalification'
} [value] || String(value || '').toLowerCase().replaceAll('_', ' ').replace(/^./, c => c.toUpperCase()));
export function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}
export function option(value, text) {
  const n = el('option', text);
  n.value = value;
  return n;
}
export function badge(status) {
  return el('span', label(status), `badge ${status.toLowerCase()}`);
}
export const date = (value, time = false) => value ? new Intl.DateTimeFormat('en-US', {
  month: 'short',
  day: 'numeric',
  year: 'numeric',
  timeZone: 'UTC',
  ...(time ? {
    hour: 'numeric',
    minute: '2-digit'
  } : {})
}).format(new Date(value)) + (time ? ' UTC' : '') : '—';
export async function api(path, {
  method = 'GET',
  body
} = {}) {
  let response;
  try {
    response = await fetch(path, {
      method,
      headers: {
        'X-Demo-User-Id': localStorage.getItem('clearpath_user_id') || '',
        ...(body ? {
          'Content-Type': 'application/json'
        } : {})
      },
      ...(body ? {
        body: JSON.stringify(body)
      } : {})
    });
  } catch {
    throw new Error('Unable to connect. Check your connection and try again. Your entered text is still here.');
  }
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error('The server could not complete this request. Please try again.');
  }
  if (!response.ok) {
    const error = new Error(data.message || 'Unable to complete this request.');
    error.code = data.code;
    error.fields = data.details?.fields;
    throw error;
  }
  return data;
}
export function notice(message, error = false) {
  const box = $('notice');
  box.hidden = false;
  box.className = `notice ${error?'error':'success'}`;
  box.replaceChildren(el('span', message));
  box.setAttribute('role', error ? 'alert' : 'status');
}
export function showError(error) {
  notice(error.message + (error.fields ? ' ' + error.fields.map(f => `${label(f.field)}: ${f.message}`).join(' · ') : ''), true);
  if (['VERSION_CONFLICT', 'UNAUTHENTICATED', 'NOT_FOUND'].includes(error.code)) {
    const b = el('button', 'Reload latest');
    b.onclick = () => location.reload();
    $('notice').append(b);
  }
  $('notice').scrollIntoView({
    block: 'nearest',
    behavior: 'smooth'
  });
}
export async function init() {
  const {
    users
  } = await api('/api/users');
  const actor = users.find(u => u.id === localStorage.getItem('clearpath_user_id')) || users.find(u => u.name.startsWith('Sarah')) || users[0];
  if (!actor) throw new Error('No workspace users are configured. Enable demo seeding on a fresh database to use this demo.');
  localStorage.setItem('clearpath_user_id', actor.id);
  const select = el('select');
  select.id = 'persona';
  select.setAttribute('aria-label', 'Demo persona');
  users.forEach(u => select.append(option(u.id, `${u.name} · ${u.display_title || label(u.role)}`)));
  select.value = actor.id;
  select.onchange = () => {
    localStorage.setItem('clearpath_user_id', select.value);
    // Persona changes reset pagination so an out-of-range page is not sticky.
    const url = new URL(location.href);
    if (url.searchParams.has('offset')) {
      url.searchParams.delete('offset');
      location.assign(url.pathname + url.search + url.hash);
    } else {
      location.reload();
    }
  };
  const avatar = el('span', actor.name.split(' ').map(n => n[0]).join(''), 'avatar');
  $('header-actions').replaceChildren(el('span', 'Viewing as', 'small'), avatar, select);
  return {
    users,
    actor
  };
}
export const person = (users, id) => users.find(u => u.id === id)?.name || 'Unassigned';
export async function busy(form, task) {
  const controls = [...form.querySelectorAll('button')];
  const states = controls.map(b => b.disabled);
  controls.forEach(b => b.disabled = true);
  form.setAttribute('aria-busy', 'true');
  try {
    await task();
  } catch (e) {
    showError(e);
  } finally {
    controls.forEach((b, i) => b.disabled = states[i]);
    form.removeAttribute('aria-busy');
  }
}
