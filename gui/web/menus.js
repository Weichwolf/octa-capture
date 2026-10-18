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
  open(items, x, y) {
    const previous = document.activeElement;
    this.close();
    this.restoreFocus = previous;
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
      this.showChildren(button, true);
    } else if (event.key === 'ArrowLeft' || event.key === 'Escape') {
      if (parent) {
        this.closeAfter(Number(menu.dataset.depth) - 1); parent.focus();
      } else this.close(true);
    } else if (event.key === 'Tab') {
      this.close(true); return;
    } else return;
    event.preventDefault(); event.stopPropagation();
  }
}
