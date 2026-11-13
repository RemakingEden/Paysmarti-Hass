const { test, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');
const fs = require('node:fs');
const path = require('node:path');
const script = fs.readFileSync(path.join(__dirname, '../custom_components/paysmarti_visitor/frontend/paysmarti-visitor-card.js'), 'utf8');
let window, card, calls, responses;
const last = { status: 'none' };
const list = { vehicles: [{id:'one',registration:'AA00AAA',label:'AA00AAA (Family)'}], can_save_new:true };
const flush = () => new Promise(resolve => setTimeout(resolve, 0));
async function open() {
  card = window.document.createElement('paysmarti-visitor-card');
  card.setConfig({config_entry_id:'entry-one'});
  window.document.body.append(card);
  card.hass = { callWS: async request => {
    calls.push(request);
    const response = responses[request.service];
    if (typeof response === 'function') return response(request);
    return {response};
  }};
  await flush(); await flush();
  return card.shadowRoot;
}
function change(selector, value, event='input') {
  const el = card.shadowRoot.querySelector(selector);
  if (event === 'change' && el.type === 'checkbox') el.checked = value;
  else el.value = value;
  el.dispatchEvent(new window.Event(event, {bubbles:true}));
}
beforeEach(() => {
  const dom = new JSDOM('<!doctype html><body></body>', {runScripts:'dangerously', url:'http://localhost'});
  window = dom.window;
  window.eval(script);
  calls = [];
  responses = { list_saved_registrations:list, get_last_result:last,
    book_visitor:{registration:'BB00BBB',starts_at:'2026-11-13T10:00:00+00:00',ends_at:'2026-11-13T23:59:00+00:00',reference:'REF1'} };
});
test('appears in the card picker and opens with its default configuration', async () => {
  const pick = window.customCards?.find(item => item.type === 'paysmarti-visitor-card');
  assert.equal(pick?.name, 'Paysmarti Visitor');
  const Card = window.customElements.get('paysmarti-visitor-card');
  const config = Card.getStubConfig();
  assert.equal(Object.keys(config).length, 0);
  card = window.document.createElement('paysmarti-visitor-card');
  card.setConfig(config);
  window.document.body.append(card);
  card.hass = {callWS: async request => {
    calls.push(request);
    return {response: responses[request.service]};
  }};
  await flush(); await flush();
  assert.match(card.shadowRoot.textContent, /AA00AAA/);
  assert.equal(calls[0].service_data.config_entry_id, undefined);
});
test('new text takes precedence and submits normalized registration via response action', async () => {
  const root = await open();
  change('#saved', 'one', 'change'); change('#new', ' bb 00 bbb ');
  assert.match(root.querySelector('#effective').textContent, /BB00BBB/);
  root.querySelector('#book').click(); await flush(); await flush();
  const request = calls.find(x => x.service === 'book_visitor');
  assert.equal(request.service_data.new_registration, 'BB00BBB');
  assert.equal(request.service_data.saved_vehicle_id, undefined);
  assert.equal(request.return_response, true);
});
test('save and nickname appear only for a new registration and clear when unchecked', async () => {
  const root = await open();
  assert.equal(root.querySelector('#save-row').hidden, true);
  change('#new', 'BB00BBB');
  assert.equal(root.querySelector('#save-row').hidden, false);
  change('#save', true, 'change');
  assert.equal(root.querySelector('#nickname-row').hidden, false);
  change('#nickname', 'Guest');
  change('#save', false, 'change');
  assert.equal(root.querySelector('#nickname').value, '');
  assert.equal(root.querySelector('#nickname-row').hidden, true);
  change('#new', '');
  assert.equal(root.querySelector('#save-row').hidden, true);
});
test('invalid new input blocks Book even with a saved selection', async () => {
  const root = await open(); change('#saved','one','change'); change('#new','AA!');
  assert.equal(root.querySelector('#book').disabled, true);
  assert.match(root.querySelector('#validation').textContent, /letters and numbers/i);
});
test('pending booking locks Book and Refresh', async () => {
  let release;
  responses.book_visitor = () => new Promise(resolve => {release = resolve;});
  const root = await open(); change('#new','BB00BBB'); root.querySelector('#book').click();
  assert.equal(root.querySelector('#book').disabled, true);
  assert.equal(root.querySelector('#refresh').disabled, true);
  assert.match(root.querySelector('[role="status"]').textContent, /Booking in progress/);
  await flush();
  release({response:{registration:'BB00BBB',starts_at:'2026-11-13T10:00:00+00:00',ends_at:'2026-11-13T23:59:00+00:00'}});
  await flush();
});
test('confirmed booking shows green receipt and clears new text', async () => {
  const root = await open(); change('#new','BB00BBB'); root.querySelector('#book').click(); await flush(); await flush();
  assert.equal(root.querySelector('#result').dataset.kind, 'booked');
  assert.match(root.querySelector('#result').textContent, /REF1/);
  assert.match(root.querySelector('#result').textContent, /BB00BBB/);
  assert.equal(root.querySelector('#new').value, '');
});
test('newly booked result can be dismissed durably', async () => {
  responses.book_visitor = {attempt_id:'new-attempt',registration:'BB00BBB',
    starts_at:'2026-11-13T10:00:00+00:00',ends_at:'2026-11-13T23:59:00+00:00'};
  responses.dismiss_last_result = request => {
    assert.equal(request.service_data.attempt_id, 'new-attempt');
    return {response:{dismissed:true}};
  };
  const root = await open();
  change('#new','BB00BBB'); root.querySelector('#book').click(); await flush(); await flush();
  root.querySelector('#dismiss-result').click(); await flush();
  assert.equal(root.querySelector('#result').hidden, true);
  assert.equal(calls.filter(x => x.service === 'dismiss_last_result').length, 1);
});
test('known pre-commit failure shows red result and keeps entered text', async () => {
  const root = await open();
  responses.book_visitor = () => {
    responses.get_last_result = {status:'not_booked',masked_registration:'****BBB',safe_details:{stage:'preparation',error_code:'form_changed'}};
    return Promise.reject(new Error('form_changed'));
  };
  change('#new','BB00BBB'); root.querySelector('#book').click(); await flush(); await flush();
  assert.equal(root.querySelector('#result').dataset.kind, 'not_booked');
  assert.match(root.querySelector('#result').textContent, /Not booked/);
  assert.equal(root.querySelector('#new').value, 'BB00BBB');
});
test('ambiguous failure shows amber instruction to check Paysmarti', async () => {
  const root = await open();
  responses.book_visitor = () => {
    responses.get_last_result = {status:'outcome_unknown',masked_registration:'****BBB',safe_details:{stage:'commit',error_code:'outcome_unknown'}};
    return Promise.reject(new Error('outcome_unknown'));
  };
  change('#new','BB00BBB'); root.querySelector('#book').click(); await flush(); await flush();
  assert.equal(root.querySelector('#result').dataset.kind, 'outcome_unknown');
  assert.match(root.querySelector('#result').textContent, /check Paysmarti before trying again/i);
});
test('card reopen restores last result from backend', async () => {
  responses.get_last_result = {status:'booked',masked_registration:'****BBB',safe_details:{starts_at:'2026-11-13T10:00:00+00:00',ends_at:'2026-11-13T23:59:00+00:00'}};
  let root = await open(); assert.equal(root.querySelector('#result').dataset.kind, 'booked');
  card.remove(); root = await open(); assert.equal(root.querySelector('#result').dataset.kind, 'booked');
  assert.equal(calls.filter(x => x.service === 'get_last_result').length, 2);
});
test('dismissed result stays hidden after card reopen', async () => {
  responses.get_last_result = {status:'outcome_unknown',attempt_id:'attempt-one',masked_registration:'****BBB',safe_details:{}};
  responses.dismiss_last_result = request => {
    assert.equal(request.service_data.attempt_id, 'attempt-one');
    responses.get_last_result = {status:'none'};
    return {response:{dismissed:true}};
  };
  let root = await open();
  const dismiss = root.querySelector('#dismiss-result');
  assert.equal(dismiss.getAttribute('aria-label'), 'Dismiss booking result');
  dismiss.click(); await flush();
  assert.equal(root.querySelector('#result').hidden, true);
  assert.equal(calls.filter(x => x.service === 'dismiss_last_result').length, 1);
  card.remove(); root = await open();
  assert.equal(root.querySelector('#result').hidden, true);
});
test('failed dismissal stays visible and explains that it was not saved', async () => {
  responses.get_last_result = {status:'booked',attempt_id:'attempt-one',masked_registration:'****BBB',safe_details:{}};
  responses.dismiss_last_result = () => Promise.reject(new Error('storage unavailable'));
  const root = await open();
  root.querySelector('#dismiss-result').click(); await flush();
  assert.equal(root.querySelector('#result').hidden, false);
  assert.match(root.querySelector('#dismiss-error').textContent, /Could not save the dismissal/);
});
test('new attempt clears local result dismissal', async () => {
  const root = await open();
  responses.get_last_result = {status:'booked',masked_registration:'****AAA',safe_details:{}};
  card._showResult(responses.get_last_result);
  root.querySelector('#dismiss-result').click();
  change('#new','BB00BBB'); root.querySelector('#book').click(); await flush(); await flush();
  assert.equal(root.querySelector('#result').dataset.kind, 'booked');
  assert.equal(root.querySelector('#result').hidden, false);
});
test('a rejected booking with an unchanged older result is Not booked', async () => {
  responses.get_last_result = {status:'booked',started_at:'2026-11-12T10:00:00+00:00',masked_registration:'****AAA',safe_details:{starts_at:'2026-11-12T10:00:00+00:00',ends_at:'2026-11-12T23:59:00+00:00'}};
  responses.book_visitor = () => Promise.reject(new Error('entry_unavailable'));
  const root = await open();
  change('#new','BB00BBB'); root.querySelector('#book').click(); await flush(); await flush();
  assert.equal(root.querySelector('#result').dataset.kind, 'not_booked');
});
test('booking B rejection cannot display booking A confirmed result', async () => {
  const root = await open();
  const prior = {status:'booked',started_at:'2026-11-13T10:00:00+00:00',masked_registration:'****AAA',safe_details:{starts_at:'2026-11-13T10:00:00+00:00',ends_at:'2026-11-13T23:59:00+00:00'}};
  change('#new','AA00AAA'); root.querySelector('#book').click(); await flush(); await flush();
  assert.equal(root.querySelector('#result').dataset.kind, 'booked');
  responses.get_last_result = prior;
  responses.book_visitor = () => Promise.reject(new Error('entry_unavailable'));
  change('#new','BB00BBB'); root.querySelector('#book').click(); await flush(); await flush();
  assert.equal(root.querySelector('#result').dataset.kind, 'not_booked');
  assert.match(root.querySelector('#result').textContent, /Not booked/);
});
test('initial restore gates booking so a late prior result cannot overwrite a new booking', async () => {
  let release;
  responses.get_last_result = () => new Promise(resolve => {release = resolve;});
  const root = await open();
  change('#new','BB00BBB');
  assert.equal(root.querySelector('#book').disabled, true);
  release({response:{status:'booked',started_at:'2026-11-12T10:00:00+00:00',masked_registration:'****AAA',safe_details:{starts_at:'2026-11-12T10:00:00+00:00',ends_at:'2026-11-12T23:59:00+00:00'}}});
  await flush();
  responses.get_last_result = {status:'booked',started_at:'2026-11-12T10:00:00+00:00',masked_registration:'****AAA',safe_details:{starts_at:'2026-11-12T10:00:00+00:00',ends_at:'2026-11-12T23:59:00+00:00'}};
  assert.equal(root.querySelector('#book').disabled, false);
  root.querySelector('#book').click(); await flush(); await flush();
  assert.match(root.querySelector('#result').textContent, /BB00BBB/);
});
test('switching config entry ignores late choices from the previous entry', async () => {
  let releaseOld;
  responses.list_saved_registrations = request => request.service_data.config_entry_id === 'entry-one'
    ? new Promise(resolve => {releaseOld = resolve;})
    : {response:{vehicles:[{id:'two',registration:'CC00CCC',label:'CC00CCC (Second)'}],can_save_new:false}};
  const root = await open();
  card.setConfig({config_entry_id:'entry-two'});
  await flush(); await flush();
  releaseOld({response:list}); await flush();
  assert.match(root.querySelector('#saved').textContent, /CC00CCC/);
  assert.doesNotMatch(root.querySelector('#saved').textContent, /AA00AAA/);
});

test('failed journal read after a rejected request stays outcome unknown', async () => {
  const root = await open();
  let reads = 0;
  responses.get_last_result = () => {
    reads += 1;
    if (reads === 1) return {response:{status:'none'}};
    return Promise.reject(new Error('journal unavailable'));
  };
  responses.book_visitor = () => Promise.reject(new Error('request failed'));
  change('#new','BB00BBB'); root.querySelector('#book').click(); await flush(); await flush();
  assert.equal(root.querySelector('#result').dataset.kind, 'outcome_unknown');
  assert.match(root.querySelector('#result').textContent, /check Paysmarti before trying again/i);
});
