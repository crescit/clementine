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
  resubmit: 'Revision submitted. A new version is ready for review.',
  analyze: 'Analysis saved. Findings are attributed to this version and policy.',
  dispositions: 'Disposition recorded on this finding.',
  exceptions: 'Manual exception recorded. You can now decide on this version.'
};
const DISPOSITION_LABEL = {ACKNOWLEDGED: 'Acknowledged — needs change', DISMISSED: 'Dismissed — not a violation'};

function showReview(current) {
  const rv = record.review;
  $('semantic-panel').hidden = !rv || !rv.enabled;
  if (!rv || !rv.enabled) return;
  const isReviewer = context.actor.id === record.assigned_reviewer_id;
  const open = !['APPROVED', 'REJECTED', 'CHANGES_REQUESTED'].includes(record.status);
  const run = rv.run;
  const pol = rv.policy;
  $('semantic-policy').textContent = pol ? `Policy v${pol.version}${pol.label ? ' · ' + pol.label : ''} · ${Object.keys(pol.rules).length} semantic rules` : 'No published policy — analysis is unavailable.';
  $('analyze').hidden = !(isReviewer && open && current && pol);
  $('analyze').textContent = run ? 'Re-run analysis' : 'Analyze current version';
  let title, summary, cls;
  if (!run) {
    [title, summary, cls] = ['Not analyzed', isReviewer ? 'Run an analysis of the current copy before approving.' : 'Waiting for the assigned reviewer to analyze this version.', 'muted'];
  } else if (rv.stale) {
    [title, summary, cls] = ['Analysis is stale', rv.error, 'check-blocked'];
  } else if (run.status !== 'SUCCESS') {
    [title, summary, cls] = ['Analysis failed', rv.exception ? `Manual exception recorded by ${person(context.users, rv.exception.actor_id)}: ${rv.exception.reason}` : 'The model call failed. Re-run, or record a manual exception and review by hand.', 'check-blocked'];
  } else if (!run.findings.length) {
    [title, summary, cls] = ['No semantic findings', '✓ No implied approval promises found in this version.', 'check-passed'];
  } else {
    const pending = run.findings.filter(f => !f.disposition).length;
    [title, summary, cls] = [`${run.findings.length} semantic finding${run.findings.length === 1 ? '' : 's'}`, pending ? `! ${pending} awaiting a reviewer disposition` : '✓ Every finding has a disposition', pending ? 'check-blocked' : 'check-passed'];
  }
  $('semantic-title').textContent = title;
  $('semantic-summary').className = cls;
  $('semantic-summary').textContent = summary;
  const findings = run && !rv.stale && run.status === 'SUCCESS' ? run.findings : [];
  $('semantic-findings').replaceChildren(...findings.map(f => {
    const item = el('div', null, 'finding');
    const rule = pol && pol.rules[f.rule_key];
    item.append(el('span', `${f.rule_key} · ${f.severity} · source: model (${run.provider_revision || 'unknown'})`, 'small'), el('h3', f.title));
    item.append(el('blockquote', f.evidence_quote));
    item.append(el('p', f.explanation));
    if (rule) item.append(el('p', `Policy: ${rule.instructions}`, 'small'));
    if (f.suggested_revision) item.append(el('p', `Suggested revision: ${f.suggested_revision}`));
    if (f.disposition) {
      item.append(el('p', `${DISPOSITION_LABEL[f.disposition.disposition] || f.disposition.disposition} · ${person(context.users, f.disposition.actor_id)} · ${date(f.disposition.created_at, true)} — ${f.disposition.reason}`, 'small check-passed'));
    } else if (isReviewer && open && current) {
      const form = el('form', null, 'form-stack');
      const reason = el('textarea');
      reason.rows = 2; reason.maxLength = 2000; reason.required = true; reason.placeholder = 'Reason (required)';
      reason.setAttribute('aria-label', `Disposition reason for ${f.rule_key}`);
      const actions = el('div', null, 'actions');
      for (const [value, text] of [['ACKNOWLEDGED', 'Acknowledge'], ['DISMISSED', 'Dismiss']]) {
        const b = el('button', text); b.type = 'submit'; b.value = value; actions.append(b);
      }
      form.append(reason, actions);
      form.onsubmit = async (e) => {
        e.preventDefault();
        if (!reason.value.trim()) { reason.reportValidity(); return; }
        await busy(form, () => reviewAction('dispositions', {run_id: run.id, finding_id: f.finding_id, disposition: e.submitter.value, reason: reason.value.trim()}));
      };
      item.append(form);
    }
    return item;
  }));
  $('exception-form').hidden = !(isReviewer && open && current && run && !rv.stale && run.status !== 'SUCCESS' && !rv.exception);
  $('semantic-history').hidden = !rv.history.length;
  $('semantic-runs').replaceChildren(...rv.history.map(h => {
    const li = el('li');
    li.append(el('strong', `${label(h.status)} · ${h.finding_count} finding${h.finding_count === 1 ? '' : 's'}`), el('span', `Version ${h.content_version} · Policy v${h.snapshot_version} · ${h.provider_revision || 'no model response'} · ${h.prompt_version} · ${person(context.users, h.actor_id)} · ${date(h.created_at, true)}`, 'small'));
    return li;
  }));
}
async function reviewAction(action, body) {
  await api(`${path}/${action}`, {method: 'POST', body, timeoutMs: action === 'analyze' ? 180000 : 15000});
  await render();
  notice(ACTION_SAVED[action]);
}

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
  const semanticBlocked = record.review && record.review.enabled && !record.review.approvable;
  $('approve').disabled = !current || !record.preflight.passed || semanticBlocked;
  $('approve-hint').textContent = !current ? 'Select the current version to make a decision.' : !record.preflight.passed ? 'Approval is blocked until the findings above are resolved.' : semanticBlocked ? `Approval is blocked by semantic review: ${record.review.error}` : 'Approves the exact current copy shown here.';
  showReview(current);
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
  $('analyze').onclick = async (e) => {
    const button = e.currentTarget;
    button.disabled = true;
    $('semantic-title').textContent = 'Analyzing…';
    $('semantic-summary').className = 'muted';
    $('semantic-summary').textContent = 'The model is reviewing the current copy against the published policy. This can take up to a minute.';
    try {
      await reviewAction('analyze', {});
    } catch (err) {
      showError(err);
      await render();
    } finally {
      button.disabled = false;
    }
  };
  $('exception-form').onsubmit = async (e) => {
    e.preventDefault();
    await busy(e.currentTarget, () => reviewAction('exceptions', {run_id: record.review.run.id, reason: $('exception-reason').value.trim()}));
    $('exception-reason').value = '';
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
