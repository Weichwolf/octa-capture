// SPDX-License-Identifier: GPL-2.0-only
// Selection-only regression checks; calibration and recovery are checked separately.
import assert from 'node:assert/strict';
import {until} from './BrowserClient.mjs';

export async function checkAutoSenseSelection(browser) {
  assert.equal(await browser.evaluate('document.querySelector("#auto-sens").open'), true);
  assert.equal(await browser.evaluate('document.querySelectorAll("#auto-channels input:checked").length'), 0, 'Idle dialog starts without selected channels');
  assert.equal(await browser.evaluate('document.querySelector("#auto-start").disabled'), true, 'Start requires at least one channel');
  await browser.click('#auto-channels input[value="0"]');
  assert.equal(await browser.evaluate('document.querySelector("#auto-start").disabled'), false);
  await browser.click('#auto-channels input[value="0"]');
  assert.equal(await browser.evaluate('document.querySelector("#auto-start").disabled'), true, 'Removing the last channel disables Start immediately');
  await browser.click('#auto-all');
  assert.equal(await browser.evaluate(`[...document.querySelectorAll('#auto-channels input')].every(node=>node.checked === !node.disabled)`), true, 'Select All includes exactly the available channels');
  assert.equal(await browser.evaluate('document.querySelector("#auto-start").disabled'), false);
  await browser.click('#auto-none');
  assert.equal(await browser.evaluate('document.querySelectorAll("#auto-channels input:checked").length'), 0);
  assert.equal(await browser.evaluate('document.querySelector("#auto-start").disabled'), true);
  await browser.click('#auto-channels input[value="0"]');
  await browser.click('#auto-cancel');
  await until(() => browser.evaluate('!document.querySelector("#auto-sens").open'), 'idle Auto-Sens cancellation');
  await browser.click('#header-action');
  assert.equal(await browser.evaluate('document.querySelectorAll("#auto-channels input:checked").length'), 0, 'Reopening the idle dialog discards the previous selection');
  assert.equal(await browser.evaluate('document.querySelector("#auto-start").disabled'), true);
  console.log('PASS Auto-Sens empty-selection guard, channel changes, Select/Clear All and idle dialog reset');
}

export async function checkAutoSenseFailure(browser) {
  if (!browser.page) return;
  const wasPreamp = await browser.evaluate('!document.querySelector("#preamp-view").hidden');
  if (!wasPreamp) await browser.click('#view-switch');
  // Change only browser responses. No calibration or failed recovery is sent to hardware.
  let phase = 'external';
  let realState;
  const stopped = [];
  const simulated = async route => {
    const response = await route.fetch();
    const original = await response.json();
    realState = original;
    if (phase === 'external') {
      await route.fulfill({response, json: {...original, busy: true,
        auto_sense: {state: 'running', active: true, channels: 1, origin: 'device', error: null},
        auto_sense_activity: {channels: 1, active: true, observed_age_ms: 0}}});
      return;
    }
    const active = phase === 'cancelling';
    await route.fulfill({response, json: {...original, busy: true,
      auto_sense: {state: phase, active, channels: 1, origin: 'device', error: active ? null : 'Simulated recovery failure'}}});
  };
  const stop = async route => {
    // Fulfill locally: sending this request to the backend would touch hardware.
    stopped.push(route.request().postDataJSON());
    phase = 'cancelling';
    await route.fulfill({json: {...realState, busy: true,
      auto_sense: {state: phase, active: true, channels: 1, origin: 'device', error: null}}});
  };
  await browser.click('#header-action');
  await browser.page.route('**/api/state', simulated);
  await browser.page.route('**/api/auto-sense/stop', stop);
  try {
    await until(() => browser.evaluate('document.querySelector("#auto-start").textContent === "Finish" && !document.querySelector("#auto-start").disabled'), 'observed activity can be finished');
    assert.equal(await browser.evaluate('!document.querySelector("#header-action").disabled && document.querySelector("#footer-rate").disabled && document.querySelector("#auto-all").disabled && document.querySelector("#auto-none").disabled'), true, 'External activity permits its dialog and blocks competing operations');
    await browser.click('#auto-sens .dialog-close');
    await until(() => browser.evaluate('document.querySelector("#auto-cancel").disabled'), 'simulated pending cancellation');
    assert.deepEqual(stopped, [{action: 'cancel'}], 'Caption X requests the same verified Cancel operation');
    assert.equal(await browser.evaluate('document.querySelector("#auto-sens").open && document.querySelector("#auto-sens .dialog-close").disabled'), true,
      'Pending cancellation keeps the dialog open and disables its caption close');
    await browser.page.keyboard.press('Escape');
    assert.deepEqual(stopped, [{action: 'cancel'}], 'Escape cannot duplicate a pending cancellation');
    assert.deepEqual(await browser.evaluate('[...document.querySelectorAll("#auto-channels input:checked")].map(node=>Number(node.value))'), [0], 'Active checks are populated from the backend job');
    phase = 'failed';
    await until(() => browser.evaluate('document.querySelector("#auto-status").textContent === "Simulated recovery failure"'), 'simulated failure visible');
    assert.equal(await browser.evaluate('document.querySelector("#auto-sens").open'), true, 'Recovery failure must not close the dialog');
    assert.equal(await browser.evaluate('document.querySelector("#auto-start").disabled && document.querySelector("#footer-rate").disabled && document.querySelector("#auto-cancel").textContent === "Close"'), true, 'Unverified completion remains blocked');
  } finally {
    await browser.page.unroute('**/api/auto-sense/stop', stop);
    await browser.page.unroute('**/api/state', simulated);
    await until(() => browser.evaluate('document.querySelector("#auto-status").textContent === "" && !document.querySelector("#auto-cancel").disabled'), 'real idle state restored after response simulation');
    await browser.click('#auto-cancel');
    if (!wasPreamp) await browser.click('#view-switch');
  }
  console.log('PASS browser-only external activity, caption cancellation, pending/Escape guards and failed-recovery dialog preservation simulation');
}
