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
  showError,
  notice,
  clearNotice,
  busy
} from './common.js';
const params = new URLSearchParams(location.search),
  id = params.get('id');
let context, record, history;
const path = `/api/submissions/${encodeURIComponent(id || '')}`;
const ACTION_SAVED = {
  approve: 'Approved. Status and activity trail are updated on this record.',
  'request-changes': 'Changes requested. The submitter can revise on this same record.',
  reject: 'Rejected. The decision is recorded in the activity trail.',
  assign: 'Assignment updated. The next owner is visible on this record.',
  resubmit: 'Revision submitted. A new version is ready for review.'
};

function safeLink(url) {
  try {
    const parsed = new URL(url);
    return ['https:', 'http:'].includes(parsed.protocol) && !parsed.username && !parsed.password;
  } catch {
    return false;
  }
}

function showVersion() {
  const v = history.versions.find(v => v.version_number === Number($('version').value));
  if (!v) {
    $('copy').replaceChildren(document.createTextNode('This version is no longer available. Pick another version or return to the queue.'));
    return;
  }
  const current = v.version_number === record.current_version;
  $('version-meta').textContent = `${current?'Current version':'Historical version · read only'} · Submitted by ${person(context.users,v.created_by)} · ${date(v.created_at,true)}`;
  const points = Array.from(v.copy_text);
  $('copy').replaceChildren();
  const scan = v.preflight;
  const spans = scan.findings.filter(f => f.start != null && f.end != null).sort((a, b) => a.start - b.start);
  let position = 0;
  for (const f of spans) {
    if (f.start < position) continue;
    $('copy').append(document.createTextNode(points.slice(position, f.start).join('')));
    const mark = el('mark', points.slice(f.start, f.end).join(''));
    mark.title = f.message;
    $('copy').append(mark);
    position = f.end;
  }
  $('copy').append(document.createTextNode(points.slice(position).join('')));
  $('asset').replaceChildren();
  if (v.asset_url && safeLink(v.asset_url)) {
    const a = el('a', 'Open supporting asset ↗');
    a.href = v.asset_url;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    $('asset').append(a);
  }
  $('preflight-title').textContent = scan.passed ? 'Checks passed' : `${scan.findings.length} blocking findings`;
  $('preflight-summary').className = scan.passed ? 'check-passed' : 'check-blocked';
  $('preflight-summary').textContent = scan.passed ? (current ? (['APPROVED','REJECTED'].includes(record.status) ? '✓ Policy checks passed for this version' : '✓ Ready for a human review') : '✓ Historical version · checks passed') : `! Changes needed in Version ${v.version_number}`;
  $('findings').replaceChildren(...scan.findings.map(f => {
    const item = el('div', null, 'finding');
    item.append(el('span', f.rule_id, 'small'), el('h3', f.title), el('p', f.message));
    if (f.matched_text) item.append(el('blockquote', f.matched_text));
    return item;
  }));
  const canDecide = record.allowed_actions.includes('APPROVE');
  $('decision-form').hidden = !canDecide;
  $('decision-form').querySelectorAll('button').forEach(b => b.disabled = !current);
  $('approve').disabled = !current || !record.preflight.passed;
  $('approve-hint').textContent = !current ? 'Select the current version to make a decision.' : record.preflight.passed ? 'Approves the exact current copy shown here.' : 'Approval is blocked until the findings above are resolved.';
}
async function render() {
  [record, history] = await Promise.all([api(path), api(`${path}/history`)]);
  $('loading').hidden = true;
  $('detail').hidden = false;
  $('record-id').textContent = `${record.external_id} / REVIEW WORKSPACE`;
  $('title').textContent = record.title;
  document.title = `${record.external_id} · ${record.title} · ClearPath`;
  $('subtitle').textContent = `${label(record.product)} · ${label(record.channel)}${record.partner?' · '+record.partner:''}`;
  $('record-status').replaceChildren(badge(record.status));
  const metadata = [
    ['Submitted by', person(context.users, record.submitter_id)],
    ['Assigned reviewer', person(context.users, record.assigned_reviewer_id)],
    ['Target launch', date(record.target_launch_date)],
    ['Review deadline', date(record.sla_breach_at, true)]
  ];
  $('record-meta').replaceChildren(...metadata.map(([name, value]) => {
    const item = el('div');
    item.append(el('span', name, 'small'), el('strong', value));
    return item;
  }));
  $('version').replaceChildren(...[...history.versions].reverse().map(v => option(v.version_number, `Version ${v.version_number}${v.version_number===record.current_version?' · Current':''}`)));
  $('version').value = record.current_version;
  $('version').onchange = showVersion;
  showVersion();
  $('timeline').replaceChildren(...[...history.events].reverse().map(event => {
    const item = el('li');
    item.append(el('strong', label(event.event_type)), el('span', `${person(context.users,event.actor_id)} · Version ${event.version_number || '—'} · ${date(event.created_at,true)}`, 'small'));
    if (event.comment) item.append(el('p', event.comment, 'event-comment'));
    if (event.metadata_json) {
      try {
        const meta = JSON.parse(event.metadata_json);
        if (meta.assignee_id) item.append(el('p', `Assigned to ${person(context.users,meta.assignee_id)}`, 'small'));
      } catch {}
    }
    return item;
  }));
  const terminal = ['APPROVED', 'REJECTED'].includes(record.status);
  const youReview = context.actor.id === record.assigned_reviewer_id;
  const youSubmit = context.actor.id === record.submitter_id;
  const waitingOnYou = !terminal && (
    (record.status === 'CHANGES_REQUESTED' && youSubmit) ||
    (record.status !== 'CHANGES_REQUESTED' && youReview && record.allowed_actions.includes('APPROVE')) ||
    (!record.assigned_reviewer_id && record.allowed_actions.includes('ASSIGN'))
  );
  let nextCopy;
  if (terminal) {
    nextCopy = `${label(record.status)} · Version ${record.current_version} · ${date(record.decided_at,true)}. The decision and its author are recorded in the activity trail.`;
  } else if (record.status === 'CHANGES_REQUESTED') {
    nextCopy = youSubmit
      ? `Edit the campaign copy on the left, then save to create version ${record.current_version + 1}.`
      : `Waiting for ${person(context.users,record.submitter_id)} to revise the copy and resubmit.`;
  } else if (record.assigned_reviewer_id) {
    nextCopy = youReview
      ? 'You own the next review decision.'
      : `${person(context.users,record.assigned_reviewer_id)} owns the next review decision.`;
  } else {
    nextCopy = waitingOnYou
      ? 'Assign a reviewer to start the review.'
      : 'Waiting for a reviewer assignment.';
  }
  $('next-action').textContent = nextCopy;
  $('next-action-panel').classList.toggle('your-turn', waitingOnYou);
  const canResubmit = record.allowed_actions.includes('RESUBMIT');
  $('copy-view').hidden = canResubmit;
  $('revision-form').hidden = !canResubmit;
  $('version-label').hidden = canResubmit;
  $('copy-panel').classList.toggle('editing', canResubmit);
  $('copy-heading').textContent = canResubmit ? 'Revise campaign copy' : 'Campaign copy';
  if (canResubmit) {
    $('version-meta').textContent = `Editing a new version from v${record.current_version} · address the feedback in the policy check, then save.`;
    $('revision-hint').textContent = `Creates version ${record.current_version + 1} · 1–20,000 characters`;
    $('revision-copy').value = record.copy_text;
    $('revision-url').value = record.asset_url || '';
  }
  $('assign-form').hidden = !record.allowed_actions.includes('ASSIGN');
  $('assign-reviewer').replaceChildren(...context.users.filter(u => u.role === 'REVIEWER').map(u => option(u.id, u.name)));
  $('assign-reviewer').value = record.assigned_reviewer_id || context.actor.id;
  $('assign-comment').required = !!record.assigned_reviewer_id;
  return {
    waitingOnYou,
    nextCopy
  };
}
async function mutate(action, body) {
  await api(`${path}/${action}`, {
    method: 'POST',
    body: {
      ...body,
      expected_record_version: record.record_version
    }
  });
  await render();
  $('decision-comment').value = '';
  $('assign-comment').value = '';
  notice(ACTION_SAVED[action] || 'Saved. The campaign and activity trail are up to date.');
}
async function load() {
  const query = sessionStorage.getItem('clearpath_queue_query') || '';
  $('back').href = '/static/index.html' + (query.startsWith('?') ? query : '');
  context = await init();
  if (!id) throw new Error('No campaign selected. Open a submission from the review queue.');
  const state = await render();
  if (params.get('created') === 'true') {
    notice('Submission received. Status, owner, and revision stay on this record — no email chase.');
    window.history.replaceState({}, '', `/static/submission.html?id=${encodeURIComponent(id)}`);
  } else if (state.waitingOnYou) {
    notice(state.nextCopy, false, {
      sticky: true
    });
    const box = $('notice');
    box.classList.add('attention');
    box.classList.remove('toast', 'success');
  } else {
    clearNotice();
  }
  $('decision-form').onsubmit = async (e) => {
    e.preventDefault();
    const action = e.submitter.value,
      comment = $('decision-comment').value.trim();
    if (action !== 'approve' && !comment) {
      $('decision-comment').setCustomValidity('Enter feedback before requesting changes or rejecting.');
      $('decision-comment').reportValidity();
      return;
    }
    await busy(e.currentTarget, () => mutate(action, {
      comment: comment || null
    }));
  };
  $('decision-comment').oninput = () => $('decision-comment').setCustomValidity('');
  $('assign-form').onsubmit = async (e) => {
    e.preventDefault();
    await busy(e.currentTarget, () => mutate('assign', {
      reviewer_id: $('assign-reviewer').value,
      comment: $('assign-comment').value.trim() || null
    }));
  };
  $('revision-form').onsubmit = async (e) => {
    e.preventDefault();
    await busy(e.currentTarget, () => mutate('resubmit', {
      copy_text: $('revision-copy').value,
      asset_url: $('revision-url').value.trim() || null
    }));
  };
}
load().catch(e => {
  const loading = $('loading');
  if (loading) {
    loading.hidden = false;
    loading.textContent = 'This campaign could not be loaded. Return to the queue or reload to try again.';
  }
  showError(e);
});
