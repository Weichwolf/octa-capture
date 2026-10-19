// SPDX-License-Identifier: GPL-2.0-only
// Numeric Win32 child bounds, independent of the Linux layout implementation.
export async function checkControlGeometry(reference) {
  const wasPreamp = !document.querySelector('#preamp-view').hidden;
  const differences = [];
  let checked = 0, hidden = 0;
  try {
    for (const [view, items] of Object.entries(reference.views)) {
      if (document.querySelector('#preamp-view').hidden === (view === 'preamp')) {
        document.querySelector('#view-switch').click();
      }
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      const panel = document.querySelector('#panel').getBoundingClientRect();
      for (const item of items) {
        const node = document.querySelector(item.selector);
        if (!node) throw new Error(`Missing original control ${view}/${item.id}: ${item.selector}`);
        if (node.closest('[hidden]')) { hidden++; continue; }
        const box = node.getBoundingClientRect();
        const actual = [box.x - panel.x, box.y - panel.y, box.width, box.height];
        if (actual.some((value, index) => Math.abs(value - item.rect[index]) > 0.01)) {
          differences.push({view, id: item.id, selector: item.selector, expected: item.rect, actual});
        }
        checked++;
      }
    }
  } finally {
    if (document.querySelector('#preamp-view').hidden === wasPreamp) document.querySelector('#view-switch').click();
  }
  if (differences.length) throw new Error(`Original control geometry differs: ${JSON.stringify(differences)}`);
  return {checked, hidden};
}
