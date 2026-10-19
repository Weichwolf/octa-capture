// SPDX-License-Identifier: GPL-2.0-only
// Trusted keyboard/pointer input; initialization confirmations are declined.
import assert from 'node:assert/strict';
import {until} from './BrowserClient.mjs';

const focus = browser => browser.evaluate(`document.activeElement?.dataset.menu || document.activeElement?.textContent`);
const expanded = browser => browser.evaluate(`document.querySelector('[data-menu][aria-expanded="true"]')?.dataset.menu || null`);
const depth = browser => browser.evaluate(`document.querySelectorAll('#menus .menu').length`);

async function arrows(browser) {
  const page = browser.page;
  await page.locator('#view-switch').focus();
  await page.keyboard.press('F10');
  assert.equal(await focus(browser), 'driver');
  assert.equal(await depth(browser), 0, 'F10 selects the menu bar without opening a popup');
  await page.keyboard.press('ArrowRight');
  assert.equal(await focus(browser), 'device');
  await page.keyboard.press('ArrowDown');
  assert.equal(await expanded(browser), 'device');
  await page.keyboard.press('ArrowLeft');
  assert.equal(await expanded(browser), 'driver', 'Left changes the root menu');
  await page.keyboard.press('ArrowRight');
  assert.equal(await expanded(browser), 'device', 'Right changes the root menu');
  await page.keyboard.press('Escape');
  assert.equal(await focus(browser), 'device', 'Popup Escape returns to its current menu button');
  await page.keyboard.press('Escape');
  assert.equal(await browser.evaluate('document.activeElement.id'), 'view-switch');
  await page.keyboard.press('F10'); await page.keyboard.press('F10');
  assert.equal(await browser.evaluate('document.activeElement.id'), 'view-switch', 'F10 restores the earlier control');
  await browser.click('[data-menu="driver"]');
  await page.locator('[data-menu="device"]').hover();
  assert.equal(await expanded(browser), 'device', 'Pointer movement changes an open root menu');
  await page.locator('[data-menu="driver"]').hover();
  assert.equal(await expanded(browser), 'driver');
  await browser.click('[data-menu="driver"]');
  assert.equal(await depth(browser), 0, 'A second click closes the menu');
  await page.locator('[data-menu="device"]').hover();
  assert.equal(await depth(browser), 0, 'Hover alone does not start menu interaction');
  await browser.evaluate('document.activeElement.blur()');
  await page.keyboard.press('F10'); await page.keyboard.press('F10');
  assert.equal(await browser.evaluate('document.activeElement === document.body'), true,
    'Menu focus can return to the document when no earlier control had focus');
}

async function mnemonics(browser, highest) {
  const page = browser.page;
  await page.keyboard.press('Alt+v');
  assert.equal(await expanded(browser), 'device');
  await page.keyboard.press('i');
  assert.equal(await depth(browser), 2, 'Initialize mnemonic opens its submenu');
  assert.equal(await focus(browser), 'All');
  await page.keyboard.press('ArrowLeft');
  assert.equal(await depth(browser), 1, 'Left closes a submenu before changing the root');
  await page.keyboard.press('ArrowRight');
  assert.equal(await depth(browser), 2);
  await page.keyboard.press('Escape');
  const downloads = browser.downloads.length;
  await page.keyboard.press('s');
  await until(() => browser.downloads.length === downloads + 1, 'First matching S mnemonic exports settings');
  await Promise.all(browser.downloads);
  await until(() => browser.evaluate('document.querySelector("#busy-message").hidden'), 'Menu export completed');
  await page.keyboard.press('Alt+v'); await page.keyboard.press('End');
  await page.keyboard.press('Enter');
  assert.equal(await browser.evaluate('document.querySelector("#diagram").open'), true);
  for (const key of ['Alt+d', 'Alt+v', 'F10']) {
    await page.keyboard.press(key);
    assert.equal(await depth(browser), 0, 'Modal dialogs retain keyboard ownership');
    assert.equal(await browser.evaluate('document.querySelector("#diagram").open'), true);
  }
  await page.keyboard.press('Escape');
  await page.locator('#diagram').waitFor({state: 'hidden'});
  for (const [key, id] of [['a', 'about'], ['s', 'readme'], ['d', 'settings']]) {
    await page.keyboard.press('Alt+d'); await page.keyboard.press(key);
    try { await page.locator(`#${id}`).waitFor({state: 'visible'}); }
    catch (error) {
      console.error(await browser.evaluate('({focus:document.activeElement.outerHTML, menus:document.querySelector("#menus").innerHTML, busy:!document.querySelector("#busy-message").hidden, connected:document.querySelector("#connection-message").hidden})'));
      throw error;
    }
    assert.equal(await browser.evaluate(`document.querySelector('#${id}').open`), true, `Driver mnemonic ${key}`);
    await page.keyboard.press('Escape');
    await page.locator(`#${id}`).waitFor({state: 'hidden'});
  }
  await page.keyboard.press('Alt+v'); await page.keyboard.press('p');
  assert.equal(await browser.evaluate('document.querySelector("#patchbay").open'), !highest,
    'Disabled Patch Bay mnemonic respects the current rate');
  await page.keyboard.press('Escape');
  if (highest) await page.keyboard.press('Escape');
  for (const [key, name] of [['a', 'All'], ['m', 'MIC PRE'], ['d', 'DIRECT MIX'], ['r', 'REVERB'], ['p', 'PATCHBAY']]) {
    await page.keyboard.press('Alt+v'); await page.keyboard.press('i'); await page.keyboard.press(key);
    assert.equal(await browser.evaluate('document.querySelector("#confirm").open'), true);
    assert.equal(await browser.evaluate('document.querySelector("#confirm").textContent.includes(' + JSON.stringify(name) + ')'), true);
    await browser.click('#confirm-no');
  }
}

export async function checkMenuBar(browser, highest) {
  const controls = browser.controls.length;
  await arrows(browser);
  await mnemonics(browser, highest);
  assert.equal(browser.controls.length, controls, 'Menu checks submit no device changes');
  console.log('PASS browser menu bar: F10/Alt+D/Alt+V, root/submenu arrows, pointer switching, first-match mnemonics, focus recovery, modal ownership and declined initialization.');
}

export async function checkUnavailableMenus(browser, absent) {
  const page = browser.page;
  await page.keyboard.press('Alt+v');
  assert.equal(await expanded(browser), absent ? null : 'device', 'Missing devices have no Device menu');
  await page.keyboard.press('i');
  assert.equal(await depth(browser), absent ? 0 : 1, 'Unavailable initialization stays blocked');
  if (!absent) {
    await page.keyboard.press('ArrowLeft');
    assert.equal(await expanded(browser), 'driver', 'Root navigation works when every Device item is disabled');
  }
  await page.keyboard.press('Escape');
  await page.keyboard.press('Alt+d');
  await page.keyboard.press('d');
  assert.equal(await browser.evaluate('document.querySelector("#settings").open'), false,
    'Unavailable driver settings cannot be opened through a mnemonic');
  await page.keyboard.press('Escape');
  await page.keyboard.press('Escape');
}
