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
  // Change only browser responses. No calibration or failed recovery is sent to hardware.
  let phase = 'cancelling';
  const simulated = async route => {
    const response = await route.fetch();
    const original = await response.json();
    const active = phase === 'cancelling';
    await route.fulfill({response, json: {...original, busy: active,
      auto_sense: {state: phase, active, channels: 1, error: active ? null : 'Simulated recovery failure'}}});
  };
  await browser.click('#header-action');
  await browser.page.route('**/api/state', simulated);
  try {
    await until(() => browser.evaluate('document.querySelector("#auto-cancel").disabled'), 'simulated pending cancellation');
    assert.deepEqual(await browser.evaluate('[...document.querySelectorAll("#auto-channels input:checked")].map(node=>Number(node.value))'), [0], 'Active checks are populated from the backend job');
    phase = 'failed';
    await until(() => browser.evaluate('document.querySelector("#auto-status").textContent === "Simulated recovery failure"'), 'simulated failure visible');
    assert.equal(await browser.evaluate('document.querySelector("#auto-sens").open'), true, 'Recovery failure must not close the dialog');
  } finally {
    await browser.page.unroute('**/api/state', simulated);
    await until(() => browser.evaluate('document.querySelector("#auto-status").textContent === "" && !document.querySelector("#auto-cancel").disabled'), 'real idle state restored after response simulation');
    await browser.click('#auto-cancel');
  }
  console.log('PASS browser-only Auto-Sens active-channel adoption and failed-recovery dialog preservation simulation');
}
