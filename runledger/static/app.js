'use strict';
const $ = selector => document.querySelector(selector);
let selected = null, current = null, busy = false, replayResult = null;
const terminal = new Set(['succeeded', 'failed', 'cancelled']);
const names = {read_incident: 'Collect incident evidence', assess_incident: 'Assess impact', publish_report: 'Publish incident report'};
const descriptions = {read_incident: 'Read a synthetic incident record. Persist the exact result.', assess_incident: 'Apply a deterministic policy to the recorded evidence.', publish_report: 'Human approval required. Stable idempotency key at the report service.'};
function node(tag, text, cls) { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; if (cls) el.className = cls; return el; }
function badge(state) { return node('span', state.replaceAll('_', ' '), 'badge ' + state); }
function notify(text, error = false) { $('#notification').textContent = text; $('#notification').className = error ? 'error' : ''; $('#notification').hidden = false; }
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Runledger-Client': 'operator-console'}, body: JSON.stringify(body)});
  const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Request failed'); return data;
}
async function action(work) {
  if (busy) return; busy = true; updateControls();
  try { await work(); } catch (error) { notify(error.message, true); }
  finally { busy = false; await refresh().catch(error => notify(error.message, true)); }
}
async function create() { await action(async () => { const data = await api('/api/runs', {request_id: crypto.randomUUID()}); selected = data.run_id; replayResult = null; notify('Investigation created. Advance the first two tools, then review the proposed report.'); }); }
async function refresh() {
  const {runs} = await api('/api/runs');
  $('#run-count').textContent = runs.length;
  if (!selected && runs.length) selected = runs[0].id;
  $('#runs').replaceChildren(...runs.map(run => {
    const button = node('button', undefined, 'run-link' + (run.id === selected ? ' active' : ''));
    button.append(node('span', '', 'status-dot ' + run.state), node('span', run.name), node('small', run.state + ' · ' + run.id.slice(0, 8)));
    button.onclick = () => { selected = run.id; replayResult = null; refresh().catch(error => notify(error.message, true)); };
    return button;
  }));
  $('#empty').hidden = Boolean(selected); $('#detail').hidden = !selected;
  if (!selected) return;
  const requested = selected;
  const run = await api('/api/runs/' + requested);
  if (requested !== selected) return;
  current = run;
  $('#run-name').textContent = run.name; $('#run-id').textContent = run.id;
  $('#run-state').replaceWith(Object.assign(badge(run.state), {id: 'run-state'}));
  $('#export').href = '/api/runs/' + run.id + '/export';
  $('#checkpoint-count').textContent = run.steps.filter(s => s.state === 'succeeded').length + ' / ' + run.steps.length;
  $('#attempt-count').textContent = run.steps.reduce((total, s) => total + s.attempt, 0);
  $('#receipt-count').textContent = run.receipts.length; $('#receipt-badge').textContent = run.receipts.length;
  $('#journal-status').textContent = run.journal_valid ? 'Verified' : 'Mismatch';
  $('#event-count').textContent = run.events.length + ' hash-linked events';
  $('#receipts').textContent = run.receipts.length ? JSON.stringify(run.receipts, null, 2) : 'No report published.';
  const opened = new Set([...document.querySelectorAll('#steps details[open]')].map(el => el.dataset.step));
  $('#steps').replaceChildren(...run.steps.map((step, index) => {
    const card = node('article', undefined, 'step ' + step.state);
    card.append(node('div', step.state === 'succeeded' ? '✓' : String(index + 1).padStart(2, '0'), 'step-number'));
    const body = node('div'), title = node('div', undefined, 'step-title');
    title.append(node('strong', names[step.tool]), badge(step.state)); body.append(title, node('p', descriptions[step.tool]));
    const lease = step.state === 'running' ? (step.lease_until * 1000 <= Date.now() ? ' · lease expired — ready for recovery' : ' · lease active') : '';
    body.append(node('p', 'attempt ' + step.attempt + ' / epoch ' + step.epoch + lease + (step.error ? ' · ' + step.error : '')));
    if (step.output || step.inputs) { const details = node('details'); details.dataset.step = step.id; details.open = opened.has(step.id); details.append(node('summary', step.output ? 'Inspect checkpoint' : 'Inspect proposed inputs'), node('pre', JSON.stringify(step.output || step.inputs, null, 2))); body.append(details); }
    card.append(body); return card;
  }));
  const proposal = run.steps.find(step => step.state === 'waiting');
  $('#approval').hidden = !proposal;
  if (proposal) {
    const box = node('div', undefined, 'approval-box'); box.append(node('h4', 'Approval required'), node('p', 'Publish this exact assessment to the local report service. Approval is bound to the payload below.'), node('pre', JSON.stringify(proposal.inputs, null, 2)), node('code', 'action digest: ' + proposal.approval_hash));
    const approve = node('button', 'Approve report', 'primary'), reject = node('button', 'Reject', 'secondary');
    for (const [button, decision] of [[approve, true], [reject, false]]) { button.disabled = busy; button.onclick = () => action(async () => { await api('/api/runs/' + selected + '/approve', {step_id: proposal.id, approval_hash: proposal.approval_hash, approve: decision}); notify(decision ? 'Approved. Try “Crash after report write” to reproduce the ambiguous commit window.' : 'Action rejected. The report was not published.'); }); }
    box.append(approve, reject); $('#approval').replaceChildren(box);
  }
  $('#events').replaceChildren(...run.events.slice().reverse().map(event => {
    const tr = node('tr', undefined, ['lease_reclaimed', 'approval_requested', 'retry_scheduled'].includes(event.kind) ? 'highlight' : '');
    const detail = [event.data.step, event.data.epoch ? 'epoch ' + event.data.epoch : null, event.data.attempt ? 'attempt ' + event.data.attempt : null].filter(Boolean).join(' · ') || '—';
    tr.append(...[String(event.seq).padStart(2, '0'), event.kind, detail, new Date(event.at * 1000).toLocaleTimeString(), event.hash.slice(0, 12)].map(text => node('td', text))); return tr;
  }));
  renderReplay(); updateControls();
}
function updateControls() {
  $('#create').disabled = busy; $('#create-empty').disabled = busy;
  if (!current) return;
  const stopped = terminal.has(current.state), waiting = current.state === 'waiting';
  for (const id of ['tick', 'transient']) $('#' + id).disabled = busy || stopped || waiting;
  const pending = current.steps.find(step => step.state !== 'succeeded');
  $('#crash').disabled = busy || stopped || waiting || pending?.tool !== 'publish_report';
  $('#cancel').disabled = busy || stopped;
  $('#replay-original').disabled = busy; $('#replay-changed').disabled = busy;
}
function renderReplay() {
  $('#replay-result').replaceChildren(); if (!replayResult) return;
  $('#replay-result').append(node('p', replayResult.divergences + ' divergences · 0 side effects executed', 'replay-summary'));
  for (const row of replayResult.rows) {
    const div = node('div', undefined, 'replay-row ' + row.status);
    div.append(node('span', row.status.replaceAll('_', ' ')), node('strong', row.step));
    if (row.status === 'diverged') div.append(node('pre', 'RECORDED\n' + JSON.stringify(row.recorded, null, 2) + '\n\nCANDIDATE\n' + JSON.stringify(row.candidate, null, 2)));
    $('#replay-result').append(div);
  }
  $('#replay-result').append(node('p', 'Changed policy raises the deterministic severity threshold from 5% to 20%. This is a per-step comparison, not a model-quality score.', 'muted'));
}
async function advance(fault) { await action(async () => {
  const result = await api('/api/runs/' + selected + '/tick', fault ? {fault} : {});
  const messages = {worker_crashed: 'Worker lost after report commit. The receipt exists, but its checkpoint does not. Wait 2 seconds, then advance to recover.', completed: 'Checkpoint committed for ' + result.step + '.', idle: result.state === 'waiting' ? 'Review the exact proposed report in the approval panel.' : 'No step is ready yet. A lease or retry delay may still be active.', retry_scheduled: 'Injected transient failure. A bounded retry was scheduled.', failed: 'Tool failed. Inspect the event journal.', stale_worker_rejected: 'Stale worker completion was rejected.'};
  notify(messages[result.outcome] || result.outcome);
}); }
$('#create').onclick = create; $('#create-empty').onclick = create;
$('#tick').onclick = () => advance(); $('#transient').onclick = () => advance('transient'); $('#crash').onclick = () => advance('after_effect');
$('#cancel').onclick = () => action(async () => { await api('/api/runs/' + selected + '/cancel', {}); notify('Run cancelled. Previously issued effects cannot be undone.'); });
for (const [id, policy] of [['replay-original', 'recorded'], ['replay-changed', 'conservative']]) $('#' + id).onclick = () => action(async () => { replayResult = await api('/api/runs/' + selected + '/replay', {policy}); });
refresh().catch(error => notify(error.message, true));
setInterval(() => { if (!busy && !document.hidden) refresh().catch(error => notify(error.message, true)); }, 1500);
