// SPDX-License-Identifier: GPL-2.0-only
import {PREAMP_MAX, VOLUME, volumeIndex,
  volumeText, parameterText, panToWire, wireToPan, panText, sensitivityIndex, preDelayText, reverbTimeText} from './parameters.js';
import {MeterBallistics, METER_TICK_MS, meterDb, meterY} from './meters.js';
import {PopupMenus} from './menus.js';
import {PanelWarnings} from './warnings.js';
import {drawCompressor} from './graphs.js';
import {ReferenceStepper} from './steppers.js';
import {masterFaderPreview} from './faders.js';

const $ = selector => document.querySelector(selector);
const all = selector => [...document.querySelectorAll(selector)];
const el = (tag, className = '', text = '') => {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = text;
  return node;
};
const popups = new PopupMenus($('#menus'), () => {
  for (const button of all('[data-menu]')) button.setAttribute('aria-expanded', 'false');
});
const tokenFromLink = new URLSearchParams(location.hash.slice(1)).get('token');
let token = tokenFromLink;
try {
  token ||= sessionStorage.getItem('octa-token');
  if (token) sessionStorage.setItem('octa-token', token);
} catch { /* The launch link also works without browser storage. */ }
history.replaceState(null, '', location.pathname);

let documentState = null, connected = false, busy = false, activeView = 'preamp', bus = 0;
let revision = 0, writing = false, toastTimer;
let stopped = false;
let localBusy = false;
const knobs = [], switches = [], plots = [], faders = [];
const meterViews = [];
let meterSnapshot = null, meterReceived = 0, meterLastID = 0, meterLastRate = null;
let meterTick = performance.now();
const pending = new Map(), queued = new Map();
const faderPreviews = new Map();
const REVERB_TYPES = ['OFF', 'ECHO', 'ROOM', 'SMALL HALL', 'LARGE HALL'];
let reverbDelay, reverbTime;
const ROUTES = ['Direct Mix A', 'Direct Mix B', 'Direct Mix C', 'Direct Mix D',
  'WAVE OUT 1-2', 'WAVE OUT 3-4', 'WAVE OUT 5-6', 'WAVE OUT 7-8', 'WAVE OUT 9-10'];
const VOLUME_PRESETS = [0x7fffff, 0x3fd930, 0x200000, 0x1009b9, 0x809bc,
  0x4074f, 0x204e1, 0x1030d, 0x81d5, 0];
const VOLUME_LABELS = ['+12 dB', '+6 dB', '0 dB', '-6 dB', '-12 dB',
  '-18 dB', '-24 dB', '-30 dB', '-36 dB', '-INF dB'];

function notify(message) {
  clearTimeout(toastTimer);
  $('#toast').textContent = message;
  $('#toast').hidden = false;
  toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 6500);
}

async function api(path, body, binary = false) {
  const headers = {'Authorization': `Bearer ${token || ''}`};
  const options = {headers, cache: 'no-store', credentials: 'omit'};
  if (body !== undefined) {
    options.method = 'POST';
    headers['Content-Type'] = binary ? 'application/octet-stream' : 'application/json';
    options.body = binary ? body : JSON.stringify(body);
  }
  const response = await fetch(path, options);
  if (!response.ok) {
    const data = await response.json();
    const error = new Error(data.error || 'Device command failed.');
    error.status = response.status;
    throw error;
  }
  if (binary && body === undefined) return response.arrayBuffer();
  return response.json();
}

function key(control) {
  return [control.kind, control.source, control.bus, control.channel,
    control.parameter, control.pair, control.bank].join(':');
}

function setConnected(value, message = 'The device could not be found.') {
  connected = value;
  $('#panel').classList.toggle('disconnected', !value);
  $('#connection-message').hidden = value;
  $('#connection-message').textContent = message;
  $('#view-switch').disabled = !value;
  $('#header-action').disabled = !value;
  $('[data-menu="device"]').hidden = !value;
  $('#device-status').hidden = false;
  $('#device-status').setAttribute('aria-label', value ? 'Identify OCTA-CAPTURE' : 'Select OCTA-CAPTURE');
  if (!value) $('#device-status').textContent = 'Select OCTA-CAPTURE…';
  if (!value) {
    $('#sample-rate').disabled = true;
    $('#settings-device').disabled = !canSelectDevice() || !$('#settings-device').value;
    if ($('#settings').open) void settingsDevices();
    reverbDelay?.sync(reverbDelay.value, false, true);
    reverbTime?.sync(reverbTime.value, false, true);
  }
}

function queueChange(control) {
  if (!connected || busy) return;
  const identifier = key(control);
  pending.set(identifier, control.value ?? control.selection);
  queued.delete(identifier);
  queued.set(identifier, control);
  if (control.kind === 'master' && control.parameter === 'volume') {
    faderPreviews.delete(identifier);
    faderPreviews.set(identifier, control);
    if (documentState) renderFaders(documentState.state);
  }
  if (!writing) void flushChanges();
}

async function flushChanges() {
  writing = true;
  while (queued.size) {
    const [identifier, control] = queued.entries().next().value;
    queued.delete(identifier);
    revision++;
    try {
      const result = await api('/api/control', control);
      if (!queued.has(identifier)) {
        pending.delete(identifier);
        faderPreviews.delete(identifier);
      }
      update(result);
    } catch (error) {
      pending.delete(identifier);
      queued.clear();
      pending.clear();
      faderPreviews.clear();
      if (error.status === 503) setConnected(false);
      notify(error.message);
    }
  }
  writing = false;
  if (documentState && connected) update(documentState);
  else if (documentState) renderFaders(documentState.state);
}

function preamp(channel, parameter, value) {
  return {kind: 'preamp', channel, parameter, value};
}

function preampValue(state, channel, parameter) {
  const input = state.preamps[channel];
  const simple = {phantom: input.phantom, 'low-cut': input.low_cut, phase: input.phase_inverted,
    'hi-z': input.hi_z, sensitivity: input.sensitivity_index,
    'stereo-link': state.stereo_links[channel >> 1], compressor: input.compressor.enabled};
  return parameter in simple ? Number(simple[parameter]) : input.compressor[`${parameter}_index`];
}

class Knob {
  constructor(label, maximum, format, control, read, extraClass = '', step = 1) {
    this.maximum = maximum;
    this.format = format;
    this.control = control;
    this.read = read;
    this.step = step;
    this.value = 0;
    this.dragging = false;
    this.node = el('div', `knob ${extraClass}`);
    this.node.tabIndex = 0;
    this.node.setAttribute('role', 'slider');
    this.node.setAttribute('aria-label', label);
    this.node.setAttribute('aria-valuemin', '0');
    this.node.setAttribute('aria-valuemax', String(maximum));
    this.label = el('span', 'knob-label', label.split(' / ').at(-1));
    this.dial = el('span', 'knob-dial');
    this.dial.append(el('span', 'knob-needle'));
    this.output = el(extraClass === 'sensitivity' ? 'input' : 'output', 'knob-value');
    this.editing = false;
    if (extraClass === 'sensitivity') this.sensitivityEditor(label);
    this.node.append(this.label, this.dial, this.output);
    this.node.addEventListener('pointerdown', event => this.start(event));
    this.node.addEventListener('pointermove', event => this.move(event));
    for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) {
      this.node.addEventListener(name, () => this.end());
    }
    this.node.addEventListener('keydown', event => this.keyboard(event));
    this.node.addEventListener('contextmenu', event => {
      if (!this.editing) controlMenu(event, this.control(this.value));
    });
    knobs.push(this);
    this.display();
  }
  display(caption) {
    const text = caption || this.format(this.value);
    this.dial.style.setProperty('--angle', `${-135 + 270 * this.value / this.maximum}deg`);
    if (this.output instanceof HTMLInputElement) this.output.value = text;
    else this.output.textContent = text;
    this.node.title = text;
    this.node.setAttribute('aria-valuenow', String(this.value));
    this.node.setAttribute('aria-valuetext', text);
  }
  set(value, caption) {
    if (this.dragging || this.editing || pending.has(key(this.control(this.value)))) return;
    if (!Number.isFinite(value)) return;
    this.value = Math.max(0, Math.min(this.maximum, value));
    this.display(caption);
  }
  change(value) {
    const next = Math.max(0, Math.min(this.maximum, Math.round(value)));
    if (next === this.value) return;
    this.value = next;
    this.display();
    queueChange(this.control(next));
  }
  start(event) {
    if (event.target === this.output && this.output instanceof HTMLInputElement) return;
    if (event.button !== 0 || !connected || busy || this.node.closest('.unavailable-channel')) return;
    event.preventDefault();
    this.node.focus();
    this.node.setPointerCapture(event.pointerId);
    this.origin = {x: event.clientX, y: event.clientY, value: this.value};
    this.dragging = true;
    this.node.classList.add('dragging');
  }
  move(event) {
    if (!this.dragging) return;
    const delta = (this.origin.y - event.clientY + (event.clientX - this.origin.x) / 2);
    const scale = event.shiftKey ? 12 : 3;
    this.change(Math.round((this.origin.value + delta / scale) / this.step) * this.step);
  }
  end() {
    this.dragging = false;
    this.node.classList.remove('dragging');
  }
  keyboard(event) {
    if (!connected || busy || this.editing || this.node.closest('.unavailable-channel')) return;
    let value = this.value;
    const step = event.shiftKey ? 1 : this.step;
    if (['ArrowUp', 'ArrowRight'].includes(event.key)) value += step;
    else if (['ArrowDown', 'ArrowLeft'].includes(event.key)) value -= step;
    else if (event.key === 'Home') value = 0;
    else if (event.key === 'End') value = this.maximum;
    else return;
    event.preventDefault();
    this.change(value);
  }
  sensitivityEditor(label) {
    this.output.type = 'text';
    this.output.inputMode = 'decimal';
    this.output.maxLength = 4;
    this.output.readOnly = true;
    this.output.setAttribute('aria-label', `${label.split(' / ')[0]} sensitivity value`);
    this.output.title = 'Double-click or press Enter to enter sensitivity in dB.';
    this.output.addEventListener('dblclick', () => this.beginEdit());
    this.output.addEventListener('blur', () => this.finishEdit(true));
    this.output.addEventListener('keydown', event => {
      if (event.ctrlKey || event.metaKey) return;
      if (!this.editing) {
        if (event.key === 'Enter' || event.key === 'F2') {
          event.preventDefault(); event.stopPropagation(); this.beginEdit();
        }
        return;
      }
      event.stopPropagation();
      if (event.key === 'Enter' || event.key === 'Escape') {
        event.preventDefault(); this.finishEdit(event.key === 'Enter'); this.output.blur();
      }
    });
    this.output.addEventListener('beforeinput', event => {
      if (event.inputType === 'insertText' && event.data && !/^[0-9.]+$/.test(event.data)) event.preventDefault();
    });
  }
  beginEdit() {
    if (!connected || busy || this.node.closest('.unavailable-channel')) return;
    this.editing = true;
    this.output.readOnly = false;
    this.output.value = (this.value / 2).toFixed(1);
    this.output.focus();
    this.output.select();
  }
  finishEdit(commit) {
    if (!this.editing) return;
    const value = sensitivityIndex(this.output.value);
    this.editing = false;
    this.output.readOnly = true;
    if (commit && value !== null && connected && !busy && !this.node.closest('.unavailable-channel')) this.change(value);
    this.display();
  }
}

function toggle(label, control, read, className = '') {
  const button = el('button', `toggle ${className}`);
  button.append(el('span', 'toggle-label', label), el('span', 'lamp'));
  button.setAttribute('aria-label', label);
  button.setAttribute('aria-pressed', 'false');
  button.dataset.control = '';
  button.addEventListener('click', () => {
    if (documentState) queueChange(control(Number(!read(documentState.state))));
  });
  button.addEventListener('contextmenu', event => controlMenu(event, control(0)));
  switches.push({button, control, read});
  return button;
}

function meter(kind, channel) {
  const node = el('div', `meter ${kind}`);
  node.dataset.channel = channel;
  node.append(el('span', 'meter-clip'), el('span', 'meter-clip-title', 'CLIP'), el('span', 'meter-fill'), el('span', 'meter-peak'),
    el('span', 'meter-name', kind.toUpperCase()));
  const scale = el('span', 'meter-scale');
  for (const text of ['-2', '-6', '-12', '-24', '-48']) scale.append(el('span', '', text));
  node.append(scale);
  if (kind === 'out') node.title = 'USB capture peak after the compressor.';
  meterViews.push({node, source: kind === 'in' ? 'input' : 'capture', channel,
    model: new MeterBallistics(kind === 'in' ? -2 : 0)});
  return node;
}

function makePreamps() {
  for (let channel = 0; channel < 8; channel++) {
    const strip = el('section', 'preamp-strip');
    strip.dataset.channel = channel;
    if (channel % 2 === 0) strip.append(el('span', 'pair-title', 'PREAMP'));
    const controls = el('div', 'preamp-controls');
    const sensitivity = new Knob(`Input ${channel + 1} / SENS`, 100,
      value => parameterText('sensitivity', value), value => preamp(channel, 'sensitivity', value),
      state => preampValue(state, channel, 'sensitivity'), 'sensitivity');
    controls.append(sensitivity.node);
    const buttons = el('div', 'preamp-switches');
    for (const [parameter, label] of [['phantom', '+48'], ['hi-z', 'Hi-Z'], ['low-cut', 'LO-CUT'], ['phase', 'PHASE']]) {
      const button = toggle(label, value => preamp(channel, parameter, value),
        state => preampValue(state, channel, parameter), parameter === 'phantom' ? 'red' : '');
      button.setAttribute('aria-label', `Input ${channel + 1} ${label}`);
      if (parameter === 'hi-z' && channel >= 2) {
        button.classList.add('placeholder');
        button.tabIndex = -1;
      }
      buttons.append(button);
    }
    controls.append(buttons);
    strip.append(controls);
    const compressor = el('div', 'compressor');
    const bypass = toggle('BYPASS', value => preamp(channel, 'compressor', 1 - value),
      state => !state.preamps[channel].compressor.enabled, 'bypass');
    bypass.replaceChildren(el('span', 'lamp'), el('span', '', 'BYPASS'));
    bypass.setAttribute('aria-label', `Input ${channel + 1} compressor bypass`);
    const canvas = el('canvas', 'curve');
    canvas.width = 102;
    canvas.height = 102;
    canvas.setAttribute('aria-label', `Input ${channel + 1} compressor transfer curve`);
    const axis = el('div', 'graph-axis');
    for (const value of ['-48', '-24', '0']) axis.append(el('span', '', value));
    const verticalAxis = el('div', 'graph-y-axis');
    for (const value of ['0', '-24', '-48']) verticalAxis.append(el('span', '', value));
    compressor.append(meter('in', channel), meter('out', channel), bypass, canvas, axis, verticalAxis);
    plots.push({canvas, channel});
    strip.append(compressor);
    const parameters = el('div', 'parameters');
    for (const parameter of ['gate', 'threshold', 'gain', 'attack', 'release', 'ratio']) {
      const knob = new Knob(`Input ${channel + 1} / ${parameter.toUpperCase()}`, PREAMP_MAX[parameter],
        value => parameterText(parameter, value), value => preamp(channel, parameter, value),
        state => preampValue(state, channel, parameter));
      parameters.append(knob.node);
    }
    strip.append(parameters, el('div', 'strip-footer'), el('span', 'channel-number', String(channel + 1)));
    if (channel % 2 === 0) {
      const link = toggle('LINK', value => preamp(channel, 'stereo-link', value),
        state => state.stereo_links[channel >> 1], 'pair-link');
      link.setAttribute('aria-label', `Compressor stereo link ${channel + 1}-${channel + 2}`);
      strip.append(link, el('span', 'compressor-pill', 'COMPRESSOR'));
    }
    $('#preamp-view').append(strip);
  }
}

function mixerControl(source, channel, parameter, value, kind = 'mixer') {
  return {kind, source, bus, channel, parameter, value};
}
function mixerValue(state, source, channel, parameter) {
  const group = state.mixers[bus][source];
  if (parameter === 'link' || parameter === 'mono') return Number(group.pairs[channel >> 1][parameter === 'link' ? 'linked' : 'mono']);
  if (parameter === 'send') return state.reverb.input_send[channel];
  return Number(group.channels[channel][parameter]);
}

function fader(control, read, label, channel = null, source = null) {
  const wrapper = el('div', 'fader');
  wrapper.dataset.source = source || control(0).source;
  const input = el('input');
  input.type = 'range'; input.min = '0'; input.max = '1024'; input.step = '1';
  input.setAttribute('aria-label', label);
  input.dataset.control = '';
  input.addEventListener('input', () => queueChange(control(VOLUME[Number(input.value)])));
  input.addEventListener('contextmenu', event => controlMenu(event, control(0)));
  const output = el('output', 'fader-value');
  output.setAttribute('aria-hidden', 'true');
  wrapper.append(input, output);
  if (channel !== null) {
    const lane = el('span', 'level-lane');
    lane.dataset.channel = channel;
    lane.append(el('span', 'level-fill'), el('span', 'meter-peak'), el('span', 'meter-clip'));
    meterViews.push({node: lane, source: source === 'input' ? 'capture' : 'playback', channel,
      model: new MeterBallistics()});
    wrapper.append(lane);
  }
  faders.push({input, output, control, read});
  return wrapper;
}

function makeMixers() {
  for (const [index, letter] of [...'ABCD'].entries()) {
    const tab = el('button', '', letter);
    tab.setAttribute('role', 'tab');
    tab.setAttribute('aria-selected', String(index === bus));
    tab.addEventListener('click', () => {
      if (index !== bus && connected && !busy) {
        bus = index;
        queueChange({kind: 'display', bus});
        if (documentState) update(documentState);
      }
    });
    $('#mixer-tabs').append(tab);
  }
  $('#mixer-tabs').append(el('span', 'routing-summary'));
  for (const source of ['input', 'playback']) {
    const group = el('section', 'mixer-group');
    const title = el('div', 'group-title'); title.dataset.source = source;
    group.append(title);
    const channels = el('div', 'mixer-channels');
    for (let channel = 0; channel < 10; channel++) {
      const strip = el('section', 'mixer-strip'); strip.dataset.channel = channel;
      strip.dataset.source = source;
      if (channel % 2 === 0) {
        strip.append(toggle('LINK', value => mixerControl(source, channel, 'link', value),
          state => mixerValue(state, source, channel, 'link'), 'mixer-pair-link'));
      }
      const buttons = el('div', 'mixer-switches');
      for (const parameter of ['mute', 'solo']) {
        buttons.append(toggle(parameter.toUpperCase(), value => mixerControl(source, channel, parameter, value),
          state => mixerValue(state, source, channel, parameter)));
      }
      strip.append(buttons);
      const send = el('div', `mixer-knob${source === 'input' ? ' mixer-send' : ''}`);
      if (source === 'input') {
        const knob = new Knob(`Input ${channel + 1} / REVERB`, 1024, value => volumeText(VOLUME[value]),
          value => mixerControl(source, channel, 'send', VOLUME[value]),
          state => volumeIndex(state.reverb.input_send[channel]));
        send.append(knob.node);
      }
      strip.append(send);
      const pan = new Knob(`${source} ${channel + 1} / PAN`, 200, panText,
        value => mixerControl(source, channel, 'pan', panToWire(value)),
        state => wireToPan(mixerValue(state, source, channel, 'pan')), 'mixer-knob', 5);
      strip.append(pan.node, el('p', 'channel-label', String(channel + 1)),
        fader(value => mixerControl(source, channel, 'volume', value),
          state => mixerValue(state, source, channel, 'volume'), `${source} ${channel + 1} volume`,
          channel, source));
      channels.append(strip);
    }
    group.append(channels);
    $('#mixer-groups').append(group);
  }
  makeMasters();
}

function makeMasters() {
  const master = el('section', 'master-panel');
  master.append(el('div', 'group-title', 'MASTER'));
  const top = el('div', 'master-top');
  top.append(el('div', 'output-jacks', 'OUTPUT JACK\n1/2  3/4  5/6  7/8  9/10'));
  top.append(toggle('LINK', value => mixerControl('input', 0, 'io-link', value, 'master'),
    state => state.mixers[bus].input_output_link, 'master-io-link'));
  const reverb = el('button', 'toggle master-reverb-type');
  reverb.append(el('span', 'lamp'), el('span', '', 'REVERB\nTYPE'));
  reverb.addEventListener('click', () => showDialog('reverb'));
  top.append(reverb);
  const returned = new Knob('REVERB RETURN', 1024, value => volumeText(VOLUME[value]),
    value => mixerControl('input', 0, 'return', VOLUME[value], 'master'),
    state => volumeIndex(state.reverb.master_return));
  returned.node.classList.add('master-return');
  top.append(returned.node); master.append(top);
  const groups = el('div', 'master-controls');
  for (const source of ['input', 'playback']) {
    const group = el('div', 'master-group');
    group.append(toggle('LINK', value => mixerControl(source, 0, 'link', value, 'master'),
      state => state.mixers[bus][`${source}_master`].linked));
    group.append(el('div', 'caption', source === 'input' ? 'INPUT MIXER' : 'WAVE OUT MIXER'));
    const pair = el('div', 'master-faders'); pair.style.display = 'flex';
    for (let channel = 0; channel < 2; channel++) {
      pair.append(fader(value => mixerControl(source, channel, 'volume', value, 'master'),
        state => state.mixers[bus][`${source}_master`].volume[channel], `${source} master ${channel + 1} volume`));
    }
    group.append(pair); groups.append(group);
  }
  master.append(groups); $('#mixer-groups').append(master);
}

function renderFaders(state) {
  const previews = new Map(masterFaderPreview(state, faderPreviews.values()).map(control => [key(control), control.value]));
  for (const {input, output, read, control} of faders) {
    const identifier = key(control(0));
    const value = previews.get(identifier) ?? read(state);
    if (previews.has(identifier) || !pending.has(identifier)) input.value = volumeIndex(value);
    input.title = volumeText(value);
    output.textContent = volumeText(value).replace(/ dB$/, '');
    input.disabled = !connected || busy || input.closest('.unavailable-channel') !== null;
  }
}

function update(result) {
  if (!result.state) return;
  documentState = result;
  setConnected(true);
  busy = localBusy || Boolean(result.busy);
  $('#busy-message').hidden = !busy;
  const state = result.state;
  const rateIndex = parseInt(state.payload_hex.slice(4, 6), 16);
  const rate = [44100, 48000, 96000, 192000][rateIndex];
  const multiple = state.output_routes.some(route => route >= 1 && route <= 3);
  if (rateIndex === 3 || !multiple) bus = 0;
  for (const strip of all('.preamp-strip, .mixer-strip')) {
    strip.classList.toggle('unavailable-channel', rateIndex === 3 && Number(strip.dataset.channel) >= 4);
  }
  for (const knob of knobs) {
    knob.set(knob.read(state));
    const disabled = busy || knob.node.closest('.unavailable-channel') !== null;
    knob.node.tabIndex = disabled ? -1 : 0;
    knob.node.setAttribute('aria-disabled', String(disabled));
    if (knob.output instanceof HTMLInputElement) knob.output.disabled = disabled;
  }
  for (const {button, read, control} of switches) {
    const value = Boolean(read(state));
    button.classList.toggle('lit', value);
    button.setAttribute('aria-pressed', String(value));
    button.disabled = busy || pending.has(key(control(0))) || button.closest('.unavailable-channel') !== null;
  }
  renderFaders(state);
  for (const {canvas, channel} of plots) drawCompressor(canvas, state.preamps[channel].compressor);
  for (const node of all('.mixer-send, .master-return, .master-reverb-type')) node.style.visibility = bus === 0 && rateIndex !== 3 ? 'visible' : 'hidden';
  $('.master-reverb-type').classList.toggle('lit', state.reverb.type !== 0);
  $('.master-reverb-type').setAttribute('aria-pressed', String(state.reverb.type !== 0));
  $('.master-reverb-type').disabled = busy;
  for (const [index, tab] of all('#mixer-tabs > button').entries()) {
    tab.setAttribute('aria-selected', String(index === bus));
    tab.hidden = index !== 0 && (rateIndex === 3 || !multiple);
    tab.disabled = busy || (rateIndex === 3 && index !== 0);
  }
  const multiActive = activeView === 'mixer' && rateIndex !== 3 && multiple;
  $('#header-action').classList.toggle('lit', multiActive);
  $('#header-action').setAttribute('aria-pressed', String(multiActive));
  $('#header-action').disabled = busy || (activeView === 'mixer' && rateIndex === 3);
  $('#header-action').style.visibility = activeView === 'mixer' && rateIndex === 3 ? 'hidden' : 'visible';
  for (const title of all('.group-title[data-source]')) {
    title.textContent = `${title.dataset.source === 'input' ? 'INPUT' : 'WAVE OUT'} MIXER ${'ABCD'[bus]}`;
  }
  $('#mixer-tabs .routing-summary').textContent = `OUTPUT: ${state.output_routes.flatMap((route, pair) => route === bus ? [`${pair * 2 + 1}/${pair * 2 + 2}`] : []).join(' ') || '--'}`;
  const clock = result.status?.clock_source === 'external' ?
    (result.status.clock_active ? `EXTERNAL (${result.status.sample_rate_hz} Hz)` : 'EXTERNAL (Unlock)') : 'INTERNAL';
  $('#device-status').textContent = `OCTA-CAPTURE · ${rate ? rate / 1000 : '--'} kHz · ${clock}`;
  $('#settings-clock').textContent = clock;
  $('#settings-channels').textContent = rateIndex === 3 ? '4 in / 4 out' : '12 in / 10 out';
  $('#settings-capture-note').textContent = rateIndex === 3 ? 'At 192 kHz, inputs and outputs 1–4 are available.' :
    'Capture channels 11/12 carry the stereo return from Mixer A.';
  $('#sample-rate').value = rate;
  $('#sample-rate').disabled = busy || writing;
  $('#settings-device').disabled = !canSelectDevice() || inFlight.has('settings-devices') || !$('#settings-device').value;
  syncDialogs(state, rateIndex);
  panelWarnings.update(rate, result.preferences);
  if (result.meters) updateMeters(result.meters);
}

function updateMeters(meters) {
  meterSnapshot = meters;
  meterReceived = performance.now();
  const rateIndex = parseInt(documentState?.state.payload_hex.slice(4, 6), 16);
  const rate = [44100, 48000, 96000, 192000][rateIndex];
  if (!meters || rate !== meterLastRate) {
    for (const view of meterViews) view.model.reset();
    meterLastRate = rate;
  }
  for (const frame of meters?.history || []) {
    if (frame.id <= meterLastID) continue;
    meterLastID = frame.id;
    if (frame.age_ms >= (frame.source === 'input' ? 1000 : 500) || (frame.rate !== 0 && frame.rate !== rate)) continue;
    for (const view of meterViews) {
      if (view.source === frame.source && view.channel < frame.levels.length) {
        view.model.feed(meterDb(frame.levels[view.channel]));
      }
    }
  }
}

function renderMeters() {
  const now = performance.now();
  const elapsed = Math.floor((now - meterTick) / METER_TICK_MS);
  if (elapsed === 0) return;
  meterTick += elapsed * METER_TICK_MS;
  const ticks = Math.min(elapsed, 250);
  const receivedAge = now - meterReceived;
  const rateIndex = parseInt(documentState?.state.payload_hex.slice(4, 6), 16);
  const rate = [44100, 48000, 96000, 192000][rateIndex];
  const pcm = meterSnapshot?.pcm;
  for (const {node, source, channel, model} of meterViews) {
    const direction = pcm?.[source];
    const available = !node.classList.contains('out') || Boolean(documentState?.state.preamps[channel]?.compressor.enabled);
    const fresh = connected && (source === 'input' ?
      Boolean(meterSnapshot && meterSnapshot.age_ms + receivedAge < 1000) :
      Boolean(pcm?.enabled && direction?.fresh && direction.rate === rate && channel < direction.channels &&
        direction.age_ms + pcm.received_age_ms + receivedAge <= 500));
    if (!available || !fresh || (rateIndex === 3 && channel >= 4)) model.reset();
    else for (let tick = 0; tick < ticks; tick++) model.step();
    const height = node.clientHeight;
    const level = Math.max(0, height - meterY(model.level, height));
    const peak = Math.max(0, height - meterY(model.peak, height));
    node.style.setProperty('--level', `${level}px`);
    node.style.setProperty('--peak', `${Math.max(0, peak - 2)}px`);
    for (const label of node.querySelectorAll('.meter-scale > span')) {
      label.style.top = `${meterY(Number(label.textContent), height) - 4}px`;
    }
    node.classList.toggle('clipped', model.clipHold > 0);
    node.classList.toggle('has-peak', peak > 0);
    node.classList.toggle('stale', !fresh);
    node.classList.toggle('unavailable', !available);
  }
}

function showDialog(name) {
  closeMenus();
  if (name === 'patchbay' && documentState?.state.payload_hex.slice(4, 6) === '03') return;
  const dialog = $(`#${name}`);
  if (!dialog.open) dialog.showModal();
  if (name === 'settings') void settingsDevices();
}

function confirmation(message) {
  $('#confirm-text').textContent = message;
  $('#confirm').showModal();
  return new Promise(resolve => {
    const dialog = $('#confirm');
    const done = value => { dialog.close(); cleanup(); resolve(value); };
    const yes = () => done(true), no = () => done(false), cancel = event => { event.preventDefault(); done(false); };
    const cleanup = () => {
      $('#confirm-yes').removeEventListener('click', yes); $('#confirm-no').removeEventListener('click', no);
      dialog.removeEventListener('cancel', cancel);
    };
    $('#confirm-yes').addEventListener('click', yes); $('#confirm-no').addEventListener('click', no);
    dialog.addEventListener('cancel', cancel);
  });
}

function canOperate() { return connected && !busy && !writing; }
function canSelectDevice() { return !(connected && busy) && !localBusy && !writing && pending.size === 0; }
function setOperationBusy(value) {
  localBusy = value;
  busy = value || Boolean(documentState?.busy);
  $('#busy-message').hidden = !busy;
  $('#sample-rate').disabled = !connected || busy || writing;
  $('#settings-device').disabled = !canSelectDevice() || inFlight.has('settings-devices') || !$('#settings-device').value;
  if (busy) {
    reverbDelay.sync(reverbDelay.value, false, true);
    reverbTime.sync(reverbTime.value, false, true);
  } else if (documentState) {
    syncDialogs(documentState.state, parseInt(documentState.state.payload_hex.slice(4, 6), 16));
  }
}

async function operation(body, message) {
  if (!canOperate()) return;
  if (message && !await confirmation(message)) return;
  if (!canOperate()) return;
  setOperationBusy(true); revision++;
  try { update(await api('/api/action', body)); }
  catch (error) { notify(error.message); }
  finally { setOperationBusy(false); void refresh(); }
}

function syncDialogs(state, rateIndex) {
  const type = state.reverb.type;
  const changingType = pending.has(key({kind: 'reverb', parameter: 'type', bank: 0}));
  if (!changingType) $('#reverb-type').value = type;
  $('#reverb-type').disabled = busy || rateIndex === 3;
  const enabled = connected && !busy && !changingType && rateIndex !== 3 && type !== 0;
  reverbDelay.sync(state.reverb.pre_delay_indices[type], enabled,
    pending.has(key({kind: 'reverb', parameter: 'pre-delay', bank: type})));
  reverbTime.sync(state.reverb.time_indices[type], enabled,
    pending.has(key({kind: 'reverb', parameter: 'time', bank: type})));
  for (const select of all('#patchbay select')) { select.value = state.output_routes[Number(select.dataset.pair)]; select.disabled = rateIndex === 3; }
  syncAutoSense(rateIndex);
}

function syncAutoSense(rateIndex) {
  const job = documentState.auto_sense, active = Boolean(job?.active);
  const messages = {starting: 'Starting Auto-Sens…', running: 'Input the loudest expected signal, then click Finish.',
    finishing: 'Finishing Auto-Sens…', cancelling: 'Cancelling and restoring settings…',
    completed: 'Auto-Sens complete.', cancelled: 'Auto-Sens cancelled. Settings restored.', failed: job?.error};
  $('#auto-status').textContent = messages[job?.state] || 'Select the input channels.';
  $('#auto-start').textContent = active ? 'Finish' : 'Start';
  $('#auto-start').disabled = inFlight.has('auto-sense') || !documentState.capabilities?.auto_sense || writing || localBusy ||
    (active ? job.state !== 'running' : busy);
  $('#auto-cancel').disabled = inFlight.has('auto-sense') || job?.state === 'cancelling';
  for (const check of all('#auto-channels input')) check.disabled = active || (rateIndex === 3 && Number(check.value) >= 4);
  $('#auto-all').disabled = active; $('#auto-none').disabled = active;
}

async function autoSenseRequest(path, body) {
  if (!enter('auto-sense')) return;
  setOperationBusy(true); revision++;
  syncAutoSense(parseInt(documentState.state.payload_hex.slice(4, 6), 16));
  try { update(await api(path, body)); }
  catch (error) { notify(error.message); }
  finally { leave('auto-sense'); setOperationBusy(false); void refresh(); }
}

function cancelAutoSense() {
  if (inFlight.has('auto-sense')) return;
  if (documentState?.auto_sense?.active) {
    void autoSenseRequest('/api/auto-sense/stop', {action: 'cancel'});
  } else $('#auto-sens').close();
}

function closeMenus() {
  popups.close();
}

function openMenu(items, x, y) {
  popups.open(items, x, y);
}

function controlMenu(event, control) {
  event.preventDefault(); if (!connected || busy) return;
  event.currentTarget.focus({preventScroll: true});
  const items = [];
  const set = (label, value) => ({label, run: () => queueChange({...control, value})});
  if (['volume', 'send', 'return'].includes(control.parameter)) {
    for (const [index, label] of VOLUME_LABELS.entries()) items.push(set(label, VOLUME_PRESETS[index]));
  } else if (control.parameter === 'pan') {
    items.push(set('Left', 0), set('Center', 16384), set('Right', 32768));
  }
  const allChannels = value => {
    const count = control.kind === 'preamp' ? 8 : 10;
    const step = ['link', 'mono', 'stereo-link'].includes(control.parameter) ? 2 : 1;
    for (let channel = 0; channel < count; channel += step) {
      queueChange({...control, channel, value: typeof value === 'function' ? value(channel) : value});
    }
  };
  if (control.kind === 'mixer' && ['volume', 'send'].includes(control.parameter)) {
    items.push(null, {label: 'All Ch.', children: VOLUME_LABELS.map((label, index) =>
      ({label, run: () => allChannels(VOLUME_PRESETS[index])}))});
  } else if (control.kind === 'mixer' && control.parameter === 'pan') {
    items.push(null, {label: 'All Ch.', children: [
      {label: 'Left / Right', run: () => allChannels(channel => channel % 2 === 0 ? 0 : 32768)},
      {label: 'Center', run: () => allChannels(16384)},
    ]});
  } else if (control.kind === 'preamp' && control.parameter === 'sensitivity') {
    items.push({label: 'Minimum All Ch.', run: () => allChannels(0)});
  } else if (control.kind === 'preamp' && control.parameter === 'compressor') {
    items.push({label: 'Bypass Off All Ch.', run: () => allChannels(1)},
      {label: 'Bypass On All Ch.', run: () => allChannels(0)});
  } else if (control.kind !== 'master' && ['phantom', 'low-cut', 'phase', 'stereo-link', 'link', 'mono', 'solo', 'mute'].includes(control.parameter)) {
    const prefix = {phantom: 'Phantom', 'low-cut': 'Lo-Cut', phase: 'Phase', 'stereo-link': 'Link',
      link: 'Link', mono: 'Mono', solo: 'Solo', mute: 'Mute'}[control.parameter];
    const upper = ['solo', 'mute'].includes(control.parameter);
    if (control.parameter !== 'phantom') items.push({label: `${prefix} ${upper ? 'ON' : 'On'} All Ch.`, run: () => allChannels(1)});
    items.push({label: `${prefix} ${upper ? 'OFF' : 'Off'} All Ch.`, run: () => allChannels(0)});
  }
  if (items.length) openMenu(items, event.clientX, event.clientY);
}

async function saveSettings() {
  try {
    const data = await api('/api/preset', undefined, true);
    const url = URL.createObjectURL(new Blob([data], {type: 'application/octet-stream'}));
    const link = el('a'); link.href = url; link.download = 'OCTA-CAPTURE.octa'; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) { notify(error.message); }
}

function menu(name, anchor) {
  const highestRate = documentState?.state.payload_hex.slice(4, 6) === '03';
  const items = name === 'driver' ? [
    {label: 'Driver Settings…', disabled: !connected || busy, run: () => showDialog('settings')}, null,
    {label: 'Always on top', disabled: true}, {label: 'Show README', run: () => showDialog('readme')},
    {label: 'About Driver & Control Panel…', run: () => showDialog('about')}, null,
    {label: 'Exit', shortcut: 'Alt+F4', run: async () => {
      stopped = true;
      try { await api('/api/disconnect', {}); } catch { /* Closing also expires the device lease. */ }
      window.close(); setConnected(false, 'The control panel is closed. You can close this browser tab.');
    }},
  ] : [
    {label: 'Save settings…', shortcut: 'Ctrl+S', run: saveSettings},
    {label: 'Load settings…', shortcut: 'Ctrl+O', run: () => $('#load-file').click()}, null,
    {label: 'Open the Patch Bay…', shortcut: 'Ctrl+P', disabled: highestRate, run: () => showDialog('patchbay')}, null,
    {label: 'Directly Output the Inputs', disabled: highestRate, run: () => operation({action: 'direct-out'}, 'This will change the patchbay and direct mixer settings. Do you want to continue?')}, null,
    {label: 'Initialize…', children:
      [['all', 'All'], ['preamps', 'MIC PRE'], ['direct-mix', 'DIRECT MIX'], ['reverb', 'REVERB'], ['patchbay', 'PATCHBAY']].map(([target, label]) =>
        ({label, run: () => operation({action: 'initialize', target}, `OK to initialize ${label} settings?`)}))}, null,
    {label: 'Show the signal flow', run: () => showDialog('diagram')},
  ];
  const rect = anchor.getBoundingClientRect();
  const enabledItems = name === 'device' ? items.map(item => item ? {...item, disabled: !canOperate() || item.disabled} : null) : items;
  openMenu(enabledItems, rect.left, rect.bottom);
  anchor.setAttribute('aria-expanded', 'true');
}

function setupDialogs() {
  for (const [index, name] of REVERB_TYPES.entries()) {
    const option = el('option', '', name); option.value = index; $('#reverb-type').append(option);
  }
  const reverbChange = parameter => value => queueChange({kind: 'reverb', parameter, bank: documentState.state.reverb.type, value});
  reverbDelay = new ReferenceStepper($('#reverb-delay-stepper'), 12, preDelayText, 'ms', reverbChange('pre-delay'));
  reverbTime = new ReferenceStepper($('#reverb-time-stepper'), 49, reverbTimeText, 'seconds', reverbChange('time'));
  for (let pair = 0; pair < 5; pair++) {
    const label = el('label', '', `OUTPUT ${pair * 2 + 1}-${pair * 2 + 2}:`);
    const select = el('select'); select.dataset.pair = pair;
    for (const [index, name] of ROUTES.entries()) { const option = el('option', '', name); option.value = index; select.append(option); }
    select.addEventListener('change', () => queueChange({kind: 'route', pair, selection: Number(select.value)}));
    label.append(select); $('#patchbay-content').append(label);
  }
  for (let channel = 0; channel < 8; channel++) {
    const label = el('label', '', String(channel + 1)); const check = el('input');
    check.type = 'checkbox'; check.value = channel; check.checked = true;
    label.append(check); $('#auto-channels').append(label);
  }
  $('#auto-all').addEventListener('click', () => all('#auto-channels input:not(:disabled)').forEach(input => { input.checked = true; }));
  $('#auto-none').addEventListener('click', () => all('#auto-channels input').forEach(input => { input.checked = false; }));
  $('#auto-start').addEventListener('click', () => {
    if (documentState?.auto_sense?.active) {
      void autoSenseRequest('/api/auto-sense/stop', {action: 'finish'});
      return;
    }
    if (!canOperate()) return;
    const channels = all('#auto-channels input:checked:not(:disabled)').reduce((mask, input) => mask | 1 << Number(input.value), 0);
    if (!channels) { notify('Select at least one input channel.'); return; }
    void autoSenseRequest('/api/auto-sense/start', {channels});
  });
  $('#auto-cancel').addEventListener('click', cancelAutoSense);
  $('#auto-sens').addEventListener('cancel', event => { event.preventDefault(); cancelAutoSense(); });
  $('#reverb-type').addEventListener('change', event => {
    queueChange({kind: 'reverb', parameter: 'type', bank: 0, value: Number(event.target.value)});
    reverbDelay.sync(reverbDelay.value, false, true);
    reverbTime.sync(reverbTime.value, false, true);
  });
  $('#sample-rate').addEventListener('change', async () => {
    const rate = Number($('#sample-rate').value);
    const current = [44100, 48000, 96000, 192000][parseInt(documentState?.state.payload_hex.slice(4, 6), 16)];
    if (!canOperate()) { $('#sample-rate').value = current; return; }
    if (rate !== current) await operation({action: 'sample-rate', rate});
    if (documentState) $('#sample-rate').value = [44100, 48000, 96000, 192000][parseInt(documentState.state.payload_hex.slice(4, 6), 16)];
  });
  $('#settings-device').addEventListener('change', event => {
    void selectDevice(event.target.value).then(() => settingsDevices());
  });
  for (const button of all('[data-close]')) button.addEventListener('click', () => button.closest('dialog').close());
  for (const button of all('[data-action]')) {
    button.addEventListener('click', () => {
      const action = button.dataset.action;
      if (action === 'reverb-default') void operation({action: 'initialize', target: 'reverb'}, 'OK to initialize the reverb settings?');
      else void operation({action: 'routing', preset: action === 'multi-monitor' ? 'multi-monitor' : 'playback'}, 'This will change the patchbay settings. Do you want to continue?');
    });
  }
  $('#load-file').addEventListener('change', async event => {
    const input = event.currentTarget, file = input.files[0];
    if (!file || !enter('file')) return;
    input.disabled = true;
    try {
      if (file.size !== 1639) throw new Error('Choose a valid OCTA-CAPTURE .octa settings file.');
      if (await confirmation('This will change the current settings. Do you want to continue?') && canOperate()) {
        setOperationBusy(true);
        update(await api('/api/preset', new Uint8Array(await file.arrayBuffer()), true));
      }
    } catch (error) { notify(error.message); }
    finally { resetFile(input); leave('file'); setOperationBusy(false); void refresh(); }
  });
}

function resetFile(input) { input.value = ''; input.disabled = false; }

function setupEvents() {
  for (const button of all('[data-menu]')) button.addEventListener('click', event => { event.stopPropagation(); menu(button.dataset.menu, button); });
  document.addEventListener('click', event => { if (!event.target.closest('.menu')) closeMenus(); });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') closeMenus();
    if ((event.ctrlKey || event.metaKey) && ['s', 'o', 'p'].includes(event.key.toLowerCase())) {
      event.preventDefault();
      const command = event.key.toLowerCase();
      if (command === 's') void saveSettings();
      else if (command === 'o') $('#load-file').click();
      else showDialog('patchbay');
    }
  });
  $('#view-switch').addEventListener('click', () => {
    activeView = activeView === 'preamp' ? 'mixer' : 'preamp';
    $('#preamp-view').hidden = activeView !== 'preamp'; $('#mixer-view').hidden = activeView !== 'mixer';
    $('#view-switch').textContent = activeView === 'preamp' ? 'DIRECT MIXER ▾' : 'MIC PRE ▾';
    $('#header-action > span:last-child').textContent = activeView === 'preamp' ? 'AUTO-SENS' : 'MULTIPLE DIRECT MIXER';
    if (documentState) update(documentState);
  });
  $('#header-action').addEventListener('click', () => {
    if (activeView === 'preamp') showDialog('auto-sens');
    else {
      const multiple = documentState?.state.output_routes.some(route => route >= 1 && route <= 3);
      const action = multiple ? {action: 'initialize', target: 'patchbay'} : {action: 'routing', preset: 'multi-monitor'};
      void operation(action, 'This will change the patchbay settings. Do you want to continue?');
    }
  });
  $('#device-status').setAttribute('role', 'button');
  $('#device-status').setAttribute('aria-label', 'Identify OCTA-CAPTURE');
  $('#device-status').title = 'Click to identify. Right-click or press Shift+F10 to select an OCTA-CAPTURE.';
  $('#device-status').tabIndex = 0;
  $('#device-status').addEventListener('click', event => {
    if (connected) queueChange({kind: 'identify'});
    else void deviceMenu(event);
  });
  $('#device-status').addEventListener('contextmenu', event => { void deviceMenu(event); });
  $('#device-status').addEventListener('keydown', event => {
    if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) {
      void deviceMenu(event); return;
    }
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      if (connected) queueChange({kind: 'identify'});
      else void deviceMenu(event);
    }
  });
}

const inFlight = new Set();
function enter(name) {
  if (inFlight.has(name)) return false;
  inFlight.add(name);
  return true;
}
function leave(name) { inFlight.delete(name); }

async function deviceMenu(event) {
  event.preventDefault();
  if (!enter('devices')) return;
  const anchor = $('#device-status');
  anchor.focus({preventScroll: true});
  const rect = anchor.getBoundingClientRect();
  try {
    const result = await api('/api/devices');
    const items = result.devices.map(device => ({label: device.name, checked: device.selected,
      disabled: !canSelectDevice(),
      run: () => { void selectDevice(device.id); }}));
    if (!items.length) items.push({label: 'The device could not be found.', disabled: true});
    openMenu(items, rect.left, rect.bottom);
  } catch (error) { notify(error.message); }
  finally { leave('devices'); }
}

async function selectDevice(id) {
  if (!canSelectDevice()) return;
  setOperationBusy(true); revision++;
  closeMenus();
  try { bus = 0; update(await api('/api/device', {id})); }
  catch (error) { notify(error.message); }
  finally { setOperationBusy(false); void refresh(); }
}

async function settingsDevices() {
  if (!enter('settings-devices')) return;
  const select = $('#settings-device');
  select.disabled = true;
  try {
    const result = await api('/api/devices');
    const options = result.devices.map(device => {
      const option = el('option', '', device.name);
      option.value = device.id; option.selected = device.selected;
      return option;
    });
    select.replaceChildren(...options);
    const selected = result.devices.find(device => device.selected);
    select.value = selected?.id || '';
    select.disabled = !canSelectDevice() || !options.length;
  } catch (error) { notify(error.message); }
  finally { leave('settings-devices'); }
}

async function refresh() {
  if (stopped || !enter('state')) return;
  const startRevision = revision;
  try {
    const result = await api('/api/state');
    if (startRevision === revision && !writing && !pending.size) update(result);
  } catch (error) {
    setConnected(false, error.status === 403 ? 'Open this control panel using its private launch link.' : 'The device could not be found.');
    updateMeters(null);
  } finally { leave('state'); }
}
async function readMeters() {
  if (!connected || stopped || !enter('meters')) return;
  try { updateMeters((await api('/api/meters')).meters); }
  catch { updateMeters(null); }
  finally { leave('meters'); }
}

makePreamps(); makeMixers(); setupDialogs(); setupEvents(); setConnected(false, 'Connecting to OCTA-CAPTURE…');
const panelWarnings = new PanelWarnings(api, notify);
addEventListener('pagehide', () => {
  if (token) {
    void fetch('/api/disconnect', {method: 'POST', keepalive: true,
      headers: {'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json'},
      body: '{}', credentials: 'omit'}).catch(() => {});
  }
});
void refresh();
setInterval(() => { void refresh(); }, 1000);
setInterval(() => { void readMeters(); }, METER_TICK_MS);
setInterval(renderMeters, METER_TICK_MS);
