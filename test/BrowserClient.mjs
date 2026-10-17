// SPDX-License-Identifier: GPL-2.0-only
// Test-only Chromium DevTools client. No remote service or browser extension.
import fs from 'node:fs/promises';
import {createRequire} from 'node:module';
const require = createRequire(import.meta.url);
const {WebSocket} = require('undici');

export const pause = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));

export async function until(predicate, description, timeout = 12000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await predicate()) return;
    await pause(30);
  }
  throw new Error(`Timed out: ${description}`);
}

export class BrowserClient {
  static async attach(portFile) {
    const port = Number((await fs.readFile(portFile, 'utf8')).split('\n')[0]);
    const response = await fetch(`http://127.0.0.1:${port}/json/list`);
    const target = (await response.json()).find(value => value.type === 'page');
    if (!target) throw new Error('No Chromium page available.');
    const socket = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      socket.addEventListener('open', resolve, {once: true});
      socket.addEventListener('error', reject, {once: true});
    });
    const client = new BrowserClient(socket);
    await client.call('Runtime.enable');
    await client.call('Page.enable');
    await client.call('Log.enable');
    await client.call('Emulation.setDeviceMetricsOverride', {width: 1100, height: 650, deviceScaleFactor: 1, mobile: false});
    return client;
  }

  constructor(socket) {
    this.socket = socket;
    this.serial = 0;
    this.pending = new Map();
    this.exceptions = [];
    this.securityErrors = [];
    socket.addEventListener('message', event => {
      const value = JSON.parse(event.data);
      if (value.id) {
        const pending = this.pending.get(value.id);
        if (!pending) return;
        this.pending.delete(value.id);
        clearTimeout(pending.timer);
        if (value.error) pending.reject(new Error(value.error.message));
        else pending.resolve(value.result);
      } else if (value.method === 'Runtime.exceptionThrown') {
        this.exceptions.push(value.params.exceptionDetails);
      } else if (value.method === 'Log.entryAdded' && value.params.entry.source === 'security' && value.params.entry.level === 'error') {
        this.securityErrors.push(value.params.entry.text);
      }
    });
  }

  call(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = ++this.serial;
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error(`CDP timeout: ${method}`)); }, 12000);
      this.pending.set(id, {resolve, reject, timer});
      this.socket.send(JSON.stringify({id, method, params}));
    });
  }

  async evaluate(expression) {
    const response = await this.call('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
    if (response.exceptionDetails) throw new Error(response.exceptionDetails.exception?.description || response.exceptionDetails.text);
    return response.result.value;
  }

  async click(selector) {
    await this.ready(selector);
    const rectangle = await this.evaluate(`(() => {const node=document.querySelector(${JSON.stringify(selector)});if(!node)throw new Error('Missing control');const r=node.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()`);
    await this.call('Input.dispatchMouseEvent', {type: 'mousePressed', button: 'left', clickCount: 1, ...rectangle});
    await this.call('Input.dispatchMouseEvent', {type: 'mouseReleased', button: 'left', clickCount: 1, ...rectangle});
  }

  async key(selector, key) {
    await this.ready(selector);
    await this.evaluate(`document.querySelector(${JSON.stringify(selector)}).focus()`);
    const codes = {ArrowUp: 38, ArrowDown: 40, ArrowLeft: 37, ArrowRight: 39, Home: 36, End: 35, Escape: 27};
    await this.call('Input.dispatchKeyEvent', {type: 'keyDown', key, code: key, windowsVirtualKeyCode: codes[key]});
    await this.call('Input.dispatchKeyEvent', {type: 'keyUp', key, code: key, windowsVirtualKeyCode: codes[key]});
  }

  async ready(selector) {
    await until(() => this.evaluate(`(() => {const node=document.querySelector(${JSON.stringify(selector)});return node && !node.disabled && node.getAttribute('aria-disabled') !== 'true' && !node.closest('.unavailable-channel') && (node.closest('#auto-sens') || document.querySelector('#busy-message').hidden);})()`), 'enabled browser control');
  }

  async screenshot(filename) {
    const clip = await this.evaluate('(() => {const r=document.querySelector("#panel").getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,scale:1};})()');
    const response = await this.call('Page.captureScreenshot', {format: 'png', clip});
    await fs.writeFile(filename, Buffer.from(response.data, 'base64'));
  }

  async blank() {
    await this.call('Page.navigate', {url: 'about:blank'});
    await until(() => this.evaluate('location.href === "about:blank"'), 'blank page');
  }

  close() {
    for (const pending of this.pending.values()) { clearTimeout(pending.timer); pending.reject(new Error('Browser client closed.')); }
    this.pending.clear();
    this.socket.close();
  }
}
