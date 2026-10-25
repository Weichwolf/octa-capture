// SPDX-License-Identifier: GPL-2.0-only
// Preference commits use synthetic HTTP replies; no device controls are sent.
import assert from 'node:assert/strict';
import {until} from './BrowserClient.mjs';

const editors = [
  {id: 'about', check: '#show-port-warning', inverse: false},
  {id: 'port-warning', check: '#hide-port-warning', inverse: true},
];
const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
};

async function open(page, editor, enabled) {
  await page.locator(`#${editor.id}`).evaluate(node => node.showModal());
  assert.equal(await page.locator(editor.check).isChecked(), editor.inverse ? !enabled : enabled,
    `Reopened ${editor.id} reflects stored preference ${enabled}`);
}

async function canceledDrafts(page, model, writes) {
  for (const editor of editors) {
    for (const cancel of ['Escape', 'caption']) {
      await open(page, editor, model.preferences.show_port_warning);
      const original = await page.locator(editor.check).isChecked();
      await page.locator(editor.check).click();
      // Consume two actual polls while the draft is open, rather than sleeping.
      for (let count = 0; count < 2; count++) {
        await page.waitForResponse(response => new URL(response.url()).pathname === '/api/state');
      }
      assert.equal(await page.locator(editor.check).isChecked(), !original, 'Polling preserves the uncommitted draft');
      assert.equal(model.preferences.show_port_warning, false);
      assert.deepEqual(writes, [], 'Editing sends no preference writes');
      if (cancel === 'Escape') await page.keyboard.press('Escape');
      else await page.locator(`#${editor.id} .dialog-close`).click();
      await open(page, editor, false);
      await page.keyboard.press('Escape');
    }
  }
}

async function pendingCommit(page, model, editor, writes, gate) {
  const before = model.preferences.show_port_warning, count = writes.length;
  await open(page, editor, before);
  await page.locator(editor.check).click();
  await page.locator(`#${editor.id} [data-close]`).click();
  await until(() => writes.length === count + 1, 'one pending preference write');
  assert.deepEqual(writes.at(-1), {show_port_warning: !before});
  for (const selector of [editor.check, `#${editor.id} [data-close]`, `#${editor.id} .dialog-close`]) {
    assert.equal(await page.locator(selector).isDisabled(), true, 'Pending preference controls disabled');
  }
  await page.keyboard.press('Escape'); await page.keyboard.press('Enter');
  assert.equal(await page.locator(`#${editor.id}`).evaluate(node => node.open), true, 'Save cannot be canceled mid-write');
  assert.equal(writes.length, count + 1, 'No duplicate save');
  assert.equal(model.preferences.show_port_warning, before, 'Storage unchanged before completion');
  gate.resolve();
  await until(async () => !await page.locator(`#${editor.id}`).evaluate(node => node.open), 'successful commit closes dialog');
  assert.equal(model.preferences.show_port_warning, !before);
}

async function rejectedCommit(page, model) {
  const editor = editors[0];
  await open(page, editor, false);
  await page.locator(editor.check).click();
  // Reject one fetch before transport, without manufacturing HTTP/console errors.
  await page.evaluate(() => {
    const original = window.fetch;
    window.fetch = (...args) => {
      if (args[0] === '/api/preferences' && args[1]?.method === 'POST') {
        window.fetch = original;
        return Promise.reject(new Error('Simulated preference storage failure'));
      }
      return original(...args);
    };
  });
  await page.locator('#about [data-close]').click();
  await until(async () => await page.locator('#toast').textContent() === 'Simulated preference storage failure', 'save error reported');
  assert.equal(await page.locator('#about').evaluate(node => node.open), true);
  assert.equal(await page.locator(editor.check).isChecked(), true, 'Failed save preserves the draft');
  assert.equal(await page.locator(editor.check).isEnabled(), true);
  assert.equal(model.preferences.show_port_warning, false);
  await page.locator('#about [data-close]').click();
  await until(async () => !await page.locator('#about').evaluate(node => node.open), 'retry commits preserved draft');
  assert.equal(model.preferences.show_port_warning, true);
}

async function stalePoll(page, model) {
  const release = deferred(), fresh = deferred();
  let first = true;
  const handler = async route => {
    if (first) {
      first = false;
      const json = structuredClone(model);
      await release.promise;
      await route.fulfill({status: 200, json});
    } else { await fresh.promise; await route.fallback(); }
  };
  await page.evaluate(() => {
    const original = window.fetch;
    window.preferencePollConsumed = false;
    window.fetch = async (...args) => {
      if (args[0] !== '/api/state') return original(...args);
      window.fetch = original;
      const response = await original(...args), json = response.json.bind(response);
      response.json = async () => {
        const result = await json();
        // The refresh continuation runs before this timer, including any update.
        setTimeout(() => { window.preferencePollConsumed = true; }, 0);
        return result;
      };
      return response;
    };
  });
  await page.route('**/api/state', handler);
  try {
    await until(() => !first, 'old state reply captured');
    await open(page, editors[0], true);
    await page.locator('#show-port-warning').click();
    await page.locator('#about [data-close]').click();
    await until(async () => !await page.locator('#about').evaluate(node => node.open), 'new preference accepted before stale poll');
    assert.equal(model.preferences.show_port_warning, false);
    release.resolve();
    await until(() => page.evaluate(() => window.preferencePollConsumed), 'stale reply consumed by refresh');
    await open(page, editors[0], false);
    await page.keyboard.press('Escape');
  } finally {
    release.resolve(); fresh.resolve(); await page.unroute('**/api/state', handler);
    await page.evaluate(() => { delete window.preferencePollConsumed; });
  }
}

export async function checkWarningPreferences(browser, model) {
  const page = browser.page, original = {...model.preferences}, writes = [];
  let gate = null;
  const nextGate = () => { gate = deferred(); return gate; };
  const restore = () => { gate?.resolve(); model.preferences = original; };
  const handler = async route => {
    const request = route.request();
    if (request.method() === 'POST') {
      const body = request.postDataJSON();
      writes.push(body);
      if (gate) await gate.promise;
      model.preferences = {...body};
    }
    await route.fulfill({status: 200, json: model.preferences});
  };
  assert.equal(original.show_port_warning, false, 'Synthetic fixture starts with warnings disabled');
  await page.route('**/api/preferences', handler);
  try {
    await canceledDrafts(page, model, writes);
    for (const editor of editors) {
      await pendingCommit(page, model, editor, writes, nextGate());
    }
    await rejectedCommit(page, model);
    await stalePoll(page, model);
    console.log('PASS staged About/port-warning preferences: cancel, polls, pending save, failure/retry and stale state replies');
  } finally {
    restore();
    await page.unroute('**/api/preferences', handler);
  }
}
