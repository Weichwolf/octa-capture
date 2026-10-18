// SPDX-License-Identifier: GPL-2.0-only
// Playwright actions for the shared browser/hardware acceptance suite.
import path from 'node:path';
import {existsSync} from 'node:fs';
import {createRequire} from 'node:module';
import {until} from './BrowserClient.mjs';
const require = createRequire(import.meta.url);
const privatePackage = new URL('../build/playwright-runner/node_modules/playwright', import.meta.url).pathname;
const {chromium} = require(existsSync(privatePackage) ? privatePackage : 'playwright');
const safe = error => String(error?.message || error).replace(/#token=[A-Za-z0-9_-]+/g, '#token=[redacted]');

export class PlaywrightClient {
  static async attach() {
    const browser = await chromium.launch({executablePath: '/usr/bin/chromium', headless: true,
      args: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage', '--disable-background-networking']});
    const context = await browser.newContext({viewport: {width: 1100, height: 650}, acceptDownloads: true});
    return new PlaywrightClient(browser, context, await context.newPage());
  }

  constructor(browser, context, page) {
    Object.assign(this, {browser, context, page, exceptions: [], securityErrors: [], consoleErrors: [], consoleEvents: [],
      networkErrors: [], httpErrors: [], controls: [], selections: [], requests: new Map(), downloads: []});
    page.on('pageerror', error => this.exceptions.push(safe(error)));
    page.on('console', message => {
      if (message.type() !== 'error') return;
      this.consoleErrors.push(safe(message.text()));
      let pathname = null;
      try { pathname = new URL(message.location().url).pathname; } catch { /* Non-URL console location. */ }
      this.consoleEvents.push({text: safe(message.text()), path: pathname});
      if (/Content Security Policy|Refused to/i.test(message.text())) this.securityErrors.push(safe(message.text()));
    });
    page.on('request', request => {
      const endpoint = new URL(request.url()).pathname;
      if (request.method() !== 'POST' || !['/api/control', '/api/device'].includes(endpoint)) return;
      const control = {...request.postDataJSON(), httpStatus: null};
      const records = endpoint === '/api/device' ? this.selections : this.controls;
      records.push(control);
      this.requests.set(request, control);
      if (records.length > 1000) records.shift();
    });
    page.on('response', response => {
      const control = this.requests.get(response.request());
      if (control) { control.httpStatus = response.status(); this.requests.delete(response.request()); }
      if (response.status() >= 400) this.httpErrors.push({path: new URL(response.url()).pathname, status: response.status()});
    });
    page.on('requestfailed', request => {
      const control = this.requests.get(request);
      if (control) { control.httpStatus = 0; this.requests.delete(request); }
      const error = request.failure()?.errorText;
      if (error !== 'net::ERR_ABORTED') this.networkErrors.push({path: new URL(request.url()).pathname, error});
    });
    page.on('download', download => {
      const promise = download.saveAs(path.join(this.downloadDirectory, path.basename(download.suggestedFilename())))
        .catch(error => { this.exceptions.push(safe(error)); });
      this.downloads.push(promise);
    });
  }

  async call(method, params = {}) {
    try {
      switch (method) {
        case 'Page.navigate': return await this.page.goto(params.url);
        case 'Input.insertText': return await this.page.keyboard.insertText(params.text);
        case 'Page.captureScreenshot': return {data: (await this.page.screenshot()).toString('base64')};
        case 'Emulation.setDeviceMetricsOverride': return await this.page.setViewportSize({width: params.width, height: params.height});
        case 'Input.dispatchKeyEvent': return await this.page.keyboard[params.type === 'keyDown' ? 'down' : 'up'](params.key);
        default: throw new Error(`Unsupported shared browser operation: ${method}`);
      }
    } catch (error) { throw new Error(safe(error)); }
  }

  async evaluate(expression) { return this.page.evaluate(expression); }

  async ready(selector) {
    await until(() => this.evaluate(`(() => {
      const node=document.querySelector(${JSON.stringify(selector)});
      const modal=[...document.querySelectorAll('dialog[open]')].at(-1);
      return node && node.getClientRects().length > 0 && (!modal || modal.contains(node)) &&
        !node.disabled && node.getAttribute('aria-disabled') !== 'true' && !node.closest('.unavailable-channel') &&
        (node.closest('#auto-sens') || node.matches('#view-switch,#header-action') || document.querySelector('#busy-message')?.hidden);
    })()`), `enabled and accessible Playwright control: ${selector}`);
  }

  async click(selector, clickCount = 1, button = 'left') {
    await this.ready(selector);
    await this.page.locator(selector).first().click({button, clickCount});
  }

  async key(selector, key) {
    await this.ready(selector);
    await this.page.locator(selector).first().focus();
    await this.page.keyboard.press(key);
  }

  async screenshot(filename) { await this.page.locator('#panel').screenshot({path: filename}); }
  async blank() { await this.page.goto('about:blank'); }
  async downloadTo(directory) { this.downloadDirectory = directory; }
  async setFiles(selector, files) { await this.page.locator(selector).setInputFiles(files); }
  async close() { await Promise.all(this.downloads); await this.browser.close(); }
}
