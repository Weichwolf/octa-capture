// SPDX-License-Identifier: GPL-2.0-only
// Real Playwright keys; busy/writing/absence replies are explicitly simulated.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import {PlaywrightClient} from './PlaywrightClient.mjs';
import {until} from './BrowserClient.mjs';

const logFile = path.resolve(process.argv[2] || 'build/gui/server.log');
const log = await fs.readFile(logFile, 'utf8');
const launch = log.match(/http:\/\/127\.0\.0\.1:[0-9]+\/#token=[^\s]+/)?.[0];
assert.ok(launch, 'Private server launch link');
const url = new URL(launch), token = new URLSearchParams(url.hash.slice(1)).get('token');
const directory = await fs.mkdtemp(path.join(path.dirname(logFile), 'shortcuts-'));
const browser = await PlaywrightClient.attach();
const page = browser.page;
await browser.downloadTo(directory);
let exports = 0, choosers = 0, mode = 'idle', chooserFile = null;
const chooserWork = [];
page.on('filechooser', chooser => {
  choosers++;
  const file = chooserFile;
  chooserFile = null;
  chooserWork.push(chooser.setFiles(file ? [file] : []));
});
page.on('request', request => {
  if (new URL(request.url()).pathname === '/api/preset') exports++;
});

async function state() {
  const response = await fetch(url.origin + '/api/state', {headers: {Authorization: `Bearer ${token}`}});
  assert.ok(response.ok, 'Real backend state request');
  return response.json();
}

async function dialogs() {
  return browser.evaluate('[...document.querySelectorAll("dialog[open]")].map(node=>node.id)');
}

async function blocked(expectedDialogs, description) {
  const before = {exports, choosers};
  for (const key of ['Control+s', 'Control+o', 'Control+p']) {
    await page.keyboard.press(key);
    await page.waitForTimeout(100);
    assert.deepEqual({exports, choosers}, before, `${description}: ${key} does not export or open files`);
    assert.deepEqual(await dialogs(), expectedDialogs, `${description}: ${key} preserves modal ownership`);
  }
}

async function spaceCommands(original) {
  const handler = route => route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(original)});
  await page.route('**/api/control', handler);
  const count = browser.controls.length;
  try {
    for (const mixer of [false, true]) {
      if (mixer) await browser.click('#view-switch');
      for (const selector of ['#view-switch', '#header-action', '[data-menu="driver"]', '#device-status',
        mixer ? '.mixer-strip .toggle' : '[aria-label="Input 1 LO-CUT"]']) {
        await page.locator(selector).first().focus();
        await page.keyboard.press('Space');
        await page.waitForTimeout(100);
        assert.equal(await browser.evaluate('!document.querySelector("#mixer-view").hidden'), mixer,
          `Space preserves the main view with focus on ${selector}`);
        assert.deepEqual(await dialogs(), [], `Space does not open dialogs from ${selector}`);
        assert.equal(await page.locator('#menus > *').count(), 0, `Space does not open menus from ${selector}`);
        assert.equal(browser.controls.length, count, `Space does not submit a device command from ${selector}`);
      }
    }
    await page.locator('#view-switch').focus();
    await page.keyboard.press('Enter');
    assert.equal(await browser.evaluate('document.querySelector("#mixer-view").hidden'), true,
      'Enter remains available for the HTML view switch');
    await browser.click('[data-menu="driver"]');
    await page.getByRole('menuitem', {name: 'Show README', exact: true}).focus();
    await page.keyboard.press('Space');
    assert.deepEqual(await dialogs(), ['readme'], 'Space still activates a popup menu item');
    await page.locator('#readme [data-close]').focus();
    await page.keyboard.press('Space');
    assert.deepEqual(await dialogs(), [], 'Space still activates a modal dialog button');
  } finally {
    await page.unroute('**/api/control', handler);
  }
}

async function modalCommands(highest) {
  for (const name of ['about', 'readme', 'settings', 'auto-sens', 'diagram', 'reverb', 'port-warning',
    ...highest ? [] : ['patchbay']]) {
    // Set up each existing native HTML modal; the keys themselves use real input.
    await page.locator(`#${name}`).evaluate(dialog => dialog.showModal());
    await blocked([name], name);
    await page.locator(`#${name}`).evaluate(dialog => dialog.close());
  }
  if (!highest) {
    await page.keyboard.press('Control+p');
    await browser.click('[data-action="routing-default"]');
    await until(async () => (await dialogs()).includes('confirm'), 'routing confirmation');
    await blocked(['patchbay', 'confirm'], 'nested confirmation');
    await browser.click('#confirm-no');
    await page.keyboard.press('Escape');
    assert.deepEqual(await dialogs(), []);
  }
}

async function positiveCommands(highest) {
  await browser.click('[data-menu="device"]');
  assert.deepEqual(await browser.evaluate('[...document.querySelectorAll("#menus button")].slice(0,3).map(node=>node.disabled)'),
    [false, false, highest], 'Menu and shortcut eligibility agree');
  await page.keyboard.press('Escape');
  let count = exports;
  await page.keyboard.press('Control+s');
  await until(() => exports === count + 1 && browser.downloads.length > 0, 'keyboard settings export');
  await Promise.all(browser.downloads);
  assert.equal((await fs.stat(path.join(directory, 'OCTA-CAPTURE.octa'))).size, 1639);
  count = choosers;
  await page.keyboard.press('Control+o');
  await until(() => choosers === count + 1, 'keyboard Open chooser');
  await Promise.all(chooserWork);
  const fileRequests = exports;
  chooserFile = path.join(directory, 'OCTA-CAPTURE.octa');
  await page.keyboard.press('Control+o');
  await until(async () => (await dialogs()).includes('confirm'), 'selected-file confirmation');
  await blocked(['confirm'], 'settings file confirmation');
  await browser.click('#confirm-no');
  await until(() => browser.evaluate('!document.querySelector("#load-file").disabled'), 'cancelled file choice');
  assert.equal(exports, fileRequests, 'No preset transfer is submitted after declining the file confirmation');
  await page.keyboard.press('Control+p');
  assert.deepEqual(await dialogs(), highest ? [] : ['patchbay'], '192 kHz Patch Bay guard');
  if (!highest) await page.keyboard.press('Escape');
  const before = {exports, choosers};
  for (const key of ['s', 'o', 'p']) {
    for (const modifier of ['shiftKey', 'altKey']) {
      await page.evaluate(({key, modifier}) => document.dispatchEvent(new KeyboardEvent('keydown',
        {key, ctrlKey: true, [modifier]: true, bubbles: true, cancelable: true})), {key, modifier});
    }
  }
  await page.waitForTimeout(100);
  assert.deepEqual({exports, choosers}, before, 'Extra modifiers do not invoke panel commands');
  assert.deepEqual(await dialogs(), []);
}

async function repeatedSave() {
  let release, entered = false;
  const gate = new Promise(resolve => { release = resolve; });
  const count = exports, downloads = browser.downloads.length, opened = choosers;
  const handler = async route => { entered = true; await gate; await route.continue(); };
  await page.route('**/api/preset', handler);
  try {
    await page.keyboard.down('Control'); await page.keyboard.down('s');
    await until(() => entered, 'pending export');
    await page.keyboard.down('s');
    await page.keyboard.up('s'); await page.keyboard.up('Control');
    await page.keyboard.press('Control+s');
    await page.keyboard.press('Control+o');
    await page.keyboard.press('Control+p');
    await page.waitForTimeout(100);
    assert.equal(exports, count + 1, 'One export while Save is pending');
    assert.equal(choosers, opened, 'No Open chooser while Save is pending');
    assert.deepEqual(await dialogs(), []);
    await browser.click('[data-menu="device"]');
    assert.equal(await browser.evaluate('[...document.querySelectorAll("#menus button")].slice(0,3).every(node=>node.disabled)'),
      true, 'File commands stay disabled in the pending Save menu');
    await page.keyboard.press('Escape');
    release();
    await until(() => browser.downloads.length === downloads + 1, 'single Save download');
    await Promise.all(browser.downloads);
  } finally {
    release();
    await page.keyboard.up('s'); await page.keyboard.up('Control');
    await page.unroute('**/api/preset', handler);
  }
}

async function autorepeatSave() {
  const count = exports, downloads = browser.downloads.length;
  try {
    await page.keyboard.down('Control'); await page.keyboard.down('s');
    await until(() => browser.downloads.length === downloads + 1, 'held-key Save download');
    await Promise.all(browser.downloads);
    await page.keyboard.down('s');
    await page.waitForTimeout(100);
    assert.equal(exports, count + 1, 'A held Save key does not export again after the first request ends');
  } finally {
    await page.keyboard.up('s'); await page.keyboard.up('Control');
  }
}

async function pendingWrite(original) {
  let release, entered = false;
  const gate = new Promise(resolve => { release = resolve; });
  const handler = async route => {
    entered = true;
    await gate;
    await route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(original)});
  };
  await page.route('**/api/control', handler);
  try {
    const current = original.state.preamps[0].sensitivity_index;
    await browser.key('.preamp-strip .sensitivity', current < 100 ? 'ArrowUp' : 'ArrowDown');
    await until(() => entered, 'simulated pending control reply');
    await blocked([], 'pending control reply');
  } finally {
    release();
    await until(() => browser.controls.every(control => control.httpStatus === 200), 'simulated write completion');
    await page.unroute('**/api/control', handler);
  }
}

const stateHandler = async route => {
  if (mode === 'absent') {
    return route.fulfill({status: 503, contentType: 'application/json', body: JSON.stringify({error: 'Simulated absence.'})});
  }
  const response = await route.fetch();
  const value = await response.json();
  if (mode === 'busy') value.busy = true;
  return route.fulfill({response, json: value});
};
try {
  const original = await state();
  const highest = original.state.payload_hex.slice(4, 6) === '03';
  await page.route('**/api/state', stateHandler);
  await browser.call('Page.navigate', {url: launch});
  await until(() => browser.evaluate('document.querySelector("#connection-message")?.hidden'), 'connected shortcut test');
  if (highest && original.preferences.show_port_warning) {
    await until(() => browser.evaluate('document.querySelector("#port-warning").open'), 'initial 192 kHz warning');
  }
  if (await browser.evaluate('document.querySelector("#port-warning").open')) await browser.click('#port-warning [data-close]');
  await spaceCommands(original);
  await modalCommands(highest);
  await positiveCommands(highest);
  await repeatedSave();
  await autorepeatSave();
  await pendingWrite(original);
  mode = 'busy';
  await until(() => browser.evaluate('!document.querySelector("#busy-message").hidden'), 'simulated busy state');
  await blocked([], 'busy state');
  mode = 'absent';
  await until(() => browser.evaluate('!document.querySelector("#connection-message").hidden'), 'simulated missing device');
  await blocked([], 'missing device');
  mode = 'idle';
  await until(() => browser.evaluate('document.querySelector("#connection-message").hidden && document.querySelector("#busy-message").hidden'), 'reconnected shortcut test');
  await Promise.all(chooserWork);
  assert.ok(browser.httpErrors.length > 0, 'Deliberate missing-state replies were observed');
  for (const error of browser.httpErrors) assert.deepEqual(error, {path: '/api/state', status: 503});
  assert.equal(browser.consoleEvents.length, browser.httpErrors.length, 'Exact resource-error correspondence');
  for (const event of browser.consoleEvents) {
    assert.equal(event.path, '/api/state');
    assert.equal(event.text, 'Failed to load resource: the server responded with a status of 503 (Service Unavailable)');
  }
  assert.deepEqual(browser.exceptions, []); assert.deepEqual(browser.securityErrors, []);
  assert.deepEqual(browser.networkErrors, []);
  assert.equal(await browser.evaluate('document.querySelector("#toast").hidden'), true, 'No unexpected device-command error');
  assert.deepEqual((await state()).state, original.state, 'No real device parameter was changed');
  console.log('PASS Playwright main-panel Space, popup/modal activation, Save/Open/Patch Bay keys, modifiers, repeat/pending guards and simulated busy/writing/absence; real raw state preserved.');
} finally {
  await browser.blank();
  await page.unroute('**/api/state', stateHandler);
  await Promise.all(chooserWork);
  await browser.close();
}
