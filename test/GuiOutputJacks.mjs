// SPDX-License-Identifier: GPL-2.0-only
// Model device replies while exercising actual browser bus selection and polling.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {until} from './BrowserClient.mjs';

const reference = JSON.parse(readFileSync(new URL('data/output-jacks.json', import.meta.url), 'utf8'));
const states = ['other', 'assigned', 'unavailable'];
const colors = ['rgb(198, 198, 198)', 'rgb(0, 230, 146)', 'rgb(82, 83, 82)'];

async function checkRouting(browser, model, count) {
  const page = browser.page;
  for (const [rate, bus] of [[0, 0], [1, 1], [2, 2], [2, 3], [3, 0]]) {
    for (const offset of [0, 3, 6]) {
      const before = count();
      model.state.payload_hex = model.state.payload_hex.slice(0, 4) + `0${rate}` + model.state.payload_hex.slice(6);
      model.state.output_routes = Array.from({length: 5}, (_, pair) => (pair + offset) % 9);
      await until(() => count() > before, 'modeled routing state polled');
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      await browser.click(`#mixer-tabs > button:nth-of-type(${bus + 1})`);
      const actual = await page.locator('.output-jack').evaluateAll(nodes => nodes.map(node => ({text: node.textContent,
        state: node.dataset.state, color: getComputedStyle(node).color, label: node.getAttribute('aria-label')})));
      assert.equal(actual.length, 5, 'Five dynamic output-jack indicators are present');
      const expected = model.state.output_routes.map((route, pair) => reference.states[rate][bus][pair][route]);
      assert.deepEqual(actual.map(jack => jack.state), expected.map(value => states[value]));
      assert.deepEqual(actual.map(jack => jack.color), expected.map(value => colors[value]));
      assert.deepEqual(actual.map(jack => jack.text), ['1/2', '3/4', '5/6', '7/8', '9/10']);
      for (const [pair, jack] of actual.entries()) {
        assert(jack.label.startsWith(`Output ${jack.text}: `), 'Output labels expose routing independently of color');
        assert.equal(jack.label.includes('unavailable'), expected[pair] === 2);
      }
      assert.equal(await page.locator('.routing-summary').count(), 0, 'Routing is displayed at its original output-jack position');
    }
  }
}

export async function checkOutputJacks(browser, api) {
  if (!browser.page) return;
  const page = browser.page, original = await api('/api/state'), url = page.url();
  const preamp = !await browser.evaluate('document.querySelector("#preamp-view").hidden');
  const bus = await browser.evaluate('[...document.querySelectorAll("#mixer-tabs > button")].findIndex(node=>node.getAttribute("aria-selected")==="true")');
  const model = structuredClone(original);
  let polls = 0;
  model.preferences.show_port_warning = false;
  model.state.output_routes = [0, 1, 2, 3, 0];
  const stateRoute = async route => { await route.fulfill({status: 200, json: model}); polls++; };
  const work = [];
  const controlRoute = route => {
    const task = (async () => {
      assert.equal(route.request().postDataJSON().kind, 'display', 'Only modeled bus selection is allowed');
      await route.fulfill({status: 200, json: model});
    })();
    work.push(task); return task;
  };
  const settled = async () => {
    let previous;
    do {
      previous = work.length; await Promise.all(work);
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    } while (previous !== work.length);
  };
  await page.route('**/api/state', stateRoute);
  await page.route('**/api/control', controlRoute);
  try {
    await browser.call('Page.navigate', {url});
    await until(() => browser.evaluate('document.querySelector("#connection-message")?.hidden === true'), 'modeled routing ready');
    await browser.click('#view-switch');
    await checkRouting(browser, model, () => polls);
  } finally {
    await settled(); await browser.blank();
    await page.unroute('**/api/state', stateRoute);
    try {
      Object.assign(model, original);
      await browser.call('Page.navigate', {url});
      await until(() => browser.evaluate('document.querySelector("#connection-message")?.hidden === true'), 'physical readback restored');
      if (!preamp || bus !== 0) await browser.click('#view-switch');
      if (bus !== 0) await browser.click(`#mixer-tabs > button:nth-of-type(${bus + 1})`);
      if (preamp && bus !== 0) await browser.click('#view-switch');
      await settled();
    } finally { await page.unroute('**/api/control', controlRoute); }
  }
  assert.deepEqual((await api('/api/state')).state, original.state, 'Modeled routing never changes the physical device');
  console.log('PASS output-jack labels, assigned/other/unavailable colors and all-rate/mixer polling; device responses modeled');
}
