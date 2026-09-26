import {
  $,
  api,
  init,
  showError,
  notice,
  busy
} from './common.js';
async function load() {
  const {
    actor
  } = await init();
  if (actor.role !== 'SUBMITTER') {
    notice('Switch to Jessica Lin in the persona selector to submit a campaign. Reviewers manage decisions and assignments.', false, {
      sticky: true
    });
    return;
  }
  $('intake-fields').disabled = false;
  $('launch-date').min = new Date().toISOString().slice(0, 10);
  $('channel').onchange = () => {
    const affiliate = $('channel').value === 'AFFILIATE';
    $('partner-label').hidden = !affiliate;
    $('partner').required = affiliate;
    if (!affiliate) $('partner').value = '';
  };
  $('intake-form').onsubmit = async (event) => {
    event.preventDefault();
    await busy($('intake-form'), async () => {
      const body = Object.fromEntries(new FormData(event.target));
      body.asset_url = body.asset_url || null;
      body.partner = body.partner || null;
      const result = await api('/api/submissions', {
        method: 'POST',
        body
      });
      location.href = `/static/submission.html?id=${encodeURIComponent(result.id)}&created=true`;
    });
  };
}
load().catch(showError);
