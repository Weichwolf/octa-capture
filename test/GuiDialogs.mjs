// SPDX-License-Identifier: GPL-2.0-only
// Trusted caption input; device writes and calibration are not part of this test.
import assert from 'node:assert/strict';
import {until} from './BrowserClient.mjs';
import {checkDialogKeys} from './GuiDialogKeys.mjs';

async function bounds(page, selector) {
  const box = await page.locator(selector).boundingBox();
  assert.ok(box, `Visible dialog: ${selector}`);
  return box;
}

async function drag(page, selector, x, y) {
  const caption = await bounds(page, `${selector} .dialog-title`);
  await page.mouse.move(caption.x + 40, caption.y + caption.height / 2);
  await page.mouse.down();
  await page.mouse.move(x, y, {steps: 6});
  await page.mouse.up();
}

async function oneDialog(browser, id, directory) {
  const page = browser.page, selector = `#${id}`;
  // Open the frame directly to test even the 192 kHz-only warning without a rate change.
  await browser.evaluate(`document.querySelector(${JSON.stringify(selector)}).showModal()`);
  const original = await bounds(page, selector);
  if (id === 'patchbay') await browser.screenshot(`${directory}/dialog-caption.png`);
  const focus = await page.locator(':focus').evaluate(node => node.id || node.outerHTML);
  assert.notEqual(await page.locator(':focus').getAttribute('class'), 'dialog-close', 'Caption X is not the default dialog control');
  const caption = await bounds(page, `${selector} .dialog-title`);
  const viewport = page.viewportSize();
  await drag(page, selector, caption.x + 80, caption.y + caption.height / 2 + 20);
  const moved = await bounds(page, selector);
  assert.ok(Math.abs(moved.x - Math.max(0, Math.min(original.x + 40, viewport.width - original.width))) < 1);
  assert.ok(Math.abs(moved.y - Math.max(0, Math.min(original.y + 20, viewport.height - original.height))) < 1);
  assert.equal(await page.locator(':focus').evaluate(node => node.id || node.outerHTML), focus, 'Moving a caption preserves its control focus');
  await drag(page, selector, 0, 0);
  const corner = await bounds(page, selector);
  assert.ok(Math.abs(corner.x) < 1 && Math.abs(corner.y) < 1, 'Caption cannot be dragged out of the viewport');
  await drag(page, selector, viewport.width - 1, viewport.height - 1);
  const edge = await bounds(page, selector);
  assert.ok(edge.x + edge.width <= viewport.width + 1 && edge.y + edge.height <= viewport.height + 1);
  await page.locator(`${selector} .dialog-close`).click();
  await until(() => browser.evaluate(`!document.querySelector(${JSON.stringify(selector)}).open`), 'caption close');
  await browser.evaluate(`document.querySelector(${JSON.stringify(selector)}).showModal()`);
  const reopened = await bounds(page, selector);
  assert.ok(Math.abs(reopened.x - original.x) < 1 && Math.abs(reopened.y - original.y) < 1, 'Reopened dialog is centered again');
  await page.keyboard.press('Escape');
}

async function resizeAndCancel(browser) {
  const page = browser.page;
  await page.keyboard.press('Control+p');
  await drag(page, '#patchbay', 1099, 649);
  await page.setViewportSize({width: 800, height: 450});
  await until(async () => {
    const box = await bounds(page, '#patchbay');
    return box.x >= 0 && box.y >= 0 && box.x + box.width <= 801 && box.y + box.height <= 451;
  }, 'dragged dialog stays reachable after viewport resize');
  await page.locator('#patchbay .dialog-close').click();
  await page.setViewportSize({width: 1100, height: 650});
  const wasPreamp = await browser.evaluate('!document.querySelector("#preamp-view").hidden');
  if (wasPreamp) await browser.click('#view-switch');
  await browser.click('.master-reverb-type');
  await browser.click('#reverb [data-action="reverb-default"]');
  await until(() => browser.evaluate('document.querySelector("#confirm").open'), 'nested confirmation');
  await page.locator('#confirm .dialog-close').click();
  assert.equal(await browser.evaluate('!document.querySelector("#confirm").open && document.querySelector("#reverb").open'), true,
    'Caption X declines initialization and preserves its parent dialog');
  await page.locator('#reverb .dialog-close').click();
  if (wasPreamp) await browser.click('#view-switch');
}

export async function checkDialogWindows(browser, directory, api) {
  if (!browser.page) return;
  await checkDialogKeys(browser, await api('/api/state'));
  const writes = [];
  const record = request => {
    if (request.method() === 'POST') writes.push(new URL(request.url()).pathname);
  };
  browser.page.on('request', record);
  try {
    for (const id of ['about', 'settings', 'readme', 'diagram', 'patchbay', 'reverb', 'port-warning', 'auto-sens']) {
      await oneDialog(browser, id, directory);
    }
    await resizeAndCancel(browser);
    assert.equal(writes.includes('/api/control') || writes.includes('/api/action') || writes.includes('/api/auto-sense/start'), false,
      'Caption actions and declined confirmations do not change device controls');
  } finally {
    browser.page.off('request', record);
    await browser.page.setViewportSize({width: 1100, height: 650});
  }
  console.log('PASS eight dialog captions: trusted drag, viewport bounds/resize, close, recenter and declined nested confirmation');
}
