// SPDX-License-Identifier: GPL-2.0-only
// Locally constructed menus; labels are text, actions remain typed callbacks.
export class PopupMenus {
  constructor(container, onClose) {
    this.container = container;
    this.onClose = onClose;
    this.children = new WeakMap();
    this.parents = new WeakMap();
    this.restoreFocus = null;
  }
  close(restore = false) {
    this.container.replaceChildren();
    this.onClose();
    if (restore && this.restoreFocus?.isConnected) this.restoreFocus.focus();
  }
  open(items, x, y, anchor = null) {
    const previous = anchor || document.activeElement;
    this.close();
    this.restoreFocus = previous;
    this.anchor = anchor;
    const menu = this.create(items, x, y, 0);
    menu.querySelector('button:not(:disabled)')?.focus();
  }
  closeAfter(depth) {
    for (const menu of [...this.container.children]) {
      if (Number(menu.dataset.depth) <= depth) continue;
      this.parents.get(menu)?.setAttribute('aria-expanded', 'false');
      menu.remove();
    }
  }
  showChildren(button, focus = false) {
    const items = this.children.get(button);
    if (!items || button.disabled) return;
    const existing = [...this.container.children].find(menu => this.parents.get(menu) === button);
    if (existing) {
      if (focus) existing.querySelector('button:not(:disabled)')?.focus();
      return;
    }
    const depth = Number(button.parentElement.dataset.depth);
    this.closeAfter(depth);
    const bounds = button.getBoundingClientRect();
    const menu = this.create(items, bounds.right - 1, bounds.top, depth + 1, button);
    button.setAttribute('aria-expanded', 'true');
    if (focus) menu.querySelector('button:not(:disabled)')?.focus();
  }
  label(button, text, mnemonic) {
    const index = mnemonic ? text.toLowerCase().indexOf(mnemonic) : -1;
    if (index < 0) { button.textContent = text; return; }
    const label = document.createElement('span'), underline = document.createElement('u');
    underline.textContent = text[index];
    label.append(text.slice(0, index), underline, text.slice(index + 1));
    button.replaceChildren(label);
  }
  create(items, x, y, depth, parent = null) {
    const menu = document.createElement('div');
    menu.className = 'menu'; menu.dataset.depth = depth;
    menu.setAttribute('role', 'menu');
    if (parent) this.parents.set(menu, parent);
    for (const item of items) {
      if (!item) { menu.append(document.createElement('hr')); continue; }
      const button = document.createElement('button');
      button.textContent = item.label;
      button.setAttribute('role', 'menuitem'); button.tabIndex = -1;
      if (typeof item.checked === 'boolean') {
        button.setAttribute('role', item.checkbox ? 'menuitemcheckbox' : 'menuitemradio');
        button.setAttribute('aria-checked', String(item.checked));
        const mark = item.checkbox ? (item.checked ? '✓' : '\u00a0') : (item.checked ? '●' : '○');
        button.textContent = `${mark} ${item.label}`;
      }
      if (item.mnemonic) {
        button.dataset.mnemonic = item.mnemonic;
        button.setAttribute('aria-keyshortcuts', item.mnemonic);
        this.label(button, button.textContent, item.mnemonic);
      }
      button.disabled = Boolean(item.disabled);
      if (item.children) {
        this.children.set(button, item.children);
        button.setAttribute('aria-haspopup', 'menu');
        button.setAttribute('aria-expanded', 'false');
      }
      if (item.shortcut || item.children) {
        const suffix = document.createElement('span');
        suffix.className = item.children ? 'submenu-arrow' : 'shortcut';
        suffix.textContent = item.children ? '▶' : item.shortcut;
        button.append(suffix);
      }
      button.addEventListener('mouseenter', () => {
        if (!button.disabled) button.focus({preventScroll: true});
        if (item.children) this.showChildren(button);
        else this.closeAfter(depth);
      });
      button.addEventListener('click', () => {
        if (item.children) this.showChildren(button, true);
        else { this.close(true); void item.run?.(); }
      });
      menu.append(button);
    }
    menu.addEventListener('keydown', event => this.keyboard(event, menu));
    this.container.append(menu);
    if (parent && x + menu.offsetWidth > innerWidth - 4) {
      x = parent.getBoundingClientRect().left - menu.offsetWidth + 1;
    }
    menu.style.left = `${Math.max(0, Math.min(x, innerWidth - menu.offsetWidth - 4))}px`;
    menu.style.top = `${Math.max(0, Math.min(y, innerHeight - menu.offsetHeight - 4))}px`;
    return menu;
  }
  keyboard(event, menu) {
    const items = [...menu.querySelectorAll(':scope > button:not(:disabled)')];
    const button = event.target.closest('button');
    const index = items.indexOf(button);
    const parent = this.parents.get(menu);
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      const offset = event.key === 'ArrowDown' ? 1 : -1;
      items[(index + offset + items.length) % items.length]?.focus();
    } else if (event.key === 'Home' || event.key === 'End') {
      items[event.key === 'Home' ? 0 : items.length - 1]?.focus();
    } else if (event.key === 'ArrowRight' && button) {
      if (this.children.has(button)) this.showChildren(button, true);
      else if (!parent) this.moveRoot?.(1);
    } else if (event.key === 'ArrowLeft' || event.key === 'Escape') {
      if (parent) {
        this.closeAfter(Number(menu.dataset.depth) - 1); parent.focus();
      } else if (event.key !== 'ArrowLeft' || !this.moveRoot?.(-1)) this.close(true);
    } else if (event.key === 'Tab') {
      this.close(true); return;
    } else if (!event.ctrlKey && !event.altKey && !event.metaKey && event.key.length === 1) {
      const matches = items.filter(item => item.dataset.mnemonic === event.key.toLowerCase());
      if (!matches.length) return;
      if (!event.repeat) {
        // The original MFC menu activates its first matching mnemonic.
        matches[0].focus(); matches[0].click();
      }
    } else return;
    event.preventDefault(); event.stopPropagation();
  }
}

export class PanelMenuBar {
  constructor(container, popups, open) {
    Object.assign(this, {container, popups, open, previous: null});
    container.setAttribute('role', 'menubar');
    for (const button of container.querySelectorAll('[data-menu]')) {
      button.setAttribute('role', 'menuitem');
      button.setAttribute('aria-haspopup', 'menu');
      button.setAttribute('aria-keyshortcuts', `Alt+${button.dataset.mnemonic}`);
      button.addEventListener('pointerdown', () => this.remember());
      button.addEventListener('click', event => {
        event.stopPropagation();
        if (button.getAttribute('aria-expanded') === 'true') popups.close(true);
        else this.show(button);
      });
      button.addEventListener('mouseenter', () => {
        if (this.active() && popups.anchor !== button) this.show(button);
      });
    }
    popups.moveRoot = direction => {
      if (!this.active()) return false;
      this.move(direction, true); return true;
    };
    document.addEventListener('keydown', event => this.keyboard(event), true);
  }
  buttons() { return [...this.container.querySelectorAll('[data-menu]')].filter(button => !button.hidden && !button.disabled); }
  active() { return this.popups.container.childElementCount > 0 && this.container.contains(this.popups.anchor); }
  remember() {
    if (!this.container.contains(document.activeElement) && !this.popups.container.contains(document.activeElement)) {
      this.previous = document.activeElement;
    }
  }
  restore() {
    this.previous?.focus();
    if (this.container.contains(document.activeElement)) document.activeElement.blur();
  }
  show(button) {
    this.remember();
    button.focus();
    this.open(button.dataset.menu, button);
  }
  move(direction, expand = false) {
    const buttons = this.buttons(), current = this.active() ? this.popups.anchor : document.activeElement;
    const button = buttons[(buttons.indexOf(current) + direction + buttons.length) % buttons.length];
    if (expand) this.show(button);
    else button?.focus();
  }
  keyboard(event) {
    if (document.querySelector('dialog[open]') || event.ctrlKey || event.metaKey || event.shiftKey) return;
    const buttons = this.buttons(), key = event.key.toLowerCase();
    const mnemonic = event.altKey && buttons.find(button => button.dataset.mnemonic === key);
    if (mnemonic) {
      if (!event.repeat) this.show(mnemonic);
    } else if (event.key === 'F10' && !event.altKey) {
      if (!event.repeat) {
        if (this.active() || this.container.contains(document.activeElement)) {
          this.popups.close(); this.restore();
        } else {
          this.remember(); this.popups.close(); buttons[0]?.focus();
        }
      }
    } else if (!event.altKey && this.container.contains(document.activeElement)) {
      if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') this.move(event.key === 'ArrowRight' ? 1 : -1, this.active());
      else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        this.show(document.activeElement);
        if (event.key === 'ArrowUp') this.popups.container.querySelector('.menu > button:not(:disabled):last-of-type')?.focus();
      } else if (event.key === 'Escape') { this.popups.close(); this.restore(); }
      else return;
    } else return;
    event.preventDefault(); event.stopPropagation();
  }
}
