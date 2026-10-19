// SPDX-License-Identifier: GPL-2.0-only
// Fixed original resource orders; calibration/confirmation acceptance is simulated.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {until} from './BrowserClient.mjs';

const fixtures = JSON.parse(await fs.readFile(new URL('./data/dialog-keyboard.json', import.meta.url)));

async function opened(page, id, value) {
  await until(() => page.locator(`#${id}`).evaluate((node, expected) => node.open === expected, value), `${id} modal ${value}`);
}

async function tabOrder(page, id, fixture) {
  if (id === 'auto-sens') await page.locator('#auto-none').evaluate(node => node.click());
  await page.locator(`#${id}`).evaluate(node => node.showModal());
  const enabled = [];
  for (const [, selector] of fixture.controls) {
    const node = page.locator(`#${id} ${selector}`);
    if (await node.isEnabled()) enabled.push(selector);
  }
  for (const key of ['Tab', 'Shift+Tab']) {
    for (let step = 0; step <= enabled.length; step++) {
      const index = ((key === 'Tab' ? step : -step) + enabled.length) % enabled.length;
      await until(() => page.locator(`#${id} ${enabled[index]}`).evaluate(node => node === document.activeElement),
        `${id}: ${key} reference focus ${enabled[index]}`);
      if (step < enabled.length) {
        await page.keyboard.press(key);
      }
    }
  }
  await page.keyboard.press('Escape');
  await opened(page, id, false);
}

async function defaultClose(page, id, selector) {
  await page.locator(`#${id}`).evaluate(node => node.showModal());
  if (selector) await page.locator(`#${id} ${selector}`).focus();
  await page.keyboard.press('Enter');
  await opened(page, id, false);
}

async function openCombo(page) {
  await page.locator('#reverb').evaluate(node => node.showModal());
  await page.locator('#reverb-type').focus();
  await page.keyboard.press('F4');
  await page.keyboard.press('Enter');
  await opened(page, 'reverb', true);
  await page.keyboard.press('Enter');
  await opened(page, 'reverb', false);
}

async function gtkTabCode(page) {
  await page.locator('#about').evaluate(node => node.showModal());
  const button = page.locator('#about [data-close]'), check = page.locator('#show-port-warning');
  await button.focus();
  await button.dispatchEvent('keydown', {key: 'Unidentified', code: 'F2', shiftKey: true});
  assert.equal(await button.evaluate(node => node === document.activeElement), true, 'Other unidentified keys do not navigate');
  await button.dispatchEvent('keydown', {key: 'Unidentified', code: 'Tab', shiftKey: true});
  assert.equal(await check.evaluate(node => node === document.activeElement), true, 'GTK physical reverse Tab wraps');
  await check.dispatchEvent('keydown', {key: 'Unidentified', code: 'Tab'});
  assert.equal(await button.evaluate(node => node === document.activeElement), true, 'GTK physical Tab moves forward');
  await page.keyboard.press('Escape');
  await opened(page, 'about', false);
}

async function confirmKeys(page, original) {
  const actions = [];
  const handler = route => {
    actions.push(route.request().postDataJSON());
    return route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(original)});
  };
  await page.route('**/api/action', handler);
  try {
    await page.locator('#patchbay').evaluate(node => node.showModal());
    await page.locator('#patchbay [data-action=routing-default]').focus();
    await page.keyboard.press('Enter');
    await opened(page, 'confirm', true);
    await until(() => page.locator('#confirm-yes').evaluate(node => node === document.activeElement), 'Original confirmation defaults to OK');
    await page.keyboard.press('Tab');
    assert.equal(await page.locator('#confirm-no').evaluate(node => node === document.activeElement), true);
    await page.keyboard.press('Enter');
    await opened(page, 'confirm', false);
    assert.deepEqual(actions, [], 'Focused Cancel declines the nested action');
    await page.keyboard.press('Enter');
    await opened(page, 'confirm', true);
    await page.keyboard.press('Enter');
    await opened(page, 'confirm', false);
    await until(() => actions.length === 1, 'Simulated default confirmation command');
    assert.deepEqual(actions, [{action: 'routing', preset: 'playback'}]);
    await until(() => page.locator('#busy-message').isHidden(), 'Simulated confirmation completion');
    await page.keyboard.press('Escape');
    await opened(page, 'patchbay', false);
  } finally {
    await page.unroute('**/api/action', handler);
  }
}

async function autoKeys(page, original) {
  let release;
  const gate = new Promise(resolve => { release = resolve; }), requests = [];
  const handler = async route => {
    requests.push(route.request().postDataJSON());
    await gate;
    await route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(original)});
  };
  await page.route('**/api/auto-sense/start', handler);
  try {
    await page.locator('#auto-none').evaluate(node => node.click());
    await page.locator('#auto-sens').evaluate(node => node.showModal());
    const check = page.locator('#auto-channels input[value="0"]');
    await check.focus();
    await page.keyboard.press('Enter');
    assert.equal(requests.length, 0, 'Disabled default Start cannot run or close the dialog');
    await opened(page, 'auto-sens', true);
    await page.keyboard.press('Space');
    await page.keyboard.press('Enter');
    await until(() => requests.length === 1, 'Simulated default Start from checkbox');
    await page.keyboard.press('Enter');
    assert.deepEqual(requests, [{channels: 1}], 'Pending Start cannot be duplicated');
    release();
    await until(() => page.locator('#auto-cancel').isEnabled(), 'Simulated Auto-Sens completion');
    await page.keyboard.press('Escape');
    await opened(page, 'auto-sens', false);
  } finally {
    release();
    await page.unroute('**/api/auto-sense/start', handler);
  }
}

export async function checkDialogKeys(browser, original) {
  const page = browser.page;
  for (const [id, fixture] of Object.entries(fixtures)) await tabOrder(page, id, fixture);
  for (const [id, selector] of [['patchbay', 'select[data-pair="0"]'], ['reverb', '#reverb-type'],
    ['about', '#show-port-warning'], ['port-warning', '#hide-port-warning'], ['auto-sens', null]]) {
    await defaultClose(page, id, selector);
  }
  assert.equal(await page.locator('#reverb .reference-stepper input, #reverb .reference-stepper button')
    .evaluateAll(nodes => nodes.every(node => node.tabIndex === -1)), true, 'Read-only spinners have no original Tab stop');
  await openCombo(page);
  await gtkTabCode(page);
  await confirmKeys(page, original);
  await autoKeys(page, original);
  console.log('PASS five original dialog Tab orders/cycles, default Enter, disabled Start and simulated nested confirmation/calibration');
}
