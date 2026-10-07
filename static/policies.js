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
  notice,
  busy
} from './common.js';

const PRODUCTS = ['PERSONAL_LOAN', 'CREDIT_CARD', 'MORTGAGE_PREQUALIFICATION'];
const CHANNELS = ['AFFILIATE', 'EMAIL', 'SOCIAL', 'PAID_SEARCH', 'WEBSITE'];
const KINDS = [
  ['semantic', 'Semantic review'],
  ['required_disclosure', 'Required disclosure']
];

let context, policies, snapshots, events, canManage = false;

function scopeOptions(currentProduct, currentChannel) {
  const productSel = el('select');
  productSel.name = 'product';
  productSel.append(option('', 'All products'));
  PRODUCTS.forEach(p => productSel.append(option(p, label(p))));
  if (currentProduct) productSel.value = currentProduct;

  const channelSel = el('select');
  channelSel.name = 'channel';
  channelSel.append(option('', 'All channels'));
  CHANNELS.forEach(c => channelSel.append(option(c, label(c))));
  if (currentChannel) channelSel.value = currentChannel;

  const wrapper = el('div', null, 'form-grid');
  const productLabel = el('label');
  productLabel.append(el('span', 'Product scope', 'small'), productSel);
  const channelLabel = el('label');
  channelLabel.append(el('span', 'Channel scope', 'small'), channelSel);
  wrapper.append(productLabel, channelLabel);
  return wrapper;
}

function literalField(requiredLiteral, kind) {
  const labelNode = el('label');
  labelNode.append(el('span', 'Required disclosure literal', 'small'));
  const input = el('input');
  input.name = 'required_literal';
  input.type = 'text';
  input.maxLength = 500;
  input.placeholder = 'Exact text the copy must contain verbatim';
  if (requiredLiteral) input.value = requiredLiteral;
  labelNode.append(input);
  labelNode.hidden = kind !== 'required_disclosure';
  return labelNode;
}

function ruleRow(rule) {
  const row = el('div', null, 'policy-rule');
  const grid = el('div', null, 'form-grid');

  const keyLabel = el('label');
  keyLabel.append(el('span', 'Rule ID', 'small'), (() => {
    const i = el('input');
    i.name = 'rule_key';
    i.required = true;
    i.maxLength = 32;
    i.value = rule.rule_key;
    return i;
  })());

  const titleLabel = el('label');
  titleLabel.append(el('span', 'Title', 'small'), (() => {
    const i = el('input');
    i.name = 'title';
    i.required = true;
    i.maxLength = 120;
    i.value = rule.title;
    return i;
  })());

  const kindLabel = el('label');
  kindLabel.append(el('span', 'Kind', 'small'), (() => {
    const s = el('select');
    s.name = 'kind';
    s.required = true;
    KINDS.forEach(([v, t]) => s.append(option(v, t)));
    s.value = rule.kind;
    return s;
  })());

  grid.append(keyLabel, titleLabel, kindLabel);

  const instrLabel = el('label');
  instrLabel.append(el('span', 'Instructions', 'small'));
  const ta = el('textarea');
  ta.name = 'instructions';
  ta.maxLength = 2000;
  ta.rows = 3;
  ta.placeholder = 'What the semantic reviewer should look for and why';
  ta.value = rule.instructions || '';
  instrLabel.append(ta);

  const enabledLabel = el('label', null, 'check-row');
  enabledLabel.append(el('span', 'Enabled', 'small'));
  const en = el('input');
  en.name = 'enabled';
  en.type = 'checkbox';
  en.checked = !!rule.enabled;
  enabledLabel.append(en);

  const scope = scopeOptions(rule.product, rule.channel);
  const literal = literalField(rule.required_literal, rule.kind);

  const remove = el('button', 'Remove rule', 'danger quiet');
  remove.type = 'button';
  remove.onclick = () => row.remove();

  row.append(grid, instrLabel, enabledLabel, scope, literal, remove);
  return row;
}

function renderRules() {
  const list = $('rule-list');
  list.replaceChildren();
  (policies.draft || []).forEach(r => list.append(ruleRow(r)));
  if (!canManage) {
    list.querySelectorAll('input,select,textarea,button').forEach(c => c.disabled = true);
    $('add-rule').hidden = true;
  }
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

function collectRules() {
  const rows = [...document.querySelectorAll('#rule-list > .policy-rule')];
  return rows.map(row => {
    const inputs = row.querySelectorAll('[name]');
    const rule = {};
    inputs.forEach(i => {
      const name = i.name;
      if (name === 'enabled') {
        rule.enabled = i.checked ? 1 : 0;
      } else if (i.type === 'checkbox') {
        // skip
      } else if (name === 'required_literal') {
        rule.required_literal = i.value.trim() || null;
      } else if (name === 'product') {
        rule.product = i.value || null;
      } else if (name === 'channel') {
        rule.channel = i.value || null;
      } else if (i.value.trim()) {
        rule[name] = i.value.trim();
      }
    });
    return rule;
  });
}

async function refresh() {
  [policies, snapshots, events] = await Promise.all([
    api('/api/policies'),
    api('/api/policies/snapshots').then(r => r.snapshots),
    api('/api/policies/audit').then(r => r.events)
  ]);
  canManage = !!policies.can_manage;
  $('draft-tag').textContent = `Draft v${policies.state.current_draft_version}`;
  $('draft-hint').textContent = canManage
    ? 'You can edit this draft and publish it.'
    : 'Read only — you do not have the manage_policies capability.';
  $('publish-copy').textContent = canManage
    ? 'Save the draft, then publish it as a new immutable version.'
    : 'Publishing requires the manage_policies capability.';
  $('publish-btn').disabled = !canManage;
  renderRules();
  renderSnapshots();
  renderAudit();
}

async function saveDraft() {
  const rules = collectRules();
  if (!rules.length) {
    notice('Add at least one rule before saving the draft.', true);
    return;
  }
  await api('/api/policies/draft', {
    method: 'PUT',
    body: { rules, expected_draft_version: policies.state.current_draft_version }
  });
  await refresh();
  notice('Draft saved. Review the summary above before publishing.');
}

async function publish() {
  await api('/api/policies/publish', {
    method: 'POST',
    body: { expected_draft_version: policies.state.current_draft_version }
  });
  await refresh();
  notice(`Published as version v${policies.state.active_version}. Earlier reviews are now stale.`);
}

async function load() {
  context = await init();
  await refresh();
  $('loading').hidden = true;
  $('detail').hidden = false;

  $('add-rule').onclick = () => {
    $('rule-list').append(ruleRow({ kind: 'semantic', enabled: 1 }));
  };
  $('rules-form').onsubmit = (e) => {
    e.preventDefault();
    return busy(e.currentTarget, saveDraft);
  };
  $('publish-btn').onclick = () => {
    $('publish-dialog').showModal();
  };
  $('publish-confirm').onclick = () => {
    $('publish-dialog').close();
    return busy($('publish-dialog'), publish);
  };
  $('publish-cancel').onclick = () => {
    $('publish-dialog').close();
  };
}

load().catch(e => {
  const loading = $('loading');
  if (loading) {
    loading.hidden = false;
    loading.textContent = 'Policy configuration could not be loaded.';
  }
  showError(e);
});
