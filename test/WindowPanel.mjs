// SPDX-License-Identifier: GPL-2.0-only
// Runs in the real WebKit panel, without any connected device.
export async function checkWindow(supported, initialAbove) {
  const bridge = window.webkit.messageHandlers.octaWindow;
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  const until = async predicate => {
    const deadline = Date.now() + 5000;
    while (Date.now() < deadline) {
      if (await predicate()) return;
      await pause(20);
    }
    throw new Error('Timed out waiting for confirmed window state.');
  };
  const check = (condition, message) => { if (!condition) throw new Error(message); };
  await until(() => document.querySelector('[data-menu=driver]'));
  await until(async () => (await bridge.postMessage({action: 'state'})).above === initialAbove);
  const open = async expected => {
    document.querySelector('[data-menu=driver]').click();
    await until(() => document.querySelector('#menus [role=menuitemcheckbox]'));
    const button = document.querySelector('#menus [role=menuitemcheckbox]');
    if (supported) await until(() => !button.disabled);
    else check(button.disabled, 'Unsupported desktop must disable Always on top.');
    check(button.getAttribute('aria-checked') === String(expected), 'Menu must show actual WM state.');
    return button;
  };
  for (const request of [null, [], 'state', {}, {action: 'state', xid: 1},
    {action: 'set', value: 1}, {action: 'set', value: 'true'},
    {action: 'set', value: true, xid: 1}, {action: 'shell', value: 'true'}, 'x'.repeat(256)]) {
    let rejected = false;
    try { await bridge.postMessage(request); } catch { rejected = true; }
    check(rejected, `Invalid bridge request was accepted: ${JSON.stringify(request)}`);
  }
  const before = await bridge.postMessage({action: 'state'});
  check(before.supported === supported && before.above === initialAbove, 'Invalid requests changed native state.');
  if (!supported) {
    await open(false);
    let rejected = false;
    try { await bridge.postMessage({action: 'set', value: true}); } catch { rejected = true; }
    check(rejected, 'Unsupported WM must reject changes.');
    return {supported: false, above: false};
  }
  let current = initialAbove;
  for (const desired of [!initialAbove, initialAbove, true]) {
    if (current === desired) continue;
    const button = await open(current);
    button.click();
    await until(async () => (await bridge.postMessage({action: 'state'})).above === desired);
    await pause(40);
    current = desired;
  }
  await open(true);
  return {supported: true, above: true};
}
