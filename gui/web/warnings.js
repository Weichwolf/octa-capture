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
  constructor(api, notify) {
    this.api = api; this.notify = notify;
    this.state = new PortWarningState();
    this.rate = 0; this.preferences = null; this.saving = false;
    this.dialog = document.querySelector('#port-warning');
    this.showCheck = document.querySelector('#show-port-warning');
    this.hideCheck = document.querySelector('#hide-port-warning');
    this.showCheck.addEventListener('change', () => { void this.save(this.showCheck.checked); });
    this.hideCheck.addEventListener('change', () => { void this.save(!this.hideCheck.checked); });
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
    this.showCheck.disabled = this.hideCheck.disabled = this.saving || !this.preferences;
    if (this.preferences && !this.saving) {
      this.showCheck.checked = this.preferences.show_port_warning;
      this.hideCheck.checked = !this.preferences.show_port_warning;
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
  async save(enabled) {
    if (this.saving) return;
    this.saving = true; this.sync();
    try { this.preferences = await this.api('/api/preferences', {show_port_warning: enabled}); }
    catch (error) { this.notify(error.message); }
    finally { this.saving = false; this.update(this.rate); }
  }
}
