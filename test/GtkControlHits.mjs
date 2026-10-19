// SPDX-License-Identifier: GPL-2.0-only
// X11 pointer checks; secondary events are forwarded by the GTK host.
// All parameter responses are browser-only models.
export function prepareNativeControlHits(original) {
  const token = sessionStorage.getItem('octa-token');
  const originalFetch = window.fetch;
  if (window.__octaNativeControlHits) throw new Error('Native pointer check is already active.');
  const state = {originalFetch, token, original, requests: [], events: [],
    wasPreamp: !document.querySelector('#preamp-view').hidden, pointer: null};
  window.__octaNativeControlHits = state;
  const node = document.querySelector('[aria-label="Input 1 / SENS"]');
  if (!state.wasPreamp) document.querySelector('#view-switch').click();
  window.fetch = (path, options) => {
    if (path === '/api/control' && options?.method === 'POST') {
      state.requests.push(JSON.parse(options.body));
      return Promise.resolve(new Response(JSON.stringify(original), {status: 200,
        headers: {'Content-Type': 'application/json'}}));
    }
    return originalFetch.call(window, path, options);
  };
  state.listener = event => {
    if (event.pointerId !== undefined) state.pointer = event.pointerId;
    state.events.push(event);
  };
  state.types = ['pointerdown', 'pointermove', 'pointerup', 'mousedown', 'mouseup', 'contextmenu',
    'gotpointercapture', 'lostpointercapture', 'keydown', 'keyup'];
  for (const type of state.types) document.addEventListener(type, state.listener, true);
  const center = selector => {
    const r = node.querySelector(selector).getBoundingClientRect();
    return {x: r.x + r.width / 2, y: r.y + r.height / 2};
  };
  const value = Number(node.getAttribute('aria-valuenow')), dy = value === 100 ? 8 : -8;
  const button = document.querySelector('[aria-label="Input 1 Hi-Z"]');
  const buttonPoint = selector => {
    const r = button.querySelector(selector).getBoundingClientRect();
    return {x: r.x + r.width / 2, y: r.y + r.height / 2};
  };
  return {label: center('.knob-label'), dial: center('.knob-dial'), dy,
    button: buttonPoint('.lamp'), buttonLabel: buttonPoint('span:not(.lamp)'),
    buttonFrame: button.classList.contains('lit') ? 2 : 0,
    expected: Math.max(0, Math.min(100, value + Math.trunc(-dy * 100 / 64)))};
}

export function nativeControlHitState() {
  const state = window.__octaNativeControlHits;
  const node = document.querySelector('[aria-label="Input 1 / SENS"]');
  const button = document.querySelector('[aria-label="Input 1 Hi-Z"]');
  return {requests: state.requests, events: state.events.map(event => ({type: event.type, button: event.button,
    buttons: event.buttons, x: event.clientX, y: event.clientY, shiftKey: event.shiftKey,
    ctrlKey: event.ctrlKey, altKey: event.altKey, metaKey: event.metaKey,
    key: event.key, trusted: event.isTrusted, target: event.target?.className, prevented: event.defaultPrevented})),
  dragging: node.classList.contains('dragging'),
    captured: state.pointer !== null && node.hasPointerCapture(state.pointer),
    buttonFrame: Number(button.dataset.buttonFrame),
    buttonCaptured: state.pointer !== null && button.hasPointerCapture(state.pointer),
    menus: document.querySelector('#menus').childElementCount, value: Number(node.getAttribute('aria-valuenow'))};
}

export async function finishNativeControlHits() {
  const state = window.__octaNativeControlHits;
  window.fetch = state.originalFetch;
  for (const type of state.types) document.removeEventListener(type, state.listener, true);
  const reply = await window.fetch('/api/state', {headers: {Authorization: `Bearer ${state.token}`}});
  if (!reply.ok || (await reply.json()).state.payload_hex !== state.original.state.payload_hex) {
    throw new Error('Native pointer check changed device state.');
  }
  if (state.wasPreamp === document.querySelector('#preamp-view').hidden) document.querySelector('#view-switch').click();
  delete window.__octaNativeControlHits;
  return true;
}
