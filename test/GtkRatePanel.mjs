// SPDX-License-Identifier: GPL-2.0-only
// Real WebKit rendering with synthetic fetch replies; no device operation is forwarded.
export async function checkRates(model, reference, checkGeometry, dialogReference, checkDialogs) {
  const check = (condition, message) => { if (!condition) throw new Error(message); };
  const until = async predicate => {
    const deadline = Date.now() + 5000;
    while (Date.now() < deadline) {
      if (predicate()) return;
      await new Promise(resolve => setTimeout(resolve, 25));
    }
    throw new Error('Timed out waiting for synthetic WebKit state.');
  };
  const unexpected = [];
  window.fetch = async (url, options = {}) => {
    const endpoint = new URL(url, location.href).pathname;
    let json;
    if (options.method && options.method !== 'GET') unexpected.push(`${options.method} ${endpoint}`);
    if (endpoint === '/api/state') json = model;
    else if (endpoint === '/api/meters') json = {meters: null};
    else if (endpoint === '/api/devices') json = {devices: []};
    else if (endpoint === '/api/preferences') json = model.preferences;
    else { unexpected.push(endpoint); throw new Error('Unexpected offline WebKit request.'); }
    return new Response(JSON.stringify(json), {headers: {'Content-Type': 'application/json'}});
  };
  model.state.preamps.forEach(preamp => { preamp.sensitivity_index = 50; });
  model.state.mixers[0].input.channels[4].volume = 23;
  await until(() => document.querySelector('.sensitivity input')?.value === '25.0');
  const geometry = await checkGeometry(reference);
  check(geometry.checked === 302 && geometry.hidden === 0, 'Original control rectangles in both views');
  const dialogs = await checkDialogs(dialogReference);
  check(dialogs.dialogs === 5 && dialogs.checked === 48, 'Original dialog client/control rectangles');
  check(/^Version [0-9]+\.[0-9]+\.[0-9]+$/.test(document.querySelector('.about-version').textContent), 'Actual project version in About');
  const originalDevice = model.device;
  for (const device of [
    {id: 'serial:EXP', name: 'OCTA-CAPTURE EXP (EXP)', model: 'OCTA-CAPTURE EXP', product_id: '0121'},
    null, originalDevice]) {
    model.device = device;
    const label = device?.model || 'OCTA-CAPTURE', name = device?.name || 'OCTA-CAPTURE';
    await until(() => document.querySelector('#device-label').textContent === label &&
      document.querySelector('#device-status').title.startsWith(`${name} · `));
    const node = document.querySelector('#device-label');
    check(node.scrollWidth <= node.clientWidth, 'Expansion model fits in the device footer');
  }
  const graph = () => document.querySelectorAll('.curve')[4].toDataURL();
  const normal = graph();
  for (const index of [3, 0, 3, 2, 1]) {
    const rate = [44100, 48000, 96000, 192000][index], high = index === 3;
    model.state.payload_hex = `0000${index.toString(16).padStart(2, '0')}` + '00'.repeat(1143);
    model.status.sample_rate_hz = rate;
    model.status.raw_status_hex = `010000${[rate & 255, rate >> 8 & 255, rate >> 16 & 255].map(byte => byte.toString(16).padStart(2, '0')).join('')}0001`;
    await until(() => Number(document.querySelector('#footer-rate').value) === rate);
    check(document.querySelectorAll('.preamp-strip.unavailable-channel').length === (high ? 4 : 0), 'Preamp availability');
    const strip = document.querySelectorAll('.preamp-strip')[4], input = strip.querySelector('input');
    check(strip.inert === high && strip.getAttribute('aria-hidden') === String(high), 'Inert/accessibility gate');
    check(input.disabled === high && input.value === (high ? '' : '25.0'), 'Sensitivity blank/restored');
    check(getComputedStyle(strip).opacity === '1', 'Captions/background remain bright');
    if (high) check(graph() !== normal, 'Unavailable graph has no live curve');
    else check(graph() === normal, 'Available graph restored');
    document.querySelector('#view-switch').click();
    check(document.querySelectorAll('.mixer-strip.unavailable-channel').length === (high ? 12 : 0), 'Mixer availability');
    const fader = document.querySelector('.mixer-strip[data-source="input"][data-channel="4"] input[type=range]');
    check(fader.disabled === high && fader.value === (high ? '0' : '1'), 'Cached nonzero fader blank/restored');
    check(fader.parentElement.querySelector('output').textContent === (high ? '' : '-99.2'), 'Fader readout');
    check([...document.querySelectorAll('.mixer-send, .master-return, .master-reverb-type')].every(
      node => getComputedStyle(node).visibility === (high ? 'hidden' : 'visible')), 'Reverb availability');
    document.querySelector('#view-switch').click();
  }
  const pairRates = [44100, 48000, 96000];
  const pair = {experimental: true, available: true, base_id: 'serial:BASE', expansion_id: 'serial:EXP',
    selected_role: 'expansion', sample_rate_hz: 48000, allowed_rates: pairRates};
  const choices = expected => [...document.querySelectorAll('[data-rate-select]')].every(select =>
    JSON.stringify([...select.options].map(option => Number(option.value))) === JSON.stringify(expected));
  function setPair(role, available = true) {
    model.pair = role === null ? null : pair;
    pair.selected_role = role; pair.available = available;
    pair.allowed_rates = !available ? [] : role === 'base' ? [48000] : pairRates;
    pair.error = available ? null : 'EXP disconnected';
    Object.assign(model.status, {clock_source: role === 'base' ? 'external' : 'internal',
      raw_status_hex: role === 'base' ? '01000080bb000101' : '01000080bb000001'});
  }
  setPair('expansion');
  await until(() => choices(pairRates));
  const settings = document.querySelector('#settings');
  settings.showModal();
  await until(() => !document.querySelector('#settings-pair').hidden);
  check(document.querySelector('#settings-pair').textContent.includes('apply to both'), 'Paired Linux settings explanation');
  const close = settings.querySelector('footer button').getBoundingClientRect();
  check(close.top >= 0 && close.bottom <= innerHeight, 'Paired settings Close remains inside the WebKit viewport');
  setPair('base');
  await until(() => choices([48000]));
  check(document.querySelector('#settings-pair').textContent.includes('Select the EXP'), 'Base cannot set the paired clock');
  setPair('base', false);
  await until(() => choices([]));
  check([...document.querySelectorAll('[data-rate-select]')].every(select => select.disabled), 'Missing companion disables both selectors');
  setPair(null);
  await until(() => choices([44100, 48000, 96000, 192000]));
  check(document.querySelector('#settings-pair').hidden, 'Single-unit settings retain their original layout');
  settings.close();
  check(unexpected.length === 0, `Unexpected requests: ${unexpected.join(', ')}`);
  return {rates: 4, transitions: 5, paired_transitions: 4, device_writes: 0, geometry, dialogs};
}
