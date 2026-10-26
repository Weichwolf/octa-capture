// SPDX-License-Identifier: GPL-2.0-only
/* eslint-env node */
// Real browser/HTTP/Application/paired coordinator; only USB and PA replies are modeled.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fixture} from './GuiSyntheticModel.mjs';
import {until} from './BrowserClient.mjs';
import {PlaywrightClient} from './PlaywrightClient.mjs';

const root = path.resolve(new URL('..', import.meta.url).pathname);
const directory = await fs.mkdtemp(path.join(root, 'build/gui/paired-browser-'));
await fs.writeFile(path.join(directory, 'model.json'), JSON.stringify(fixture()));
const server = spawn('/usr/bin/python3', ['-u', 'test/GuiPairServer.py', directory],
  {cwd: root, stdio: ['pipe', 'pipe', 'pipe']});
let output = '', errors = '', sequence = 0, browser;
server.stdout.on('data', data => { output += data; });
server.stderr.on('data', data => { errors += data; });
const actions = [];
let holdBusy = false, delayedBusy = null, freezeStates = false;
const waitingStates = [];
async function control(value) {
  const id = ++sequence;
  server.stdin.write(JSON.stringify({...value, id}) + '\n');
  await until(() => output.includes(`FIXTURE_ACK ${id}\n`), `fixture ${value.action}`);
}
async function stats() {
  await control({action: 'stats'});
  return JSON.parse(await fs.readFile(path.join(directory, 'stats.json'), 'utf8'));
}
async function choices(rates, rate, disabled = false) {
  await until(async () => {
    const actual = await browser.page.locator('[data-rate-select]').evaluateAll(nodes => nodes.map(node => ({
      values: [...node.options].map(option => Number(option.value)), selected: node.value, disabled: node.disabled})));
    return actual.length === 2 && actual.every(node => JSON.stringify(node.values) === JSON.stringify(rates) &&
      node.selected === String(rate) && node.disabled === disabled);
  }, 'both paired selectors match backend capabilities');
}
async function selectUnit(identity) {
  await browser.page.locator('#settings-device').selectOption(identity);
  await until(() => browser.page.locator('#settings-device').isEnabled(), 'unit selection completed');
}
try {
  await until(() => /PRIVATE_FIXTURE http:\/\/127\.0\.0\.1:[0-9]+\/#token=[^\s]+/.test(output), 'private paired HTTP fixture');
  const launch = output.match(/http:\/\/127\.0\.0\.1:[0-9]+\/#token=[^\s]+/)[0];
  const url = new URL(launch), token = new URLSearchParams(url.hash.slice(1)).get('token');
  const post = (endpoint, body, origin = url.origin) => fetch(`${url.origin}${endpoint}`, {method: 'POST',
    headers: {Authorization: `Bearer ${token}`, Origin: origin, 'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  browser = await PlaywrightClient.attach();
  await browser.page.route('**/api/state', async route => {
    const response = await route.fetch();
    const document = await response.json();
    if (holdBusy && document.busy) {
      holdBusy = false; freezeStates = true;
      await new Promise(resolve => { delayedBusy = {request: route.request(), release: resolve}; });
    } else if (freezeStates) {
      await new Promise(resolve => { waitingStates.push(resolve); });
    }
    await route.fulfill({response});
  });
  browser.page.on('request', request => {
    if (new URL(request.url()).pathname === '/api/action') actions.push(request.postDataJSON());
  });
  await browser.call('Page.navigate', {url: launch});
  await choices([44100, 48000, 96000], 48000);
  await browser.click('[data-menu="driver"]');
  await browser.page.getByRole('menuitem', {name: 'Driver Settings…', exact: true}).click();
  await until(() => browser.page.locator('#settings-device').isEnabled(), 'paired device choices');
  assert.deepEqual(await browser.page.locator('#settings-device option').evaluateAll(nodes => nodes.map(node => node.value)),
    ['serial:BASE', 'serial:EXP']);
  assert.match(await browser.page.locator('#settings-pair').textContent(), /apply to both/);
  assert.equal((await post('/api/action', {action: 'sample-rate', rate: 192000})).status, 400);
  assert.equal((await post('/api/device', {id: 'serial:OTHER'})).status, 400);
  assert.equal((await post('/api/action', {action: 'sample-rate', rate: 96000}, 'https://invalid.example')).status, 403);
  assert.equal((await stats()).native_calls.length, 0);
  await browser.page.locator('#sample-rate').evaluate(node => {
    node.add(new Option('Forged 192 kHz', '192000')); node.value = '192000';
    node.dispatchEvent(new Event('change', {bubbles: true}));
  });
  await choices([44100, 48000, 96000], 48000);
  assert.equal(actions.length, 0);

  await selectUnit('serial:BASE');
  await choices([48000], 48000);
  assert.match(await browser.page.locator('#settings-pair').textContent(), /Select the EXP/);
  assert.equal((await post('/api/action', {action: 'sample-rate', rate: 96000})).status, 503);
  assert.equal((await stats()).native_calls.length, 0);
  await selectUnit('serial:EXP');
  await choices([44100, 48000, 96000], 48000);
  await browser.page.locator('#sample-rate').selectOption('96000');
  await choices([44100, 48000, 96000], 96000);
  let state = await stats();
  assert.deepEqual(state.current, {...state.baseline, rate: 96000});
  assert.equal(state.native_calls.length, 1);

  await control({action: 'failure', stage: 'load:BASE'});
  await browser.page.locator('#sample-rate').selectOption('44100');
  await until(async () => /Injected load:BASE/.test(await browser.page.locator('#toast').textContent()), 'paired rollback error shown');
  await choices([44100, 48000, 96000], 96000);
  state = await stats();
  assert.deepEqual(state.current, {...state.baseline, rate: 96000});
  assert.equal(state.native_calls.length, 3, 'Failed server reopen restores the previous paired hardware rate');

  await control({action: 'auto-base', enabled: true});
  await browser.page.locator('#sample-rate').selectOption('44100');
  await until(async () => /Auto-Sens is running on OCTA-CAPTURE \(BASE\)/.test(await browser.page.locator('#toast').textContent()), 'companion calibration blocks the change');
  await choices([44100, 48000, 96000], 96000);
  assert.equal((await stats()).native_calls.length, 3);
  await control({action: 'auto-base', enabled: false});
  await control({action: 'missing-base', enabled: true});
  await choices([], '', true);
  assert.equal((await post('/api/action', {action: 'sample-rate', rate: 48000})).status, 503);
  assert.equal((await stats()).native_calls.length, 3, 'Missing companion cannot fall back to a single-unit change');
  await control({action: 'missing-base', enabled: false});
  await choices([44100, 48000, 96000], 96000);

  await control({action: 'pause'});
  holdBusy = true;
  await browser.page.locator('#sample-rate').selectOption('48000');
  await until(() => fs.access(path.join(directory, 'started')).then(() => true, () => false), 'paired operation pending');
  await until(() => delayedBusy !== null, 'real busy-state reply delayed in transit');
  assert.deepEqual(await browser.page.locator('[data-rate-select]').evaluateAll(nodes => nodes.map(node => node.disabled)), [true, true]);
  assert.equal(await browser.page.locator('#settings-device').isDisabled(), true);
  await control({action: 'release'});
  await choices([44100, 48000, 96000], 48000);
  const finishedBusy = browser.page.waitForEvent('requestfinished', {predicate: request => request === delayedBusy.request});
  delayedBusy.release(); await finishedBusy;
  await browser.page.evaluate(() => new Promise(resolve => setTimeout(resolve, 50)));
  assert.deepEqual(await browser.page.locator('[data-rate-select]').evaluateAll(nodes => nodes.map(node => ({
    value: node.value, disabled: node.disabled}))), [{value: '48000', disabled: false}, {value: '48000', disabled: false}],
  'A late pre-commit busy response must not overwrite the completed paired change');
  freezeStates = false;
  waitingStates.splice(0).forEach(release => release());
  assert.equal(await browser.page.locator('#toast').isHidden(), true,
    'A successful paired change clears the earlier companion-calibration error');
  const closeVisible = await browser.page.locator('#settings footer button').evaluate(node => {
    const rect = node.getBoundingClientRect();
    return rect.top >= 0 && rect.bottom <= innerHeight && rect.left >= 0 && rect.right <= innerWidth;
  });
  assert.equal(closeVisible, true, 'Paired settings Close remains inside the browser viewport');
  await browser.page.screenshot({path: path.join(directory, 'paired-settings.png')});
  assert.deepEqual(browser.exceptions, []);
  assert.deepEqual(browser.securityErrors, []);
  assert.deepEqual(browser.networkErrors, []);
  assert.deepEqual(browser.httpErrors, [{path: '/api/action', status: 503}, {path: '/api/action', status: 503}]);
  assert.equal(browser.consoleEvents.length, 2, 'Only the two deliberate action rejections reach the browser console');

  await control({action: 'pause'});
  await browser.page.locator('#sample-rate').selectOption('96000');
  await until(() => fs.access(path.join(directory, 'started')).then(() => true, () => false), 'operation pending before shutdown');
  await browser.close(); browser = null;
  server.kill('SIGTERM'); await once(server, 'exit');
  assert.equal(server.exitCode, 0);
  await fs.access(path.join(directory, 'cancelled'));
  state = JSON.parse(await fs.readFile(path.join(directory, 'stats.json'), 'utf8'));
  assert.deepEqual(state.current, state.baseline, 'Closing the host cancels pending paired work and restores both units');
  assert.deepEqual(state.modules, ['module-alsa-card', 'module-alsa-card']);
  console.log('PASS Playwright: actual HTTP/application/joint coordinator, base/master choices, forged rates, origin checks, rollback, companion Auto-Sens, disconnect, pending state and shutdown recovery. USB/PA are modeled.');
} finally {
  delayedBusy?.release();
  waitingStates.splice(0).forEach(release => release());
  if (browser) await browser.close();
  if (server.exitCode === null) { server.kill('SIGTERM'); await once(server, 'exit'); }
  assert.equal(errors, '', 'Private paired fixture has no unhandled errors');
}
