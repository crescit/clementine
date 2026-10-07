import {
  $,
  api,
  init,
  el,
  option,
  label,
  date,
  person,
  showError,
  notice
} from './common.js';

const PRODUCTS = ['PERSONAL_LOAN', 'CREDIT_CARD', 'MORTGAGE_PREQUALIFICATION'];
const CHANNELS = ['AFFILIATE', 'EMAIL', 'SOCIAL', 'PAID_SEARCH', 'WEBSITE'];
const KINDS = [
  ['semantic', 'Semantic review'],
  ['required_disclosure', 'Required disclosure']
];
const GROUPS = [
  ['semantic', 'Semantic rules'],
  ['required_disclosure', 'Required disclosures']
];

let context, policies, snapshots, events, canManage = false;
let rules = [];      // working copy being edited
let saved = '[]';    // serialized server draft, for change detection
let selected = 0;

const clean = (r) => {
  const rule = {
    rule_key: (r.rule_key || '').trim(),
    title: (r.title || '').trim(),
    kind: r.kind,
    required_literal: r.kind === 'required_disclosure' ? ((r.required_literal || '').trim() || null) : null,
    product: r.product || null,
    channel: r.channel || null,
    enabled: r.enabled ? 1 : 0
  };
  const instructions = (r.instructions || '').trim();
  if (instructions) rule.instructions = instructions;
  return rule;
};
const serialize = () => JSON.stringify(rules.map(clean));
const dirty = () => serialize() !== saved;

const scopeText = (r) =>
  `${r.product ? label(r.product) : 'All products'} · ${r.channel ? label(r.channel) : 'All channels'}`;

function fillSelect(select, items, blank) {
  select.replaceChildren();
  if (blank) select.append(option('', blank));
  items.forEach(([v, t]) => select.append(option(v, t)));
}

function renderList() {
  const list = $('rule-list');
  list.replaceChildren();
  GROUPS.forEach(([kind, heading]) => {
    const members = rules.map((r, i) => [r, i]).filter(([r]) => r.kind === kind);
    if (!members.length) return;
    list.append(el('p', heading, 'pol-group'));
    members.forEach(([r, i]) => {
      const item = el('button', null, 'pol-item' + (i === selected ? ' selected' : '') + (r.enabled ? '' : ' off'));
      item.type = 'button';
      item.setAttribute('role', 'option');
      item.setAttribute('aria-selected', String(i === selected));
      const top = el('span', null, 'pol-item-top');
      top.append(el('strong', r.title || 'Untitled rule'), el('span', r.enabled ? 'On' : 'Off', 'pol-state'));
      item.append(top, el('span', `${r.rule_key || 'NEW'} · ${scopeText(r)}`, 'small'));
      item.onclick = () => {
        selected = i;
        renderList();
        renderEditor();
      };
      list.append(item);
    });
  });
  $('rule-count').textContent = String(rules.length);
}

function renderEditor() {
  const rule = rules[selected];
  $('editor-empty').hidden = !!rule;
  $('editor-body').hidden = !rule;
  if (!rule) return;
  $('editor-title').textContent = rule.title || 'Untitled rule';
  $('f-title').value = rule.title || '';
  $('f-key').value = rule.rule_key || '';
  $('f-kind').value = rule.kind;
  $('f-literal').value = rule.required_literal || '';
  $('f-literal-wrap').hidden = rule.kind !== 'required_disclosure';
  $('f-instructions').value = rule.instructions || '';
  $('f-product').value = rule.product || '';
  $('f-channel').value = rule.channel || '';
  $('f-enabled').checked = !!rule.enabled;
  $('rules-form').querySelectorAll('input,select,textarea,button').forEach(c => c.disabled = !canManage);
  $('remove-rule').hidden = !canManage;
}

function renderChrome() {
  const isDirty = dirty();
  const active = policies.state.active_version;
  $('active-pill').textContent = active ? `Published v${active}` : 'Nothing published';
  $('draft-pill').textContent = isDirty ? 'Unsaved changes' : `Draft v${policies.state.current_draft_version}`;
  $('draft-pill').className = 'pol-pill' + (isDirty ? ' pol-pill-dirty' : '');
  $('draft-hint').textContent = '';
  $('save-btn').disabled = !canManage || !isDirty;
  $('discard-btn').hidden = !canManage || !isDirty;
  $('publish-btn').disabled = !canManage || isDirty;
  $('publish-copy').textContent = !canManage
    ? ''
    : isDirty
      ? 'Save your changes to enable publishing.'
      : 'Publishing creates a new immutable version used for future reviews.';
  $('add-rule').hidden = !canManage;
  $('readonly-banner').hidden = canManage;
  $('readonly-banner').textContent = 'Read only: your profile lacks the manage_policies permission. Switch to a policy admin from the profile menu (top right) to add, edit or remove rules and publish.';
}

function onEdit(field, read) {
  const node = $(field);
  node.oninput = node.onchange = () => {
    const rule = rules[selected];
    if (!rule) return;
    read(rule, node);
    if (field === 'f-kind') {
      $('f-literal-wrap').hidden = rule.kind !== 'required_disclosure';
    }
    $('editor-title').textContent = rule.title || 'Untitled rule';
    renderList();
    renderChrome();
  };
}

function renderSnapshots() {
  const body = $('snapshots-body');
  body.replaceChildren();
  (snapshots || []).forEach(s => {
    const tr = el('tr');
    const active = policies.state.active_version === s.version;
    tr.append(
      el('td', `v${s.version}${active ? ' · active' : ''}`),
      el('td', s.label || '—'),
      el('td', person(context.users, s.published_by)),
      el('td', date(s.published_at, true)),
      el('td', (s.policy_hash || '').slice(0, 10) + '…', 'mono')
    );
    body.append(tr);
  });
  $('active-summary').textContent = policies.state.active_version
    ? `Active published version: v${policies.state.active_version}. Draft v${policies.state.current_draft_version} is the working copy used for the next publication.`
    : 'No published version yet — reviews will use the baseline snapshot.';
}

function renderAudit() {
  const list = $('audit-list');
  list.replaceChildren();
  (events || []).forEach(e => {
    const li = el('li');
    li.append(
      el('strong', (e.event_type || 'event').replaceAll('_', ' ').replace(/^./, c => c.toUpperCase())),
      el('span', `${person(context.users, e.actor_id)} · ${date(e.created_at, true)}`, 'small')
    );
    list.append(li);
  });
}

function resetWorkingCopy() {
  rules = (policies.draft || []).map(r => ({ ...r }));
  saved = serialize();
  selected = Math.min(selected, Math.max(rules.length - 1, 0));
}

async function refresh() {
  [policies, snapshots, events] = await Promise.all([
    api('/api/policies'),
    api('/api/policies/snapshots').then(r => r.snapshots),
    api('/api/policies/audit').then(r => r.events)
  ]);
  canManage = !!policies.can_manage;
  resetWorkingCopy();
  renderList();
  renderEditor();
  renderChrome();
  renderSnapshots();
  renderAudit();
}

async function saveDraft() {
  if (!rules.length) {
    notice('Add at least one rule before saving the draft.', true);
    return;
  }
  const incomplete = rules.findIndex(r => !(r.rule_key || '').trim() || !(r.title || '').trim());
  if (incomplete >= 0) {
    selected = incomplete;
    renderList();
    renderEditor();
    notice('Every rule needs a title and a rule ID.', true);
    return;
  }
  await api('/api/policies/draft', {
    method: 'PUT',
    body: { rules: rules.map(clean), expected_draft_version: policies.state.current_draft_version }
  });
  await refresh();
  notice('Draft saved. Publish it when you are ready.');
}

async function publish() {
  await api('/api/policies/publish', {
    method: 'POST',
    body: { expected_draft_version: policies.state.current_draft_version }
  });
  await refresh();
  notice(`Published as version v${policies.state.active_version}. Earlier reviews are now stale.`);
}

async function run(task) {
  const buttons = [...document.querySelectorAll('.pol-actions button')];
  buttons.forEach(b => b.disabled = true);
  try {
    await task();
  } catch (e) {
    showError(e);
  } finally {
    renderChrome();
  }
}

function showTab(name) {
  document.querySelectorAll('.pol-tab').forEach(t => {
    const on = t.dataset.tab === name;
    t.classList.toggle('active', on);
    t.setAttribute('aria-selected', String(on));
  });
  ['rules', 'versions', 'activity'].forEach(n => { $(`tab-${n}`).hidden = n !== name; });
}

async function load() {
  context = await init();
  fillSelect($('f-kind'), KINDS);
  fillSelect($('f-product'), PRODUCTS.map(p => [p, label(p)]), 'All products');
  fillSelect($('f-channel'), CHANNELS.map(c => [c, label(c)]), 'All channels');
  await refresh();
  $('loading').hidden = true;
  $('detail').hidden = false;

  onEdit('f-title', (r, n) => { r.title = n.value; });
  onEdit('f-key', (r, n) => { r.rule_key = n.value; });
  onEdit('f-kind', (r, n) => { r.kind = n.value; });
  onEdit('f-literal', (r, n) => { r.required_literal = n.value; });
  onEdit('f-instructions', (r, n) => { r.instructions = n.value; });
  onEdit('f-product', (r, n) => { r.product = n.value || null; });
  onEdit('f-channel', (r, n) => { r.channel = n.value || null; });
  onEdit('f-enabled', (r, n) => { r.enabled = n.checked ? 1 : 0; });

  document.querySelectorAll('.pol-tab').forEach(t => { t.onclick = () => showTab(t.dataset.tab); });

  $('add-rule').onclick = () => {
    rules.push({ kind: 'semantic', enabled: 1, rule_key: '', title: '' });
    selected = rules.length - 1;
    renderList();
    renderEditor();
    renderChrome();
    $('f-title').focus();
  };
  $('remove-rule').onclick = () => {
    rules.splice(selected, 1);
    selected = Math.max(0, Math.min(selected, rules.length - 1));
    renderList();
    renderEditor();
    renderChrome();
  };
  $('discard-btn').onclick = () => {
    resetWorkingCopy();
    renderList();
    renderEditor();
    renderChrome();
  };
  $('rules-form').onsubmit = (e) => e.preventDefault();
  $('save-btn').onclick = () => run(saveDraft);
  $('publish-btn').onclick = () => $('publish-dialog').showModal();
  $('publish-confirm').onclick = () => {
    $('publish-dialog').close();
    return run(publish);
  };
  $('publish-cancel').onclick = () => $('publish-dialog').close();
  window.addEventListener('beforeunload', (e) => {
    if (canManage && dirty()) e.preventDefault();
  });
}

load().catch(e => {
  const loading = $('loading');
  if (loading) {
    loading.hidden = false;
    loading.textContent = 'Policy configuration could not be loaded.';
  }
  showError(e);
});
