// SPDX-License-Identifier: GPL-2.0-only
// Real browser input with modeled, deliberately delayed device responses.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {until} from './BrowserClient.mjs';
import {VOLUME, volumeText} from '../gui/web/parameters.js';

const displayCases = JSON.parse(readFileSync(new URL('data/master-display.json', import.meta.url), 'utf8')).cases;

async function checkMasterReadouts(browser, model, settings, pollCount) {
  const page = browser.page;
  const displayed = () => page.locator('.master-value').allTextContents();
  for (const step of settings.readouts.steps) {
    const start = pollCount(), mixer = model.state.mixers[settings.bus];
    mixer.input_master.volume = [...step.values.input];
    mixer.playback_master.volume = [...step.values.playback];
    await until(() => pollCount() > start, 'modeled master readout state polled');
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    if (step.select) {
      const [source, channel] = step.select;
      const node = page.locator(`[aria-label="${source} master ${channel + 1} volume"]`);
      const value = Number(await node.inputValue()), box = await node.boundingBox();
      const before = await displayed();
      await page.mouse.move(box.x + 9, box.y + 72 - value / 1024 * 56);
      await page.mouse.down();
      try { assert.deepEqual(await displayed(), before, 'An unchanged thumb press retains the selected side until release'); }
      finally { await page.mouse.up(); }
    }
    assert.deepEqual(await displayed(), ['input', 'playback'].map(source => volumeText(step.expected[source]).replace(/ dB$/, '')),
      'Two visible master fields match original selection/polling traces with unequal stereo levels');
  }
  const before = await displayed();
  await browser.click('#view-switch'); await browser.click('#view-switch');
  assert.deepEqual(await displayed(), before, 'Switching views preserves the selected master sides');
}

function latch() {
  let release;
  return {released: new Promise(resolve => { release = resolve; }), release: () => release()};
}

async function modeledPair(browser, original, url, settings) {
  const page = browser.page, base = 512, model = structuredClone(original);
  model.state.payload_hex = model.state.payload_hex.slice(0, 4) + '01' + model.state.payload_hex.slice(6);
  model.state.output_routes = [0, 1, 2, 3, 0];
  for (const mixer of model.state.mixers) {
    for (const source of ['input', 'playback']) {
      for (const channel of mixer[source].channels) channel.volume = VOLUME[base];
      for (const pair of mixer[source].pairs) pair.linked = false;
    }
  }
  model.state.mixers[settings.bus][settings.source].pairs[settings.channel >> 1].linked = settings.linked;
  const held = [latch(), latch()], writes = [], work = [], start = browser.controls.length;
  let polls = 0;
  const stateRoute = async route => { await route.fulfill({status: 200, json: model}); polls++; };
  const handle = async route => {
    const control = route.request().postDataJSON();
    if (control.kind === 'mixer' && control.parameter === 'volume') {
      const index = writes.length;
      writes.push(control);
      if (index < held.length) await held[index].released;
      const group = model.state.mixers[control.bus][control.source];
      group.channels[control.channel].volume = control.value;
      if (group.pairs[control.channel >> 1].linked) group.channels[control.channel ^ 1].volume = control.value;
    } else assert.equal(control.kind, 'display', 'Only the modeled bus selection is expected');
    await route.fulfill({status: 200, json: model});
  };
  const controlRoute = route => { const task = handle(route); work.push(task); return task; };
  await page.route('**/api/state', stateRoute);
  await page.route('**/api/control', controlRoute);
  try {
    await browser.call('Page.navigate', {url});
    await until(() => browser.evaluate('document.querySelector("#connection-message")?.hidden === true'), 'modeled channel state');
    await browser.click('#view-switch');
    await browser.click(`#mixer-tabs > button:nth-of-type(${settings.bus + 1})`);
    if (settings.readouts) {
      await checkMasterReadouts(browser, model, settings, () => polls);
      assert.equal(writes.length, 0, 'Thumb selection changes only the readout, never a volume command');
      return;
    }
    const selector = `[aria-label="${settings.source} ${settings.channel + 1} volume"]`;
    const positions = () => page.locator('.mixer-strip .fader input').evaluateAll(nodes => nodes.map(node => Number(node.value)));
    const origin = (settings.source === 'input' ? 0 : 10) + settings.channel;
    const peers = settings.linked ? [origin, origin ^ 1] : [origin];
    const expected = target => Array.from({length: 20}, (_, index) => peers.includes(index) ? target : base);
    await until(async () => (await positions()).every(value => value === base), 'seeded channel positions');
    const masters = await page.locator('.master-faders input').evaluateAll(nodes => nodes.map(node => node.value));
    for (let step = 1; step <= 3; step++) {
      await browser.key(selector, 'ArrowUp');
      await until(() => writes.length === 1, 'first channel write held');
      assert.deepEqual(await positions(), expected(base + step), 'Both linked channel thumbs move before any reply');
    }
    const box = await page.locator(selector).boundingBox();
    const y = index => box.y + 72 - index / 1024 * 56;
    await page.mouse.move(box.x + 9, y(base + 3)); await page.mouse.down();
    try { await page.mouse.move(box.x + 9, y(base + 100), {steps: 4}); }
    finally { await page.mouse.up(); }
    const target = (await positions())[origin];
    assert(target > base + 3 && target < base + 200);
    const checkPreview = async () => {
      assert.deepEqual(await positions(), expected(target), 'Latest channel preview survives delayed state/acknowledgments');
      const texts = await page.locator('.mixer-strip .fader-value').allTextContents();
      if (settings.linked) assert.equal(texts[peers[0]], texts[peers[1]], 'Paired numeric readouts stay synchronized');
      assert.deepEqual(await page.locator('.master-faders input').evaluateAll(nodes => nodes.map(node => node.value)), masters,
        'Channel preview cannot move master faders');
    };
    await new Promise(resolve => setTimeout(resolve, 1100));
    await checkPreview();
    held[0].release(); await until(() => writes.length === 2, 'coalesced channel write held');
    await checkPreview();
    held[1].release();
    await until(() => browser.controls.slice(start).filter(control => control.kind === 'mixer' && control.parameter === 'volume' &&
      control.httpStatus === 200).length >= 2, 'modeled writes acknowledged');
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await checkPreview();
    assert.equal(writes.length, 2, 'Only the initial and coalesced latest command are sent');
    assert(writes.every(control => control.channel === settings.channel && control.source === settings.source && control.bus === settings.bus));
    assert.equal(writes[1].value, VOLUME[target]);
  } finally {
    for (const item of held) item.release();
    let count;
    do {
      count = work.length; await Promise.all(work);
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    } while (work.length !== count);
    // Keep every control request intercepted until the test document is gone.
    await browser.blank();
    await page.unroute('**/api/control', controlRoute);
    await page.unroute('**/api/state', stateRoute);
  }
}

export async function checkChannelFaders(browser, api) {
  if (!browser.page) return;
  const original = await api('/api/state'), url = browser.page.url();
  const preamp = !await browser.evaluate('document.querySelector("#preamp-view").hidden');
  const bus = await browser.evaluate('[...document.querySelectorAll("#mixer-tabs > button")].findIndex(node=>node.getAttribute("aria-selected")==="true")');
  try {
    for (const settings of [
      {bus: 0, source: 'input', channel: 0, linked: true},
      {bus: 3, source: 'input', channel: 9, linked: true},
      {bus: 1, source: 'playback', channel: 5, linked: true},
      {bus: 2, source: 'playback', channel: 6, linked: false},
    ]) await modeledPair(browser, original, url, settings);
    for (const readouts of displayCases) {
      await modeledPair(browser, original, url, {bus: readouts.bus,
        source: 'input', channel: 0, linked: false, readouts});
    }
  } finally {
    const restore = route => route.fulfill({status: 200, json: original});
    await browser.page.route('**/api/control', restore);
    try {
      await browser.call('Page.navigate', {url});
      await until(() => browser.evaluate('document.querySelector("#connection-message")?.hidden === true'), 'real state restored');
      if (!preamp || bus !== 0) await browser.click('#view-switch');
      if (bus !== 0) await browser.click(`#mixer-tabs > button:nth-of-type(${bus + 1})`);
      if (preamp && bus !== 0) await browser.click('#view-switch');
    } finally { await browser.page.unroute('**/api/control', restore); }
  }
  assert.deepEqual((await api('/api/state')).state, original.state, 'Modeled channel responses never change the device');
  console.log('PASS linked/unlinked channel faders: immediate keyboard/drag thumbs and readouts, delayed replies, stale acknowledgments and no redundant peer writes; device responses modeled');
  console.log('PASS two shared master readouts: 32 original unequal-stereo selection/polling traces across all buses; device responses modeled');
}
