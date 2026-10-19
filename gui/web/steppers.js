// SPDX-License-Identifier: GPL-2.0-only
// Read-only value with clamped index stepping, matching the reverb dialog.
export class ReferenceStepper {
  constructor(node, maximum, format, unit, change) {
    this.node = node;
    this.input = node.querySelector('input');
    this.buttons = [...node.querySelectorAll('button')];
    // The original read-only edit/up-down pairs have no WS_TABSTOP.
    for (const control of [this.input, ...this.buttons]) control.tabIndex = -1;
    this.maximum = maximum;
    this.format = format;
    this.unit = unit;
    this.change = change;
    this.value = 0;
    this.enabled = false;
    this.timer = null;
    this.input.setAttribute('role', 'spinbutton');
    this.input.setAttribute('aria-valuemin', format(0));
    this.input.setAttribute('aria-valuemax', format(maximum));
    this.input.addEventListener('keydown', event => {
      if (!['ArrowUp', 'ArrowDown'].includes(event.key)) return;
      event.preventDefault();
      this.step(event.key === 'ArrowUp' ? 1 : -1);
    });
    for (const button of this.buttons) {
      const direction = Number(button.dataset.step);
      button.addEventListener('pointerdown', event => {
        if (event.button !== 0 || !this.enabled) return;
        event.preventDefault();
        this.input.focus();
        button.setPointerCapture(event.pointerId);
        this.cancel();
        this.step(direction);
        const repeat = () => {
          this.step(direction);
          if (this.enabled) this.timer = setTimeout(repeat, 100);
        };
        this.timer = setTimeout(repeat, 500);
      });
      // Keyboard and assistive-technology clicks have no pointer-down event.
      button.addEventListener('click', event => { if (event.detail === 0) this.step(direction); });
      for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) {
        button.addEventListener(name, () => this.cancel());
      }
    }
    window.addEventListener('blur', () => this.cancel());
    document.addEventListener('visibilitychange', () => { if (document.hidden) this.cancel(); });
    this.sync(0, false);
  }
  display() {
    this.input.value = this.format(this.value);
    this.input.setAttribute('aria-valuenow', this.input.value);
    this.input.setAttribute('aria-valuetext', `${this.input.value} ${this.unit}`);
  }
  step(direction) {
    if (!this.enabled) return;
    const next = Math.max(0, Math.min(this.maximum, this.value + direction));
    if (next === this.value) return;
    this.value = next;
    this.display();
    this.change(next);
  }
  sync(value, enabled, pending = false) {
    this.enabled = enabled;
    this.input.disabled = !enabled;
    for (const button of this.buttons) button.disabled = !enabled;
    if (!enabled) this.cancel();
    if (!pending) {
      this.value = value;
      this.display();
    }
  }
  cancel() {
    clearTimeout(this.timer);
    this.timer = null;
  }
}
