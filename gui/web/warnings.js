// SPDX-License-Identifier: GPL-2.0-only
// The original panel warns once on entering 192 kHz and defers while unavailable.
export class PortWarningState {
  constructor() { this.rate = 0; this.pending = false; }
  update(rate, enabled, ready) {
    const changed = rate !== this.rate;
    this.rate = rate;
    this.pending = rate === 192000 && enabled && (changed || this.pending);
    const show = this.pending && ready;
    if (show) this.pending = false;
    return show;
  }
}

export class PanelWarnings {
  constructor(api, notify, changed = () => {}) {
    this.api = api; this.notify = notify; this.changed = changed;
    this.state = new PortWarningState();
    this.rate = 0; this.preferences = null; this.saving = false;
    this.dialog = document.querySelector('#port-warning');
    this.editors = [
      {dialog: document.querySelector('#about'), check: document.querySelector('#show-port-warning'), inverse: false, dirty: false},
      {dialog: this.dialog, check: document.querySelector('#hide-port-warning'), inverse: true, dirty: false},
    ];
    for (const editor of this.editors) {
      editor.check.addEventListener('change', () => { editor.dirty = true; });
      editor.dialog.querySelector('[data-close]').addEventListener('click', () => { void this.save(editor); });
      editor.dialog.addEventListener('cancel', event => { if (this.saving) event.preventDefault(); });
      new MutationObserver(() => { editor.dirty = false; this.sync(); })
        .observe(editor.dialog, {attributes: true, attributeFilter: ['open']});
    }
    for (const dialog of document.querySelectorAll('dialog')) {
      dialog.addEventListener('close', () => { this.update(this.rate); });
    }
    void this.load();
  }
  async load() {
    try {
      this.preferences = await this.api('/api/preferences');
      this.update(this.rate);
    } catch (error) { this.notify(error.message); }
  }
  sync() {
    for (const editor of this.editors) {
      editor.check.disabled = this.saving || !this.preferences;
      for (const button of editor.dialog.querySelectorAll('[data-close], .dialog-close')) button.disabled = this.saving;
      if (this.preferences && !this.saving && (!editor.dialog.open || !editor.dirty)) {
        editor.check.checked = editor.inverse ? !this.preferences.show_port_warning : this.preferences.show_port_warning;
      }
    }
  }
  update(rate, preferences = null) {
    this.rate = rate;
    if (preferences && !this.saving) this.preferences = preferences;
    this.sync();
    if (!this.preferences) return;
    const ready = !document.querySelector('dialog[open]');
    if (this.state.update(rate, this.preferences.show_port_warning, ready)) this.dialog.showModal();
  }
  accepted(editor, preferences) {
    this.preferences = preferences;
    editor.dirty = false;
    editor.dialog.close();
  }
  async save(editor) {
    if (this.saving || !editor.dialog.open) return;
    if (!this.preferences) { editor.dialog.close(); return; }
    const enabled = editor.inverse ? !editor.check.checked : editor.check.checked;
    this.saving = true; this.changed(); this.sync();
    try {
      this.accepted(editor, await this.api('/api/preferences', {show_port_warning: enabled}));
    } catch (error) { this.notify(error.message); }
    finally { this.saving = false; this.changed(); this.update(this.rate); }
  }
}
