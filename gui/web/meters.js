// SPDX-License-Identifier: GPL-2.0-only
// Numerical meter thresholds agree in both original panels.
export const METER_TICK_MS = 30;
export const METER_FALL_DB = 0.72;
export const METER_HOLD_TICKS = 50;
export const CLIP_HOLD_TICKS = 133;

export const LEVEL_THRESHOLDS = Object.freeze([
  -48.164799, -47.511417, -47.141749, -46.787174, -46.446507, -46.118698, -45.70002, -45.300599,
  -44.91874, -44.552964, -44.201971, -43.864613, -43.460545, -43.074442, -42.704774, -42.350199,
  -41.942973, -41.617621, -41.242632, -40.824673, -40.481765, -40.098098, -39.730665, -39.328933,
  -38.992042, -38.622374, -38.224478, -37.844013, -37.479516, -37.091689, -36.720441, -36.364412,
  -35.988932, -35.597021, -35.222032, -34.862565, -34.489229, -34.104348, -33.735801, -33.357546,
  -32.995078, -32.624423, -32.247199, -31.864873, -31.498669, -31.109102, -30.73626, -30.361271,
  -29.985052, -29.624453, -29.24743, -28.871359, -28.496785, -28.124179, -27.741013, -27.374038,
  -26.998177, -26.61517, -26.248341, -25.865078, -25.498014, -25.117121, -24.743075, -24.366926,
  -24.0824, -23.806899, -23.619924, -23.436889, -23.24992, -23.059349, -22.87287, -22.683086,
  -22.497361, -22.308606, -22.123866, -21.936345, -21.746303, -21.56033, -21.372044, -21.181678,
  -20.995394, -20.807203, -20.623002, -20.43705, -20.249538, -20.060648, -19.870554, -19.684531,
  -19.497404, -19.309326, -19.120443, -18.935579, -18.749976, -18.559269, -18.37266, -18.185672,
  -17.998414, -17.810987, -17.623485, -17.435998, -17.24861, -17.061398, -16.874437, -16.684174,
  -16.497989, -16.312244, -16.123599, -15.935644, -15.74842, -15.561965, -15.373202, -15.185408,
  -14.998608, -14.809905, -14.62236, -14.435984, -14.248054, -14.061428, -13.873489, -13.686967,
  -13.499349, -13.310788, -13.12383, -12.93611, -12.747759, -12.561154, -12.374066, -12.18661,
  -12.0412, -11.904444, -11.811013, -11.718577, -11.623071, -11.530602, -11.43713, -11.342696,
  -11.249277, -11.154938, -11.061612, -10.967404, -10.874207, -10.780165, -10.68713, -10.593286,
  -10.49867, -10.405073, -10.312474, -10.217413, -10.123381, -10.030356, -9.936653, -9.842303,
  -9.748967, -9.655012, -9.562063, -9.46852, -9.374414, -9.279774, -9.186153, -9.093531,
  -8.998898, -8.905285, -8.811208, -8.718139, -8.624625, -8.530692, -8.436363, -8.343048,
  -8.249354, -8.155304, -8.062262, -7.967552, -7.873864, -7.781175, -7.686895, -7.593628,
  -7.498838, -7.405071, -7.312305, -7.218085, -7.124876, -7.030275, -6.936692, -6.842942,
  -6.749039, -6.656141, -6.561969, -6.46769, -6.374424, -6.281056, -6.186519, -6.093,
  -6.0206, -5.937238, -5.874456, -5.812124, -5.749209, -5.686746, -5.624729, -5.562146,
  -5.499012, -5.437325, -5.374105, -5.312318, -5.249997, -5.187157, -5.124769, -5.061876,
  -4.999435, -4.937439, -4.874954, -4.811992, -4.749484, -4.687422, -4.624897, -4.561923,
  -4.499402, -4.437328, -4.374818, -4.311882, -4.2494, -4.187364, -4.124915, -4.062066,
  -3.999668, -3.936881, -3.874545, -3.811831, -3.749566, -3.686934, -3.624751, -3.56221,
  -3.499323, -3.436889, -3.374899, -3.311797, -3.24992, -3.186951, -3.124435, -3.062366,
  -2.999988, -2.937311, -2.874346, -2.811833, -2.749767, -2.687419, -2.624799, -2.561914,
  -2.499482, -2.437495, -2.374553, -2.312064, -2.249335, -2.187056, -2.124543, -2.062476,
  -2.0,
]);

export const HEADROOM_THRESHOLDS = Object.freeze([
  -2.0, -1.999515, -1.937008, -1.874946, -1.812019, -1.749544, -1.686872, -1.624649,
  -1.562233, -1.499633, -1.437481, -1.374528, -1.312028, -1.249975, -1.187146, -1.124769,
  -1.062237, -0.999557, -0.937326, -0.874952, -0.81244, -0.749797, -0.687029, -0.624711,
  -0.562271, -0.499716, -0.43705, -0.374833, -0.311959, -0.249538, -0.18702, -0.124948,
  -0.062249,
]);

// The low scale returns 1..257; zero is below its floor and -1 is above -2 dB.
// The headroom scale returns 0..32; -1 means full scale or above.
function nearest(table, value) {
  let low = 0, high = table.length - 1;
  while (low < high) {
    const middle = Math.ceil((low + high) / 2);
    if (table[middle] <= value) low = middle;
    else high = middle - 1;
  }
  return low < table.length - 1 && table[low + 1] - value <= value - table[low] ? low + 1 : low;
}

export function meterIndex(value) {
  if (!Number.isFinite(value) || value < LEVEL_THRESHOLDS[0]) return 0;
  if (value > LEVEL_THRESHOLDS.at(-1)) return -1;
  return nearest(LEVEL_THRESHOLDS, value) + 1;
}

export function headroomIndex(value) {
  if (!Number.isFinite(value) || value <= -2) return 0;
  if (value >= 0) return -1;
  return nearest(HEADROOM_THRESHOLDS, value);
}

// Original sprite geometry: four pixels above -2 dB and five below the floor.
export function meterY(value, height) {
  const index = meterIndex(value);
  const top = 4, bottom = 5;
  if (index === 0) return height;
  if (index < 0) {
    const headroom = headroomIndex(value);
    return headroom < 0 ? 0 : top - Math.trunc((headroom - 1) * top / 32);
  }
  const usable = height - top - bottom;
  const scaled = Math.fround(Math.fround((index - 1) * usable) / 256);
  return height - bottom - Math.trunc(Math.fround(scaled + 0.5));
}

export function meterDb(level) {
  if (!Number.isInteger(level) || level < 0 || level > 16383) return -96;
  return level === 0 ? -96 : 20 * Math.log10(level / 16383);
}

export class MeterBallistics {
  constructor(clipThreshold = 0) {
    this.clipThreshold = clipThreshold;
    this.reset();
  }
  reset() {
    this.level = -96;
    this.peak = -96;
    this.hold = 0;
    this.clipHold = 0;
    this.raw = -96;
    this.pending = null;
  }
  feed(value) {
    if (!Number.isFinite(value) || value < -96 || value > 0) return;
    this.raw = value;
    this.pending = Math.max(this.pending ?? -96, value);
  }
  step() {
    const value = this.pending ?? this.raw;
    this.pending = null;
    this.hold = Math.max(0, this.hold - 1);
    this.clipHold = Math.max(0, this.clipHold - 1);
    if (value >= this.peak || this.hold === 0) {
      if (value < this.peak && value <= this.peak - METER_FALL_DB) this.peak -= METER_FALL_DB;
      else { this.peak = value; this.hold = METER_HOLD_TICKS; }
    }
    this.level = Math.max(value, this.level - METER_FALL_DB);
    if (value >= this.clipThreshold) this.clipHold = CLIP_HOLD_TICKS;
  }
}
