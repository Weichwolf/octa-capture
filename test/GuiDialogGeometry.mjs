// SPDX-License-Identifier: GPL-2.0-only
// Original Windows client/control bounds; opening and closing performs no device writes.
export async function checkDialogGeometry(reference) {
  const differences = [];
  let checked = 0;
  for (const [name, item] of Object.entries(reference.dialogs)) {
    const dialog = document.getElementById(name);
    if (dialog.open) throw new Error(`Geometry check requires a closed dialog: ${name}`);
    try {
      dialog.showModal();
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      const client = dialog.querySelector('.reference-dialog-client').getBoundingClientRect();
      if (client.width !== item.client_size[0] || client.height !== item.client_size[1]) {
        differences.push({name, expected: item.client_size, actual: [client.width, client.height]});
      }
      for (const control of item.controls) {
        const nodes = dialog.querySelectorAll(control.selector);
        if (nodes.length !== 1) throw new Error(`Expected one dialog control: ${name}/${control.id}/${control.selector}`);
        const box = nodes[0].getBoundingClientRect();
        const actual = [box.x - client.x, box.y - client.y, box.width, box.height];
        if (actual.some((value, index) => Math.abs(value - control.rect[index]) > 0.01)) {
          differences.push({name, id: control.id, expected: control.rect, actual});
        }
        if (nodes[0].matches('button, input, select, .stepper-buttons, .auto-channels label')) {
          const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
          if (!hit || !nodes[0].contains(hit)) throw new Error(`Dialog control is obscured: ${name}/${control.id}`);
        }
        checked++;
      }
      if (name === 'auto-sens') {
        const status = dialog.querySelector('#auto-status'), original = status.textContent;
        try {
          status.textContent = 'Device communication failed. Completion is unverified; check the device and retry recovery. '.repeat(4);
          const message = status.getBoundingClientRect(), button = dialog.querySelector('#auto-cancel').getBoundingClientRect();
          if (message.height <= 12 || message.bottom > button.top - 6 ||
              button.bottom > dialog.querySelector('.reference-dialog-client').getBoundingClientRect().bottom - 8) {
            throw new Error('A multiline Auto-Sens failure must remain readable above reachable recovery controls.');
          }
        } finally {
          status.textContent = original;
        }
      }
    } finally {
      dialog.close();
    }
  }
  if (differences.length) throw new Error(`Original dialog geometry differs: ${JSON.stringify(differences)}`);
  return {dialogs: Object.keys(reference.dialogs).length, checked};
}
