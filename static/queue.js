import {
  $,
  api,
  init,
  el,
  option,
  badge,
  date,
  label,
  person,
  showError
} from './common.js';

const FILTER_KEYS = ['completed', 'status', 'reviewer', 'search', 'risk', 'limit', 'offset'];

function queueHref(params, overrides = {}) {
  const next = new URLSearchParams();
  for (const key of FILTER_KEYS) {
    const value = key in overrides ? overrides[key] : params.get(key);
    if (value != null && value !== '') next.set(key, String(value));
  }
  const query = next.toString();
  return query ? `/static/index.html?${query}` : '/static/index.html';
}

function parseNonNegInt(value, fallback) {
  if (value == null || value === '') return fallback;
  const n = Number.parseInt(value, 10);
  return Number.isFinite(n) && n >= 0 ? n : fallback;
}

async function load() {
  const {
    users,
    actor
  } = await init();
  const params = new URLSearchParams(location.search);
  const completed = params.get('completed') === 'true';
  sessionStorage.setItem('clearpath_queue_query', location.search);
  $('completed').value = String(completed);
  $(completed ? 'completed-tab' : 'active-tab').classList.add('active');
  $(completed ? 'completed-tab' : 'active-tab').setAttribute('aria-current', 'page');
  $('clear-filters').href = completed ? '/static/index.html?completed=true' : '/static/index.html';
  $('search').value = params.get('search') || '';
  $('risk').value = params.get('risk') || '';
  $('risk-label').hidden = completed;
  $('risk').disabled = completed;
  if (completed) params.delete('risk');
  $('status').append(option('', 'All statuses'), ...(completed ? ['APPROVED', 'REJECTED'] : ['PENDING_ASSIGNMENT', 'UNDER_REVIEW', 'CHANGES_REQUESTED']).map(s => option(s, label(s))));
  $('status').value = params.get('status') || '';
  $('reviewer').append(option('', 'All reviewers'), option('MINE', 'Assigned to me'), option('UNASSIGNED', 'Unassigned'), ...users.filter(u => u.role === 'REVIEWER').map(u => option(u.id, u.name)));
  $('reviewer').value = params.get('reviewer') || '';
  if (actor.role === 'SUBMITTER') {
    $('reviewer-label').hidden = true;
    $('reviewer').disabled = true;
    params.delete('reviewer');
  }
  // Filter / tab / clear links omit offset; applying filters via the form also omits it.
  const limit = Math.min(100, Math.max(1, parseNonNegInt(params.get('limit'), 50) || 50));
  const offset = parseNonNegInt(params.get('offset'), 0);
  const query = new URLSearchParams();
  for (const key of ['completed', 'status', 'reviewer', 'search', 'risk'])
    if (params.get(key)) query.set(key, params.get(key));
  query.set('limit', String(limit));
  query.set('offset', String(offset));
  const [{
    submissions,
    total,
    limit: pageLimit,
    offset: pageOffset
  }, metrics, config] = await Promise.all([api(`/api/submissions?${query}`), api('/api/metrics'), api('/api/config')]);
  if (total > 0 && pageOffset >= total) {
    location.replace(queueHref(params, { offset: 0, limit: pageLimit }));
    return;
  }
  $('scope').textContent = `${actor.role === 'SUBMITTER' ? 'Your submissions' : 'All team submissions'} · ${completed ? 'most recent decisions first.' : 'sorted by review deadline.'}`;
  const cards = [
    ['Open reviews', metrics.open_count, 'Across active submissions', ''],
    ['Past review target', metrics.sla_breached_count, '72-hour elapsed-time target', 'warn'],
    ['Completed this week', metrics.completed_last_7_days, 'Decisions in the last 7 days', ''],
    ['Avg. turnaround', metrics.avg_turnaround_days === null ? '—' : `${metrics.avg_turnaround_days}d`, `${metrics.turnaround_sample_size} decision${metrics.turnaround_sample_size===1?'':'s'} · last 30 days`, '']
  ];
  $('metrics').replaceChildren(...cards.map(([name, value, note, cls]) => {
    const card = el('article', null, `metric ${cls}`);
    card.append(el('p', name, 'metric-label'), el('strong', value, 'metric-value'), el('p', note, 'small'));
    return card;
  }));
  $('metrics').setAttribute('aria-busy', 'false');
  const start = total === 0 ? 0 : pageOffset + 1;
  const end = pageOffset + submissions.length;
  const rangeLabel = total === 0
    ? `0 campaigns · ${metrics.unassigned_count} unassigned overall`
    : `${start}–${end} of ${total} campaign${total===1?'':'s'} · ${metrics.unassigned_count} unassigned overall`;
  $('count').textContent = rangeLabel;
  $('as-of').textContent = `Updated ${date(metrics.as_of,true)}`;
  $('time-heading').textContent = completed ? 'Decision date' : 'Review target';
  $('queue-body').replaceChildren();
  for (const s of submissions) {
    const row = el('tr');
    const campaign = el('td');
    const link = el('a', s.title, 'campaign-title');
    link.href = `/static/submission.html?id=${encodeURIComponent(s.id)}`;
    campaign.append(link, el('span', `${s.external_id} · ${label(s.product)} · ${s.partner || label(s.channel)}`, 'campaign-meta'));
    const status = el('td');
    status.append(badge(s.status));
    if (!completed) status.append(el('span', s.status === 'CHANGES_REQUESTED' ? 'Waiting on marketer' : 'Waiting on compliance', 'campaign-meta'));
    const reviewer = el('td', person(users, s.assigned_reviewer_id));
    const target = el('td');
    if (completed) target.append(el('span', date(s.decided_at)));
    else {
      const remaining = (new Date(s.sla_breach_at) - new Date(metrics.as_of)) / 3600000;
      target.append(el('span', remaining <= 0 ? (-remaining < 48 ? `${Math.max(1,Math.round(-remaining))}h overdue` : `${Math.floor(-remaining/24)}d overdue`) : `${Math.ceil(remaining)}h remaining`, remaining <= 0 ? 'overdue' : 'due'), el('span', date(s.sla_breach_at), 'campaign-meta'));
    }
    const open = el('td');
    const arrow = el('a', '↗', 'row-arrow');
    arrow.href = link.href;
    arrow.setAttribute('aria-label', `Open ${s.title}`);
    open.append(arrow);
    row.append(campaign, status, reviewer, el('td', date(s.target_launch_date)), target, open);
    $('queue-body').append(row);
  }
  $('queue-state').hidden = !!submissions.length;
  $('queue-state').textContent = 'No campaigns match this view. Clear your filters or start a new submission.';

  const pager = $('pager');
  const prevOffset = Math.max(0, pageOffset - pageLimit);
  const nextOffset = pageOffset + pageLimit;
  const hasPrev = pageOffset > 0;
  const hasNext = nextOffset < total;
  const prev = el('a', 'Previous', hasPrev ? 'button quiet' : 'button quiet is-disabled');
  prev.href = hasPrev ? queueHref(params, { offset: prevOffset, limit: pageLimit }) : '#';
  prev.setAttribute('aria-disabled', hasPrev ? 'false' : 'true');
  if (!hasPrev) prev.tabIndex = -1;
  const next = el('a', 'Next', hasNext ? 'button quiet' : 'button quiet is-disabled');
  next.href = hasNext ? queueHref(params, { offset: nextOffset, limit: pageLimit }) : '#';
  next.setAttribute('aria-disabled', hasNext ? 'false' : 'true');
  if (!hasNext) next.tabIndex = -1;
  const summary = el('span', total === 0 ? 'No matching campaigns' : `Showing ${start}–${end} of ${total}`, 'pager-summary');
  pager.replaceChildren(prev, summary, next);
  pager.hidden = false;

  $('reset').hidden = !(config.demo_mode && actor.role === 'REVIEWER');
  $('reset').onclick = () => $('reset-dialog').showModal();
  $('reset-dialog').addEventListener('close', async () => {
    if ($('reset-dialog').returnValue !== 'reset') return;
    $('reset').disabled = true;
    try {
      await api('/api/demo/reset', {
        method: 'POST'
      });
      location.href = '/';
    } catch (e) {
      showError(e);
      $('reset').disabled = false;
    }
  });
}
load().catch(e => {
  $('queue-state').textContent = 'Unable to load the workspace. Reload to try again.';
  showError(e);
});
