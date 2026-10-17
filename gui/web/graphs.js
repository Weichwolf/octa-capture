// SPDX-License-Identifier: GPL-2.0-only
// Original curve geometry and drawing parameters, with independently drawn assets.
import {GRAPH_MIN_DB, compressorOutput} from './parameters.js';

export const GRAPH_STYLE = Object.freeze({
  enabled: Object.freeze({background: '#d09b42', fill: '#b6643c', line: '#3c3c3c', grid: '#857358'}),
  bypassed: Object.freeze({background: '#634d42', fill: '#534138', line: '#383838', grid: '#6b593e'}),
  lineWidth: Math.fround(1.8),
});

export function compressorShape(compressor) {
  const gate = compressor.gate_index === 0 ? 0 : compressor.gate_index - 70;
  const gated = gate > GRAPH_MIN_DB && gate !== 0;
  const start = gated ? gate : GRAPH_MIN_DB;
  const point = input => [(1 - input / GRAPH_MIN_DB) * 50,
    compressorOutput(input, compressor) * 50 / GRAPH_MIN_DB];
  const first = point(start);
  const polygon = [[first[0], 50], first,
    point(Math.max(start, compressor.threshold_index - 50)), point(0), [50, 50]];
  return {polygon, line: polygon.slice(gated ? 0 : 1, 4)};
}

function path(context, points) {
  context.beginPath(); context.moveTo(...points[0]);
  for (const point of points.slice(1)) context.lineTo(...point);
}

export function drawCompressor(canvas, compressor) {
  const context = canvas.getContext('2d');
  const style = compressor.enabled ? GRAPH_STYLE.enabled : GRAPH_STYLE.bypassed;
  const shape = compressorShape(compressor);
  context.setTransform(canvas.width / 51, 0, 0, canvas.height / 51, 0, 0);
  context.clearRect(0, 0, 51, 51);
  context.fillStyle = style.background; context.fillRect(0, 0, 51, 51);
  path(context, shape.polygon); context.closePath();
  context.fillStyle = style.fill; context.fill();
  path(context, shape.line);
  context.strokeStyle = style.line; context.lineWidth = GRAPH_STYLE.lineWidth; context.stroke();
  // The reference grid is composited after the filled curve, at 12 dB intervals.
  context.fillStyle = style.grid;
  for (let position = 0; position <= 50; position += 10) {
    context.fillRect(position, 0, 1, 51); context.fillRect(0, position, 51, 1);
  }
  const shade = context.createLinearGradient(0, 0, 4, 0);
  shade.addColorStop(0, '#000a'); shade.addColorStop(1, '#0000');
  context.fillStyle = shade; context.fillRect(0, 0, 4, 51);
  const top = context.createLinearGradient(0, 0, 0, 4);
  top.addColorStop(0, '#000a'); top.addColorStop(1, '#0000');
  context.fillStyle = top; context.fillRect(0, 0, 51, 4);
}
