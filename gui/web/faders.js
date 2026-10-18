// SPDX-License-Identifier: GPL-2.0-only
// Preview only; the device's fresh bulk reply remains authoritative.
export function masterFaderPreview(state, controls) {
  const result = [];
  for (const control of controls) {
    if (control.kind !== 'master' || control.parameter !== 'volume') continue;
    const mixer = state.mixers[control.bus];
    if (!mixer || !['input', 'playback'].includes(control.source) || ![0, 1].includes(control.channel)) continue;
    const other = control.source === 'input' ? 'playback' : 'input';
    const channels = mixer[`${control.source}_master`].linked ? [0, 1] : [control.channel];
    for (const channel of channels) result.push({...control, channel});
    if (mixer.input_output_link) {
      const peers = mixer[`${other}_master`].linked ? [0, 1] : channels;
      for (const channel of peers) result.push({...control, source: other, channel});
    }
  }
  return result;
}
