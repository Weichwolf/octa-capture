// SPDX-License-Identifier: GPL-2.0-only
// Trusted browser wheel input with independent hardware readback and outer recovery.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {until} from './BrowserClient.mjs';
import {VOLUME, panToWire} from '../gui/web/parameters.js';

function watchControls(page) {
  const work = [];
  const track = request => {
    if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/control') {
      work.push(request.response().then(response => response?.finished()));
    }
  };
  const settled = async () => {
    let previous;
    do {
      previous = work.length;
      await Promise.all(work);
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => resolve())));
    } while (work.length !== previous);
  };
  page.on('request', track);
  return {settled, stop: () => page.off('request', track)};
}

export async function checkWheels(browser, api, restore) {
  const {settled, stop} = watchControls(browser.page);
  const families = [
    ['sensitivity', 100, '[aria-label="Input 1 / SENS"]', {kind: 'preamp', channel: 0, parameter: 'sensitivity'}, s => s.preamps[0].sensitivity_index],
    ['compressor', 50, '[aria-label="Input 1 / GATE"]', {kind: 'preamp', channel: 0, parameter: 'gate'}, s => s.preamps[0].compressor.gate_index],
    ['pan', 200, '[aria-label="input 1 / PAN"]', {kind: 'mixer', source: 'input', bus: 0, channel: 0, parameter: 'pan'}, s => s.mixers[0].input.channels[0].pan],
    ['volume', 1024, '[aria-label="Input 1 / REVERB"]', {kind: 'mixer', source: 'input', bus: 0, channel: 0, parameter: 'send'}, s => s.reverb.input_send[0]],
    ['volume', 1024, '[aria-label="REVERB RETURN"]', {kind: 'master', source: 'input', bus: 0, channel: 0, parameter: 'return'}, s => s.reverb.master_return],
  ];
  for (const kind of ['mixer', 'master']) {
    for (const source of ['input', 'playback']) {
      families.push(['fader', 1024,
        `[aria-label="${source}${kind === 'master' ? ' master' : ''} 1 volume"]`,
        {kind, source, bus: 0, channel: 0, parameter: 'volume'}, s => kind === 'master' ?
          s.mixers[0][`${source}_master`].volume[0] : s.mixers[0][source].channels[0].volume]);
    }
  }
  let count = 0;
  try {
    for (const [kind, maximum, selector, control, read] of families) {
      const preamp = control.kind === 'preamp';
      if (await browser.evaluate(`document.querySelector('#preamp-view').hidden === ${preamp}`)) await browser.click('#view-switch');
      const input = browser.page.locator(selector);
      const displayed = () => input.evaluate(node => Number(node.matches('input') ? node.value : node.getAttribute('aria-valuenow')));
      const wire = value => ['volume', 'fader'].includes(kind) ? VOLUME[value] : kind === 'pan' ? panToWire(value) : value;
      const seed = async value => {
        await settled();
        await api('/api/control', {...control, value: wire(value)});
        await until(async () => await displayed() === value, `seeded ${selector} wheel value`);
        await browser.ready(selector);
        await input.hover();
      };
      const wheel = async (delta, expected) => {
        await browser.page.mouse.wheel(0, delta);
        await until(async () => await displayed() === expected, `${selector} wheel position`);
        assert.equal(await input.evaluate(node => document.activeElement === node), true, 'Wheel focuses the hovered control');
        await settled();
        await until(async () => read((await api('/api/state')).state) === wire(expected), `${selector} wheel wire value`);
        count++;
      };
      for (const flags of [0, 4, 8, 12]) {
        await seed(maximum / 2);
        if (flags & 4) await browser.page.keyboard.down('Shift');
        if (flags & 8) await browser.page.keyboard.down('Control');
        try { await wheel(-120, maximum / 2 + 1); await wheel(120, maximum / 2); }
        finally {
          if (flags & 4) await browser.page.keyboard.up('Shift');
          if (flags & 8) await browser.page.keyboard.up('Control');
        }
      }
      for (const [value, delta] of [[0, 120], [maximum, -120]]) {
        await seed(value);
        const requests = browser.controls.length;
        await wheel(delta, value);
        assert.equal(browser.controls.length, requests, 'Endpoint wheel sends no redundant command');
      }
      await seed(maximum / 2);
      await browser.page.mouse.wheel(120, 0);
      await browser.page.waitForTimeout(100);
      assert.equal(await displayed(), maximum / 2, 'Horizontal wheel leaves vertical controls unchanged');
      console.log(`PASS ${selector} native wheel: modifiers, focus, bounds, horizontal guard and wire readback`);
    }
    const target = browser.page.locator('[aria-label="playback master 1 volume"]');
    const value = await target.inputValue(), requests = browser.controls.length;
    await browser.click('[data-menu="driver"]');
    await target.hover(); await browser.page.mouse.wheel(0, -120);
    await browser.page.waitForTimeout(100);
    assert.equal(await target.inputValue(), value, 'Popup retains wheel ownership');
    assert.equal(browser.controls.length, requests, 'Popup blocks background wheel commands');
    await browser.key('#menus button', 'Escape');
    assert.equal(await browser.evaluate('window.visualViewport.scale'), 1, 'Ctrl-wheel does not zoom the panel');
  } finally { stop(); await restore(); }
  console.log(`PASS ${count} actual wheel events, hardware readback and complete raw recovery`);
}

export async function checkWheelDrags(browser, api, restore) {
  const traces = JSON.parse(readFileSync(new URL('data/wheel-drag.json', import.meta.url), 'utf8'));
  const {settled, stop} = watchControls(browser.page);
  let count = 0;
  try {
    for (const [kind, maximum, selector, control, read] of [
      ['compressor', 50, '[aria-label="Input 1 / GATE"]', {kind: 'preamp', channel: 0, parameter: 'gate'}, s => s.preamps[0].compressor.gate_index],
      ['fader', 1024, '[aria-label="input 1 volume"]', {kind: 'mixer', source: 'input', bus: 0, channel: 0, parameter: 'volume'}, s => s.mixers[0].input.channels[0].volume]
    ]) {
      const preamp = kind === 'compressor';
      if (await browser.evaluate(`document.querySelector('#preamp-view').hidden === ${preamp}`)) await browser.click('#view-switch');
      const trace = traces.find(test => test.kind === kind && test.maximum === maximum && test.initial === maximum / 2);
      await settled();
      await api('/api/control', {...control, value: kind === 'fader' ? VOLUME[trace.initial] : trace.initial});
      const input = browser.page.locator(selector);
      const value = () => input.evaluate(node => Number(node.matches('input') ? node.value : node.getAttribute('aria-valuenow')));
      await until(async () => await value() === trace.initial, 'interleaved trace seed');
      await browser.ready(selector);
      const box = await browser.page.locator(kind === 'fader' ? selector : `${selector} .knob-dial`).boundingBox();
      const x = box.x + box.width / 2, y = box.y + box.height / 2;
      const held = {flags: 0};
      const modifiers = async next => {
        const changed = held.flags ^ next;
        held.flags = next;
        for (const [mask, key] of [[4, 'Shift'], [8, 'Control']]) {
          if (changed & mask) await browser.page.keyboard[next & mask ? 'down' : 'up'](key);
        }
      };
      await browser.page.mouse.move(x, y); await browser.page.mouse.down();
      try {
        for (const [index, [event, dx, dy, mask]] of trace.events.entries()) {
          await modifiers(mask);
          if (event === 'wheel') await browser.page.mouse.wheel(0, -dx);
          else await browser.page.mouse.move(x + (kind === 'fader' ? 0 : dx), y + dy);
          const expected = trace.expected[index];
          await until(async () => await value() === expected[3], 'interleaved pointer position');
          await settled();
          await until(async () => read((await api('/api/state')).state) === expected[4], 'interleaved pointer wire value');
          count++;
        }
      } finally { await browser.page.mouse.up(); await modifiers(0); }
    }
  } finally { stop(); await restore(); }
  console.log(`PASS ${count} interleaved wheel/drag events with hardware readback and raw recovery`);
}
