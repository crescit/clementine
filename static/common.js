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
  body,
  timeoutMs = 15000
} = {}) {
  let response;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    response = await fetch(path, {
      method,
      signal: controller.signal,
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
  } catch (err) {
    if (err && err.name === 'AbortError') {
      throw new Error('The server took too long to respond. Please try again.');
    }
    throw new Error('Unable to connect. Check your connection and try again. Your entered text is still here.');
  } finally {
    clearTimeout(timer);
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
let noticeTimer;
export function clearNotice() {
  clearTimeout(noticeTimer);
  noticeTimer = undefined;
  const box = $('notice');
  if (!box) return;
  box.hidden = true;
  box.className = 'notice';
  box.replaceChildren();
  box.removeAttribute('role');
}
export function notice(message, error = false, {
  sticky = error,
  duration = error ? 0 : 4500
} = {}) {
  const box = $('notice');
  if (!box) return;
  clearTimeout(noticeTimer);
  box.hidden = false;
  box.className = `notice toast ${error ? 'error' : 'success'}`;
  box.setAttribute('role', error ? 'alert' : 'status');
  const copy = el('span', message);
  const dismiss = el('button', 'Dismiss', 'notice-dismiss');
  dismiss.type = 'button';
  dismiss.setAttribute('aria-label', 'Dismiss notification');
  dismiss.onclick = () => clearNotice();
  box.replaceChildren(copy, dismiss);
  if (!sticky && duration > 0) {
    noticeTimer = setTimeout(() => clearNotice(), duration);
  }
}
export function showError(error) {
  notice(error.message + (error.fields ? ' ' + error.fields.map(f => `${label(f.field)}: ${f.message}`).join(' · ') : ''), true, {
    sticky: true
  });
  if (['VERSION_CONFLICT', 'UNAUTHENTICATED', 'NOT_FOUND'].includes(error.code)) {
    const b = el('button', 'Reload latest');
    b.onclick = () => location.reload();
    $('notice').append(b);
  }
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
  select.setAttribute('aria-label', 'Switch demo user');
  users.forEach(u => {
    const title = u.display_title || label(u.role);
    const adminNote = u.can_manage_policies ? ' · Policy admin' : '';
    // Closed control stays short (name only); title is available on hover.
    const opt = option(u.id, u.name);
    opt.title = `${u.name} · ${title}${adminNote}`;
    select.append(opt);
  });
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
  const initials = actor.name.split(/\s+/).filter(Boolean).map(n => n[0]).join('').slice(0, 2);
  const roleKey = String(actor.role || '').toLowerCase();
  const roleLabel = actor.can_manage_policies ? 'Policy admin' : label(actor.role);
  const title = actor.display_title || roleLabel;
  const avatar = el('span', initials, `avatar role-${roleKey}`);
  avatar.setAttribute('aria-hidden', 'true');
  const roleChip = el('span', roleLabel, `role-chip role-${roleKey}`);
  roleChip.title = title;
  const meta = el('span', null, 'persona-meta');
  meta.append(select, roleChip);
  const switcher = el('label', null, 'persona-switcher');
  switcher.title = `${actor.name} · ${title}`;
  switcher.append(avatar, meta);
  const inbox = buildInbox(users);
  const tools = el('div', null, 'header-tools');
  tools.append(inbox.root, switcher);
  $('header-actions').replaceChildren(tools);
  // Never block queue/detail rendering on the inbox fetch — a slow or failing
  // notifications request left campaign pages stuck on "Loading…".
  inbox.refresh();
  return {
    users,
    actor
  };
}
export const person = (users, id) => users.find(u => u.id === id)?.name || 'Unassigned';

function buildInbox(users) {
  const root = el('div', null, 'notif-root');
  const toggle = el('button', null, 'notif-toggle');
  toggle.type = 'button';
  toggle.setAttribute('aria-haspopup', 'true');
  toggle.setAttribute('aria-expanded', 'false');
  toggle.setAttribute('aria-label', 'Notifications');
  const bell = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  bell.setAttribute('class', 'notif-bell');
  bell.setAttribute('viewBox', '0 0 24 24');
  bell.setAttribute('aria-hidden', 'true');
  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  path.setAttribute('fill', 'currentColor');
  path.setAttribute('d', 'M12 22a2.2 2.2 0 0 0 2.2-2.2h-4.4A2.2 2.2 0 0 0 12 22Zm7-6.2V11a7 7 0 1 0-14 0v4.8L3 18v1h18v-1l-2-2.2Z');
  bell.append(path);
  const badge = el('span', '0', 'notif-badge');
  badge.hidden = true;
  toggle.append(bell, badge);
  const panel = el('div', null, 'notif-panel');
  panel.hidden = true;
  panel.setAttribute('role', 'region');
  panel.setAttribute('aria-label', 'Notification center');
  const heading = el('div', null, 'notif-heading');
  heading.append(el('strong', 'Notifications'));
  const markAll = el('button', 'Mark all read', 'notif-mark-all');
  markAll.type = 'button';
  heading.append(markAll);
  const list = el('div', null, 'notif-list');
  const empty = el('p', 'No notifications yet.', 'notif-empty muted');
  panel.append(heading, list, empty);
  root.append(toggle, panel);

  const setOpen = (open) => {
    panel.hidden = !open;
    toggle.setAttribute('aria-expanded', String(open));
    root.classList.toggle('open', open);
  };

  const paint = (data) => {
    const items = data.notifications || [];
    const unread = data.unread_count || 0;
    badge.hidden = unread === 0;
    badge.textContent = unread > 9 ? '9+' : String(unread);
    toggle.setAttribute('aria-label', unread ? `Notifications, ${unread} unread` : 'Notifications');
    markAll.hidden = unread === 0;
    list.replaceChildren();
    empty.hidden = items.length > 0;
    for (const item of items) {
      const link = el('a', null, `notif-item${item.unread ? ' unread' : ''}`);
      link.href = `/static/submission.html?id=${encodeURIComponent(item.submission_id)}`;
      link.append(
        el('strong', item.headline),
        el('span', `${item.external_id} · ${item.title}`, 'small'),
        el('span', `${person(users, item.actor_id)} · ${date(item.created_at, true)}`, 'small')
      );
      if (item.comment) link.append(el('span', item.comment, 'notif-comment'));
      link.addEventListener('click', async (event) => {
        if (!item.unread) return;
        event.preventDefault();
        try {
          await api(`/api/notifications/${encodeURIComponent(item.id)}/read`, {
            method: 'POST'
          });
        } catch {
          // Still navigate — durable mark-read can retry next open.
        }
        location.href = link.href;
      });
      list.append(link);
    }
  };

  const refresh = async () => {
    try {
      paint(await api('/api/notifications?limit=20'));
    } catch {
      badge.hidden = true;
      empty.hidden = false;
      empty.textContent = 'Notifications unavailable.';
      list.replaceChildren();
    }
  };

  toggle.addEventListener('click', (event) => {
    event.stopPropagation();
    setOpen(panel.hidden);
  });
  markAll.addEventListener('click', async (event) => {
    event.stopPropagation();
    try {
      await api('/api/notifications/read-all', {
        method: 'POST'
      });
      await refresh();
    } catch (error) {
      showError(error);
    }
  });
  document.addEventListener('click', (event) => {
    if (!root.contains(event.target)) setOpen(false);
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') setOpen(false);
  });

  return {
    root,
    refresh
  };
}
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
