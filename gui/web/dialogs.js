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
    // Resource order starts with the bottom buttons in these Windows dialogs.
    if (dialog.hasAttribute('data-footer-first')) this.caption.after(dialog.querySelector(':scope > footer'));
    dialog.addEventListener('keydown', event => this.keyboard(event));
    dialog.addEventListener('focusin', () => this.defaultStyle());
    this.caption.addEventListener('pointerdown', event => this.start(event));
    this.caption.addEventListener('pointermove', event => this.move(event));
    for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) {
      this.caption.addEventListener(name, event => {
        if (event.pointerId === this.drag?.pointer) this.stop();
      });
    }
    dialog.addEventListener('close', () => this.reset());
    new MutationObserver(records => {
      if (!records.some(record => record.target === dialog && record.attributeName === 'open')) {
        this.defaultStyle(); return;
      }
      this.reset();
      if (!dialog.open) return;
      // HTML dialog autofocus includes tabindex=-1; keep the caption out of it.
      if (document.activeElement === close || document.activeElement === dialog) {
        this.controls()[0]?.focus();
      }
      this.defaultStyle();
    }).observe(dialog, {attributes: true, subtree: true, attributeFilter: ['open', 'disabled']});
    new ResizeObserver(() => { if (dialog.open && dialog.style.inset) this.fit(); }).observe(dialog);
    window.addEventListener('resize', () => { if (dialog.open && dialog.style.inset) this.fit(); });
    window.addEventListener('blur', () => this.stop());
    document.addEventListener('visibilitychange', () => { if (document.hidden) this.stop(); });
  }
  controls() {
    return [...this.dialog.querySelectorAll('button, input, select, textarea, a[href], [tabindex]')]
      .filter(node => !node.disabled && node.tabIndex >= 0 && node.getClientRects().length);
  }
  defaultButton() {
    const focused = document.activeElement?.closest('button:not(.dialog-close)');
    return focused && this.dialog.contains(focused) ? focused : this.dialog.querySelector('[data-default]');
  }
  defaultStyle() {
    const selected = this.defaultButton();
    for (const button of this.dialog.querySelectorAll('button')) {
      button.classList.toggle('dialog-default', button === selected && !button.disabled);
    }
  }
  keyboard(event) {
    if (event.defaultPrevented || event.ctrlKey || event.altKey || event.metaKey) return;
    // WebKit/GTK X11 reports Shift+Tab as Unidentified with physical code Tab.
    if (event.key === 'Tab' || (event.key === 'Unidentified' && event.code === 'Tab')) {
      const controls = this.controls(), index = controls.indexOf(document.activeElement);
      const next = index < 0 ? (event.shiftKey ? controls.length - 1 : 0)
        : (index + (event.shiftKey ? -1 : 1) + controls.length) % controls.length;
      event.preventDefault(); controls[next]?.focus();
    } else if (event.key === 'Enter' && !event.shiftKey) {
      // Native open combo popups own Enter; a closed combo uses the default.
      try { if (event.target.matches('select:open')) return; } catch { /* Older WebKit has no :open. */ }
      event.preventDefault();
      this.defaultButton()?.click();
    }
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
