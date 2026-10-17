// SPDX-License-Identifier: GPL-2.0-only
// Trusted test body for WebKit.call_async_javascript_function; write is supplied by the checker.
export async function checkPanel(write) {
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  const until = async (predicate, name) => {
    const deadline = Date.now() + 12000;
    while (Date.now() < deadline) {
      if (await predicate()) return;
      await pause(40);
    }
    throw new Error(`Timed out: ${name}`);
  };
  const check = (condition, message) => { if (!condition) throw new Error(message); };
  const errors = [];
  window.addEventListener('error', event => errors.push(event.message));
  window.addEventListener('unhandledrejection', event => errors.push(String(event.reason)));
  const api = async (path, body) => {
    const options = {headers: {Authorization: `Bearer ${sessionStorage.getItem('octa-token')}`}};
    if (body !== undefined) {
      options.method = 'POST'; options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(body);
    }
    const response = await fetch(path, options);
    check(response.ok, `API error ${response.status}`);
    return response.json();
  };
  await until(() => document.querySelectorAll('.preamp-strip').length === 8 &&
    document.querySelector('#connection-message').hidden, 'connected GTK panel');
  check(location.hash === '', 'Private fragment was removed');
  const panel = document.querySelector('#panel').getBoundingClientRect();
  check(panel.left >= 0 && panel.top >= 0 && panel.right <= innerWidth && panel.bottom <= innerHeight,
    `Complete panel fits the GTK viewport: ${JSON.stringify({panel:panel.toJSON(), width:innerWidth, height:innerHeight})}`);
  const initial = (await api('/api/state')).state;
  await until(async () => (await api('/api/meters')).meters?.age_ms < 1000, 'live device meters');
  await until(async () => {
    const pcm = (await api('/api/meters')).meters?.pcm;
    return pcm?.enabled && pcm.capture.fresh && pcm.playback.fresh && pcm.received_age_ms < 500;
  }, 'live kernel PCM meters');
  if (write) {
    const sensitivity = initial.preamps[0].sensitivity_index;
    const delta = sensitivity < 100 ? 1 : -1;
    document.querySelector('.preamp-strip .sensitivity').dispatchEvent(
      new KeyboardEvent('keydown', {key: delta > 0 ? 'ArrowUp' : 'ArrowDown', bubbles: true}));
    await until(async () => (await api('/api/state')).state.preamps[0].sensitivity_index === sensitivity + delta,
      'GTK control hardware readback');
    await api('/api/control', {kind: 'preamp', channel: 0, parameter: 'sensitivity', value: sensitivity});
  }
  document.querySelector('#view-switch').click();
  check(!document.querySelector('#mixer-view').hidden, 'Direct Mixer opened');
  check(document.querySelectorAll('.mixer-strip').length === 20, 'Twenty mixer strips');
  await until(() => document.querySelectorAll('.level-lane:not(.stale)').length === 20,
    'all input/playback PCM meter lanes');
  for (const id of ['patchbay', 'reverb', 'settings', 'auto-sens', 'diagram', 'about']) {
    const dialog = document.querySelector(`#${id}`);
    dialog.showModal(); check(dialog.open, `${id} opened`); dialog.close();
  }
  check((await api('/api/state')).state.payload_hex === initial.payload_hex, 'Complete state preserved');
  check(errors.length === 0, errors.join('; '));
  return {initial, meters: true, views: true, control: write, errors};
}
