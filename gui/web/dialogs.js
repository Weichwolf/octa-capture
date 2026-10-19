// SPDX-License-Identifier: GPL-2.0-only
// Modal caption controls; cancellation remains owned by each dialog's handler.
export class DialogWindow {
  constructor(dialog) {
    this.dialog = dialog;
    this.drag = null;
    const heading = dialog.querySelector(':scope > h2');
    this.caption = document.createElement('header');
    this.caption.className = 'dialog-title';
    heading.replaceWith(this.caption);
    this.caption.append(heading);
    const close = document.createElement('button');
    close.type = 'button'; close.className = 'dialog-close'; close.tabIndex = -1;
    close.textContent = '×'; close.title = 'Close';
    close.setAttribute('aria-label', `Close ${heading.textContent}`);
    close.addEventListener('click', () => {
      // Auto-Sens and confirmations may veto closing or complete asynchronously.
      if (dialog.dispatchEvent(new Event('cancel', {cancelable: true})) && dialog.open) dialog.close();
    });
    this.caption.append(close);
    this.caption.addEventListener('pointerdown', event => this.start(event));
    this.caption.addEventListener('pointermove', event => this.move(event));
    for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) {
      this.caption.addEventListener(name, event => {
        if (event.pointerId === this.drag?.pointer) this.stop();
      });
    }
    dialog.addEventListener('close', () => this.reset());
    new MutationObserver(() => {
      this.reset();
      if (!dialog.open) return;
      // HTML dialog autofocus includes tabindex=-1; keep the caption out of it.
      if (document.activeElement === close) {
        const content = [...dialog.querySelectorAll('button, input, select, textarea, a[href], [tabindex]')];
        content.find(node => node !== close && !node.disabled && node.tabIndex >= 0 && node.getClientRects().length)?.focus();
      }
    }).observe(dialog, {attributes: true, attributeFilter: ['open']});
    new ResizeObserver(() => { if (dialog.open && dialog.style.inset) this.fit(); }).observe(dialog);
    window.addEventListener('resize', () => { if (dialog.open && dialog.style.inset) this.fit(); });
    window.addEventListener('blur', () => this.stop());
    document.addEventListener('visibilitychange', () => { if (document.hidden) this.stop(); });
  }
  start(event) {
    if (event.button !== 0 || !event.isPrimary || event.target.closest('button')) return;
    event.preventDefault();
    this.stop();
    const rect = this.dialog.getBoundingClientRect();
    this.drag = {pointer: event.pointerId, x: event.clientX, y: event.clientY, left: rect.left, top: rect.top};
    this.caption.setPointerCapture(event.pointerId);
  }
  move(event) {
    if (event.pointerId !== this.drag?.pointer) return;
    if (!(event.buttons & 1)) { this.stop(); return; }
    event.preventDefault();
    this.place(this.drag.left + event.clientX - this.drag.x, this.drag.top + event.clientY - this.drag.y);
  }
  place(left, top) {
    const rect = this.dialog.getBoundingClientRect();
    const x = Math.max(0, Math.min(left, innerWidth - rect.width));
    const y = Math.max(0, Math.min(top, innerHeight - rect.height));
    this.dialog.style.margin = '0';
    this.dialog.style.inset = `${y}px auto auto ${x}px`;
  }
  fit() {
    const rect = this.dialog.getBoundingClientRect();
    this.place(rect.left, rect.top);
    // Content/viewport changes invalidate the previous pointer anchor.
    this.stop();
  }
  stop() {
    if (!this.drag) return;
    const pointer = this.drag.pointer;
    this.drag = null;
    if (this.caption.hasPointerCapture(pointer)) this.caption.releasePointerCapture(pointer);
  }
  reset() {
    this.stop();
    for (const property of ['margin', 'inset', 'top', 'right', 'bottom', 'left']) {
      this.dialog.style.removeProperty(property);
    }
  }
}
