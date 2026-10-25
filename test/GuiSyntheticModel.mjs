// SPDX-License-Identifier: GPL-2.0-only
// Synthetic connected-device state for offline browser and WebKit presentation checks.
export function fixture() {
  const group = () => ({channels: Array.from({length: 10}, () => ({mute: false, solo: false, pan: 0, volume: 0})),
    pairs: Array.from({length: 5}, () => ({linked: false, mono: false}))});
  return {connected: true, busy: false, preferences: {show_port_warning: false}, meters: null,
    device: {id: 'serial:SYNTHETIC', name: 'OCTA-CAPTURE (SYNTHETIC)', model: 'OCTA-CAPTURE', product_id: '0120'},
    auto_sense: null, capabilities: {auto_sense: true},
    status: {sample_rate_hz: 48000, raw_status_hex: '01000080bb000001', clock_source: 'internal', clock_active: true},
    state: {payload_hex: '000001' + '00'.repeat(1143),
      preamps: Array.from({length: 8}, () => ({phantom: false, phase_inverted: false, hi_z: false,
        low_cut: false, sensitivity_index: 0, compressor: {enabled: false, gate_index: 0,
          attack_index: 0, release_index: 0, threshold_index: 0, ratio_index: 0, gain_index: 0}})),
      stereo_links: [false, false, false, false], output_routes: [0, 0, 0, 0, 0],
      mixers: Array.from({length: 4}, () => ({input: group(), playback: group(), input_output_link: false,
        input_master: {linked: false, volume: [0, 0]}, playback_master: {linked: false, volume: [0, 0]}})),
      reverb: {type: 0, pre_delay_indices: [0, 0, 0, 0, 0], time_indices: [0, 0, 0, 0, 0],
        input_send: Array(10).fill(0), master_return: 0}}};
}
