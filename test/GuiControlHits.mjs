// SPDX-License-Identifier: GPL-2.0-only
// Trusted mouse/keyboard events; every parameter response is intercepted offline.
import assert from 'node:assert/strict';
import {until} from './BrowserClient.mjs';

async function view(browser, preamp) {
  if (await browser.evaluate(`document.querySelector('#preamp-view').hidden === ${preamp}`)) await browser.click('#view-switch');
  if (!preamp) await browser.click('#mixer-tabs > button:first-of-type');
}

async function point(page, selector) {
  const box = await page.locator(selector).first().boundingBox();
  assert.ok(box, `Visible pointer region: ${selector}`);
  return {x: box.x + box.width / 2, y: box.y + box.height / 2};
}

async function noLabelDrag(page, selector) {
  const origin = await point(page, `${selector} .knob-label`);
  await page.mouse.move(origin.x, origin.y); await page.mouse.down();
  try {
    assert.equal(await page.locator(selector).first().evaluate(node => node.classList.contains('dragging')), false,
      'A painted label cannot begin a knob drag');
    await page.mouse.move(origin.x + 20, origin.y - 20);
  } finally { await page.mouse.up(); }
}

async function buttonHits(browser, selectors, requests, settled) {
  for (const selector of selectors) {
    const label = browser.page.locator(`${selector} > span:not(.lamp)`).first();
    const count = requests.length;
    await label.click(); await settled();
    assert.equal(requests.length, count, `Label is inactive: ${selector}`);
    const start = await point(browser.page, `${selector} > span:not(.lamp)`);
    const end = await point(browser.page, `${selector} .lamp`);
    await browser.page.mouse.move(start.x, start.y); await browser.page.mouse.down();
    try { await browser.page.mouse.move(end.x, end.y); }
    finally { await browser.page.mouse.up(); }
    await settled();
    assert.equal(requests.length, count, `A press outside the skin cannot activate it on release: ${selector}`);
    await browser.click(selector); await settled();
    assert.equal(requests.length, count + 1, `Skin activates: ${selector}`);
    await browser.page.locator(selector).first().focus();
    await browser.page.keyboard.press('Enter'); await settled();
    assert.equal(requests.length, count + 2, `Keyboard activates: ${selector}`);
  }
}

async function contextRelease(browser, selector, origin, requests) {
  const page = browser.page, empty = () => page.locator('#menus > *').count();
  const count = requests.length;
  await page.locator(selector).first().evaluate(node => {
    node.addEventListener('pointerdown', event => { node.__octaTestPointer = event.pointerId; }, {once: true});
  });
  await page.mouse.move(origin.x, origin.y);
  await page.mouse.down();
  await page.mouse.down({button: 'right'});
  assert.equal(await empty(), 0, 'Right-down cannot open a popup');
  await page.mouse.up({button: 'right'});
  assert.equal(await empty(), 0, 'Right-up is ignored while left is held');
  await page.mouse.up();
  assert.equal(requests.length, count, 'A stationary chord does not submit controls');
  await page.mouse.down({button: 'right'});
  assert.equal(await empty(), 0, 'Popup waits for release');
  await page.mouse.up({button: 'right'});
  await until(async () => await empty() === 1, `${selector} right-release popup`);
  await page.keyboard.press('Escape');
  await page.locator(selector).first().focus();
  await page.keyboard.press('Shift+F10');
  await until(async () => await empty() === 1, `${selector} keyboard context popup`);
  await page.keyboard.press('Escape');
  await page.mouse.move(origin.x, origin.y);
  await page.mouse.down(); await page.mouse.down({button: 'right'});
  await page.mouse.up();
  assert.equal(await page.locator(selector).first().evaluate(node => node.hasPointerCapture(node.__octaTestPointer)), false,
    'Left release ends capture even while right is held');
  await page.mouse.move(origin.x + 3, origin.y - 3);
  await page.mouse.up({button: 'right'});
  await until(async () => await empty() === 1, 'Right release after left release still opens its menu');
  await page.keyboard.press('Escape');
  assert.equal(requests.length, count, 'Movement after left release cannot continue a knob/fader drag');
  await page.mouse.move(origin.x, origin.y);
  await page.mouse.down({button: 'right'}); await page.mouse.down();
  assert.equal(await page.locator(selector).first().evaluate(node => node.hasPointerCapture(node.__octaTestPointer)), true,
    'Left press starts capture while right is already held');
  await page.mouse.up();
  await page.mouse.move(origin.x + 3, origin.y - 3);
  await page.mouse.up({button: 'right'});
  await until(async () => await empty() === 1, 'Right-first chord ends with its release popup');
  await page.keyboard.press('Escape');
  assert.equal(requests.length, count, 'Button-only pointer transitions do not quantize a stored value');
  await page.locator(selector).first().evaluate(node => { delete node.__octaTestPointer; });
}

async function knobsAndContexts(browser, requests, settled) {
  const page = browser.page;
  for (const [preamp, selectors] of [[true, ['[aria-label="Input 1 / SENS"]', '[aria-label="Input 1 / GATE"]']],
    [false, ['[aria-label="input 1 / PAN"]', '[aria-label="Input 1 / REVERB"]', '[aria-label="REVERB RETURN"]']]]) {
    await view(browser, preamp);
    for (const selector of selectors) {
      if (!await page.locator(selector).first().isVisible()) continue;
      const count = requests.length;
      await noLabelDrag(page, selector); await settled();
      assert.equal(requests.length, count, 'Label drag is inactive');
      await page.locator(`${selector} .knob-label`).click({button: 'right'});
      assert.equal(await page.locator('#menus > *').count(), 0, 'Label has no context menu');
    }
  }
  await view(browser, true);
  const knob = '[aria-label="Input 1 / SENS"]';
  await contextRelease(browser, knob, await point(page, `${knob} .knob-dial`), requests);
  const origin = await point(page, `${knob} .knob-dial`), label = await point(page, `${knob} .knob-label`);
  await page.mouse.move(origin.x, origin.y); await page.mouse.down({button: 'right'});
  await page.mouse.move(label.x, label.y); await page.mouse.up({button: 'right'});
  assert.equal(await page.locator('#menus > *').count(), 0, 'Release outside the knob window is ignored');
  await view(browser, false);
  const fader = '[aria-label="playback master 1 volume"]';
  const box = await page.locator(fader).boundingBox(), value = Number(await page.locator(fader).inputValue());
  await contextRelease(browser, fader, {x: box.x + box.width / 2,
    y: box.y + Math.round((1024 - value) * 56 / 1024) + 16}, requests);
}

async function headers(browser) {
  const page = browser.page;
  await view(browser, true);
  await page.locator('#header-action > span:last-child').click();
  assert.equal(await page.locator('dialog[open]').count(), 0, 'Auto-Sens label is inactive');
  await browser.click('#header-action');
  await until(() => page.locator('#auto-sens').evaluate(node => node.open), 'Auto-Sens skin');
  await browser.click('#auto-cancel');
  await view(browser, false);
  if (await page.locator('#header-action').isDisabled()) return;
  await page.locator('#header-action > span:last-child').click();
  assert.equal(await page.locator('dialog[open]').count(), 0, 'Multiple-mixer label is inactive');
  await browser.click('#header-action');
  await until(() => page.locator('#confirm').evaluate(node => node.open), 'Multiple-mixer skin');
  await browser.click('#confirm-no');
  await page.locator('.master-reverb-type > span:not(.lamp)').click();
  assert.equal(await page.locator('#reverb').evaluate(node => node.open), false, 'Reverb label is inactive');
  await browser.click('.master-reverb-type');
  await until(() => page.locator('#reverb').evaluate(node => node.open), 'Reverb skin');
  await browser.click('#reverb [data-close]');
}

async function selection(browser) {
  const page = browser.page;
  await view(browser, false);
  const start = await point(page, '.group-title[data-source="input"]');
  const end = await point(page, '.group-title[data-source="playback"]');
  await page.evaluate(() => getSelection().removeAllRanges());
  await page.mouse.move(start.x, start.y); await page.mouse.down();
  try { await page.mouse.move(end.x, end.y, {steps: 20}); }
  finally { await page.mouse.up(); }
  assert.equal(await page.evaluate(() => getSelection().toString()), '', 'Panel text cannot be selected by dragging');
  await view(browser, true);
  const input = page.locator('.sensitivity input.knob-value').first();
  await input.dblclick();
  await page.keyboard.press('Control+A');
  assert.equal(await input.evaluate(node => !node.readOnly && node.selectionStart === 0 && node.selectionEnd === node.value.length),
    true, 'An editable sensitivity value retains native text selection');
  await page.keyboard.press('Escape');
  assert.equal(await input.evaluate(node => node.readOnly), true, 'Escape cancels the unchanged value editor');
}

async function switchFrames(page) {
  await page.evaluate(async () => {
    const {controlButton} = await import('/knobs.js');
    const node = document.createElement('button');
    node.id = 'switch-frame-check';
    node.style.cssText = 'position:fixed;left:80px;top:90px;z-index:100;width:100px;height:50px';
    node.innerHTML = '<span class="lamp"></span><span>Frame check</span>';
    node.__activations = 0;
    document.body.append(node);
    controlButton(node, '.lamp', () => { node.__activations++; });
  });
  const node = page.locator('#switch-frame-check');
  const frame = async expected => {
    await until(() => node.evaluate((node, expected) => node.dataset.buttonFrame === String(expected), expected),
      `switch frame ${expected}`);
  };
  try {
    for (const className of ['toggle', 'toggle bypass', 'header-action mixer-action']) {
      for (const lit of [false, true]) {
        await node.evaluate((node, {className, lit}) => {
          node.className = className; node.classList.toggle('lit', lit); node.disabled = false;
        }, {className, lit});
        const base = lit ? 2 : 0, before = await node.evaluate(node => node.__activations);
        await frame(base);
        const skin = await point(page, '#switch-frame-check .lamp');
        const label = await point(page, '#switch-frame-check > span:not(.lamp)');
        await page.mouse.move(skin.x, skin.y); await page.mouse.down();
        await frame(base + 1);
        await page.mouse.move(label.x, label.y); await frame(base);
        await page.mouse.move(skin.x, skin.y); await frame(base + 1);
        await page.mouse.up(); await frame(base);
        assert.equal(await node.evaluate(node => node.__activations), before + 1, 'Reentry releases inside exactly once');
        await page.mouse.down(); await frame(base + 1);
        await page.mouse.move(label.x, label.y); await frame(base);
        await page.mouse.up();
        assert.equal(await node.evaluate(node => node.__activations), before + 1, 'Release outside cancels activation');
        await page.mouse.move(skin.x, skin.y); await page.mouse.down(); await frame(base + 1);
        await node.evaluate(node => { node.disabled = true; });
        await frame(base + 4);
        assert.deepEqual(await node.locator('.lamp').evaluate(node => {
          const style = getComputedStyle(node);
          return ['--lamp-light', '--lamp-face'].map(name => style.getPropertyValue(name).trim());
        }), ['#8a8a8a', '#8a8a8a'], 'Every disabled palette uses the original gray face');
        await page.mouse.up();
        assert.equal(await node.evaluate(node => node.__activations), before + 1, 'Disabling during a press cancels activation');
        await node.evaluate(node => { node.disabled = false; }); await frame(base);
        await page.mouse.down(); await frame(base + 1);
        await page.evaluate(() => window.dispatchEvent(new Event('blur'))); await frame(base);
        await page.mouse.up();
        assert.equal(await node.evaluate(node => node.__activations), before + 1, 'Modeled host focus loss cancels activation');
      }
    }
  } finally {
    await page.mouse.up();
    await node.evaluate(node => node.remove());
  }
}

export async function checkControlHits(browser, api) {
  if (!browser.page) return;
  const page = browser.page, original = await api('/api/state'), requests = [], work = [];
  const wasPreamp = !await browser.evaluate('document.querySelector("#preamp-view").hidden');
  const handler = route => {
    requests.push(route.request().postDataJSON());
    const response = route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(original)});
    work.push(response); return response;
  };
  const settled = async () => {
    let previous;
    do {
      previous = work.length; await Promise.all(work);
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    } while (work.length !== previous);
  };
  await page.route('**/api/control', handler);
  try {
    await view(browser, true);
    await buttonHits(browser, ['[aria-label="Input 1 +48"]', '[aria-label="Input 1 Hi-Z"]',
      '[aria-label="Input 1 LO-CUT"]', '[aria-label="Input 1 PHASE"]',
      '[aria-label="Input 1 compressor bypass"]', '[aria-label="Compressor stereo link 1-2"]'], requests, settled);
    await view(browser, false);
    await buttonHits(browser, ['.mixer-strip .toggle', '.mixer-switches .toggle:first-child',
      '.mixer-switches .toggle:last-child', '.master-io-link', '.master-group .toggle'], requests, settled);
    await knobsAndContexts(browser, requests, settled);
    await headers(browser);
    await selection(browser);
    await switchFrames(page);
    await settled();
  } finally {
    await page.mouse.up(); await page.mouse.up({button: 'right'});
    await page.keyboard.press('Escape');
    await view(browser, wasPreamp);
    await page.unroute('**/api/control', handler);
  }
  assert.deepEqual((await api('/api/state')).state, original.state, 'All responses were intercepted; hardware state unchanged');
  console.log('PASS trusted control hits/chords, switch press/reentry/disable frames, keyboard activation and editable values; parameter responses and host blur simulated');
}
