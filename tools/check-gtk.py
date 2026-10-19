#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Exercise the GTK/WebKit panel on X11, including its actual native Save dialog."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
location_parser = argparse.ArgumentParser(add_help=False)
location_parser.add_argument('--installed-prefix', type=Path)
installed_prefix = location_parser.parse_known_args()[0].installed_prefix
application_root = installed_prefix.resolve() / 'libexec/octa-capture' if installed_prefix else ROOT
sys.path.insert(0, str(application_root))
from gui.application import Application
from gui.desktop import Desktop
from gui.server import LocalServer


class Check(Desktop):
    def __init__(self, application, server, directory, write):
        super().__init__(application, server)
        self.directory = directory
        self.write = write
        self.errors = []
        self.result = None
        self.saved = directory / 'GTK-settings.octa'
        self.phase = 'loading'
        self.deadline = time.monotonic() + 40
        self.pending = False
        self.loads = 0
        self.open_request = None
        self.native_pointer_events = []
        self.hit_process = None

    def fail(self, error):
        self.errors.append(str(error))
        if self.hit_process is not None and self.hit_process.poll() is None:
            self.hit_process.terminate()
            try:
                self.hit_process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                self.hit_process.kill()
                self.hit_process.communicate(timeout=3)
        try:
            subprocess.run(['import', '-window', 'root', str(self.directory / 'failure.png')],
                           capture_output=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass  # Preserve the original failure if screenshot capture is unavailable.
        self.quit()

    def activate(self, app):
        super().activate(app)
        dispatch = self.pointer_controls.dispatch

        def record_pointer(message):
            self.native_pointer_events.append(dict(message))
            dispatch(message)
        self.pointer_controls.dispatch = record_pointer
        self.view.connect('load-changed', self.loaded)
        self.view.connect('run-file-chooser', self.file_chooser)
        self.view.connect('web-process-terminated', lambda _view, reason: self.fail(f'Web process terminated: {reason}'))
        self.GLib.timeout_add(100, self.tick)

    def loaded(self, _view, event):
        if event == self.WebKit.LoadEvent.FINISHED:
            self.loads += 1
            print(f'WebKit document finished: {self.loads}', flush=True)
        if event != self.WebKit.LoadEvent.FINISHED or self.phase != 'loading':
            return
        self.phase = 'suite'
        source = (ROOT / 'test/GtkPanel.mjs').read_text().replace('export async function', 'async function')
        source += (ROOT / 'test/GuiControlGeometry.mjs').read_text().replace('export ', '')
        geometry = json.loads((ROOT / 'test/data/control-geometry.json').read_text())
        outputs = json.loads((ROOT / 'test/data/output-jacks.json').read_text())
        source += f'\nconst result = await checkPanel({str(self.write).lower()}, {json.dumps(outputs)});'
        source += f'\nresult.geometry = await checkControlGeometry({json.dumps(geometry)});return result;'

        def finished(view, result, _data):
            try:
                value = view.call_async_javascript_function_finish(result)
                self.result = json.loads(value.to_json(0))
                print(f'PASS original main-panel control bounds in WebKit: {self.result["geometry"]}', flush=True)
                self.start_native_footer()
            except Exception as error:
                self.fail(error)
        self.view.call_async_javascript_function(source, -1, None, None, None, None, finished, None)

    def focus_window(self):
        window = subprocess.run(['xdotool', 'search', '--onlyvisible', '--pid', str(os.getpid())],
                                capture_output=True, text=True, check=True, timeout=3).stdout.splitlines()[0]
        subprocess.run(['xdotool', 'windowfocus', window], check=True, timeout=3)
        return window

    def key(self, key, focus=True):
        if focus:
            self.focus_window()
        subprocess.run(['xdotool', 'key', '--clearmodifiers', key], check=True, timeout=3)

    def start_native_footer(self):
        self.phase = 'native-footer'
        script = '''(()=>{const node=document.querySelector('#footer-rate');
            window.__octaFooterValue=node.value;window.__octaFooterClick=false;
            node.addEventListener('click',event=>{window.__octaFooterClick=event.isTrusted;},{once:true});
            const box=node.getBoundingClientRect();return {x:box.x+box.width/2,y:box.y+box.height/2};})()'''

        def prepared(view, result, _data):
            try:
                point = json.loads(view.evaluate_javascript_finish(result).to_json(0))
                valid, bounds = self.view.compute_bounds(self.window)
                if not valid:
                    raise RuntimeError('Cannot locate the rate menu in the native window.')
                scale = self.view.get_zoom_level()
                subprocess.run(['xdotool', 'mousemove', '--window', self.focus_window(),
                                str(round(bounds.get_x() + point['x'] * scale)),
                                str(round(bounds.get_y() + point['y'] * scale)), 'click', '1'],
                               check=True, timeout=3)
                self.GLib.timeout_add(200, self.finish_native_footer)
            except Exception as error:
                self.fail(error)
        self.view.evaluate_javascript(script, -1, None, None, None, prepared, None)

    def finish_native_footer(self):
        self.key('Escape', focus=False)
        script = '''(()=>{const node=document.querySelector('#footer-rate'),root=document.documentElement;
            const panel=document.querySelector('#panel').getBoundingClientRect();
            const valid=window.__octaFooterClick && node.value===window.__octaFooterValue &&
                root.scrollWidth===root.clientWidth && root.scrollHeight===root.clientHeight &&
                scrollX===0 && scrollY===0 && panel.x===0 && panel.y===0;
            delete window.__octaFooterClick;delete window.__octaFooterValue;return valid;})()'''

        def checked(view, result, _data):
            try:
                if not json.loads(view.evaluate_javascript_finish(result).to_json(0)):
                    raise RuntimeError('The native rate menu changed the panel position, overflow or sample rate.')
                print('PASS trusted X11/WebKit rate menu opens and dismisses without scrolling or changing the rate.', flush=True)
                self.start_native_space()
            except Exception as error:
                self.fail(error)
        self.view.evaluate_javascript(script, -1, None, None, None, checked, None)
        return False

    def start_native_space(self):
        self.phase = 'native-space-focus'
        self.view.grab_focus()
        self.focus_window()
        self.GLib.timeout_add(100, self.focus_native_space)

    def focus_native_space(self):
        script = "(()=>{document.querySelector('#view-switch').focus();" \
                 "const focused=document.hasFocus()&&document.activeElement.id==='view-switch';" \
                 "if(focused){window.__octaSpaceTest={mixer:!document.querySelector('#mixer-view').hidden};" \
                 "document.addEventListener('keydown',event=>Object.assign(window.__octaSpaceTest," \
                 "{key:event.key,trusted:event.isTrusted,prevented:event.defaultPrevented}),{capture:true,once:true});}" \
                 "return focused;})();"

        def focused(view, result, _data):
            try:
                if not json.loads(view.evaluate_javascript_finish(result).to_json(0)):
                    self.GLib.timeout_add(100, self.focus_native_space)
                    return
                self.phase = 'native-space'
                self.key('space', focus=False)
                self.GLib.timeout_add(100, self.check_native_space)
            except Exception as error:
                self.fail(error)
        self.view.evaluate_javascript(script, -1, None, None, None, focused, None)
        return False

    def check_native_space(self):
        script = "(()=>{const result=window.__octaSpaceTest;delete window.__octaSpaceTest;" \
                 "return result.key===' '&&result.trusted&&result.prevented&&" \
                 "result.mixer===!document.querySelector('#mixer-view').hidden;})();"

        def checked(view, result, _data):
            try:
                if not json.loads(view.evaluate_javascript_finish(result).to_json(0)):
                    raise RuntimeError('Native Space did not preserve the main panel view.')
                print('PASS actual X11/WebKit Space is consumed without activating the focused view button.', flush=True)
                self.start_native_menus()
            except Exception as error:
                self.fail(error)
        self.view.evaluate_javascript(script, -1, None, None, None, checked, None)
        return False

    def start_native_menus(self):
        self.phase = 'native-menus'
        expanded = "document.querySelector('[data-menu][aria-expanded=true]')?.dataset.menu"
        focused = 'document.activeElement.dataset.menu'
        depth = 'document.querySelectorAll("#menus .menu").length'
        about = 'document.querySelector("#about").open'
        self.menu_steps = iter((
            ('F10', f'{focused}==="driver"&&{depth}===0'),
            ('Right', f'{focused}==="device"'),
            ('Down', f'{expanded}==="device"'),
            ('Left', f'{expanded}==="driver"'),
            ('Right', f'{expanded}==="device"'),
            ('i', f'{depth}===2'),
            ('Escape', f'{depth}===1'),
            ('Escape', f'{depth}===0&&{focused}==="device"'),
            ('Escape', 'document.activeElement.id==="view-switch"'),
            ('alt+d', f'{expanded}==="driver"'),
            ('a', about),
            ('alt+v', f'{about}&&{depth}===0'),
            ('Tab', 'document.activeElement.id==="show-port-warning"'),
            ('Tab', 'document.activeElement.matches("#about [data-close]")'),
            ('shift+Tab', 'document.activeElement.id==="show-port-warning"'),
            ('Return', f'!{about}'),
            ('alt+v', f'{expanded}==="device"'),
            ('i', f'{depth}===2'),
            ('Escape', f'{depth}===1'),
            ('Escape', f'{depth}===0&&{focused}==="device"'),
            ('F10', 'document.activeElement.id==="view-switch"'),
        ))
        script = 'document.querySelector("#view-switch").focus();' \
                 'window.__octaMenuTest=null;' \
                 'window.__octaMenuListener=e=>window.__octaMenuTest=e;' \
                 'document.addEventListener("keydown",window.__octaMenuListener,true);'

        def prepared(view, result, _data):
            try:
                view.evaluate_javascript_finish(result)
                self.next_native_menu()
            except Exception as error:
                self.fail(error)
        self.view.evaluate_javascript(script, -1, None, None, None, prepared, None)

    def next_native_menu(self):
        step = next(self.menu_steps, None)
        if step is None:
            script = 'document.removeEventListener("keydown",window.__octaMenuListener,true);' \
                     'delete window.__octaMenuListener;delete window.__octaMenuTest;'
            self.view.evaluate_javascript(script, -1, None, None, None, None, None)
            print('PASS actual X11/WebKit menu keys: F10, Alt+D/V, root/submenu arrows, '
                  'mnemonics, modal ownership, dialog Tab/default Enter and focus recovery (21 steps).', flush=True)
            self.start_native_dialogs()
            return
        key, self.menu_expected = step
        self.phase = f'native-menu-{key}'
        self.menu_status = None
        self.key(key, focus=False)
        self.GLib.timeout_add(100, self.check_native_menu)

    def check_native_menu(self):
        script = f'''({{passed:window.__octaMenuTest?.isTrusted===true&&({self.menu_expected}),
            key:window.__octaMenuTest?.key,shift:window.__octaMenuTest?.shiftKey,
            code:window.__octaMenuTest?.code,keyCode:window.__octaMenuTest?.keyCode,
            prevented:window.__octaMenuTest?.defaultPrevented,focus:document.activeElement.outerHTML}});'''

        def checked(view, result, _data):
            try:
                self.menu_status = json.loads(view.evaluate_javascript_finish(result).to_json(0))
                if self.menu_status['passed']:
                    self.next_native_menu()
                else:
                    self.GLib.timeout_add(100, self.check_native_menu)
            except Exception as error:
                self.fail(error)
        self.view.evaluate_javascript(script, -1, None, None, None, checked, None)
        return False

    def caption_pointer(self, point, drag=False):
        valid, bounds = self.view.compute_bounds(self.window)
        if not valid:
            raise RuntimeError('Cannot locate the native dialog caption.')
        scale = self.view.get_scale_factor()
        position = lambda x, y: [str(round((bounds.get_x() + x) * scale)),
                                 str(round((bounds.get_y() + y) * scale))]
        window = self.focus_window()
        command = ['xdotool', 'mousemove', '--window', window, *position(point['x'], point['y'])]
        if drag:
            command += ['mousedown', '1', 'sleep', '0.1', 'mousemove', '--window', window,
                        *position(point['x'] + 40, point['y'] + 20), 'sleep', '0.1', 'mouseup', '1']
        else:
            command += ['click', '1']
        subprocess.run(command, check=True, timeout=3)

    def start_native_dialogs(self):
        self.phase = 'native-caption-drag'
        script = '''(()=>{const dialog=document.querySelector('#about');dialog.showModal();
            const r=dialog.getBoundingClientRect(),h=dialog.querySelector('.dialog-title').getBoundingClientRect();
            window.__octaCaptionTrusted=false;
            window.__octaCaptionEvents=[];
            window.__octaCaptionListener=e=>window.__octaCaptionEvents.push({type:e.type,
              trusted:e.isTrusted,x:e.clientX,y:e.clientY,button:e.button,buttons:e.buttons,target:e.target.className});
            for(const type of ['pointerdown','pointerup','click','close'])dialog.addEventListener(type,window.__octaCaptionListener);
            dialog.addEventListener('pointerdown',e=>{
              window.__octaCaptionTrusted=e.isTrusted;
              window.__octaCaptionPoint={x:e.clientX,y:e.clientY,target:e.target.className};
            },{once:true});
            dialog.addEventListener('click',e=>window.__octaCaptionClick=e.target.className,{once:true});
            return {x:h.x+40,y:h.y+h.height/2,left:r.left,top:r.top,
              expectedX:Math.min(r.left+40,innerWidth-r.width),expectedY:Math.min(r.top+20,innerHeight-r.height)};})();'''

        def prepared(view, result, _data):
            try:
                self.caption_original = json.loads(view.evaluate_javascript_finish(result).to_json(0))
                self.caption_pointer(self.caption_original, drag=True)
                self.GLib.timeout_add(100, self.check_native_caption)
            except Exception as error:
                self.fail(error)
        self.view.evaluate_javascript(script, -1, None, None, None, prepared, None)

    def check_native_caption(self):
        script = '''(()=>{const dialog=document.querySelector('#about'),r=dialog.getBoundingClientRect(),
            close=dialog.querySelector('.dialog-close').getBoundingClientRect();
            return {left:r.left,top:r.top,trusted:window.__octaCaptionTrusted,
              x:close.x+close.width/2,y:close.y+close.height/2};})();'''

        def checked(view, result, _data):
            try:
                point = json.loads(view.evaluate_javascript_finish(result).to_json(0))
                expected = self.caption_original
                if (not point['trusted'] or abs(point['left'] - expected['expectedX']) > 1 or
                        abs(point['top'] - expected['expectedY']) > 1):
                    self.GLib.timeout_add(100, self.check_native_caption)
                    return
                self.phase = 'native-caption-close'
                self.caption_pointer(point)
                self.GLib.timeout_add(100, self.reopen_native_caption)
            except Exception as error:
                self.fail(error)
        self.view.evaluate_javascript(script, -1, None, None, None, checked, None)
        return False

    def reopen_native_caption(self):
        original = self.caption_original
        script = f'''(()=>{{const dialog=document.querySelector('#about');if(dialog.open)return {{closed:false,
              pointer:window.__octaCaptionPoint,clicked:window.__octaCaptionClick,events:window.__octaCaptionEvents}};
            const previousStyle=dialog.style.cssText;
            dialog.showModal();const r=dialog.getBoundingClientRect();
            return {{closed:true,left:r.left,top:r.top,previousStyle,style:dialog.style.cssText,events:window.__octaCaptionEvents,
              centered:Math.abs(r.left-{original['left']})<1&&Math.abs(r.top-{original['top']})<1}};}})();'''

        def checked(view, result, _data):
            try:
                value = json.loads(view.evaluate_javascript_finish(result).to_json(0))
                self.caption_status = value
                if not value.get('closed'):
                    self.GLib.timeout_add(100, self.reopen_native_caption)
                    return
                clicked = any(event['type'] == 'click' and event['trusted'] and
                              'dialog-close' in event['target'].split() for event in value['events'])
                if not value['centered'] or not clicked:
                    raise RuntimeError(f'Caption close/reopen failed: {value}; original={original}; '
                                       f'surface transform={self.window.get_surface_transform()}')
                self.phase = 'native-caption-escape'
                self.key('Escape', focus=False)
                self.GLib.timeout_add(100, self.finish_native_caption)
            except Exception as error:
                self.fail(error)
        self.view.evaluate_javascript(script, -1, None, None, None, checked, None)
        return False

    def finish_native_caption(self):
        script = '(()=>{if(document.querySelector("#about").open)return false;' \
                 'for(const type of ["pointerdown","pointerup","click","close"])' \
                 'document.querySelector("#about").removeEventListener(type,window.__octaCaptionListener);' \
                 'delete window.__octaCaptionEvents;delete window.__octaCaptionListener;' \
                 'delete window.__octaCaptionTrusted;delete window.__octaCaptionPoint;' \
                 'delete window.__octaCaptionClick;return true;})();'

        def checked(view, result, _data):
            try:
                if not json.loads(view.evaluate_javascript_finish(result).to_json(0)):
                    self.GLib.timeout_add(100, self.finish_native_caption)
                    return
                print('PASS trusted X11/WebKit dialog caption drag/close, centered reopen and Escape.', flush=True)
                self.start_native_hits()
            except Exception as error:
                self.fail(error)
        self.view.evaluate_javascript(script, -1, None, None, None, checked, None)
        return False

    def start_native_hits(self):
        self.phase = 'native-pointer-prepare'
        self.hit_native_begin = len(self.native_pointer_events)
        self.hit_source = (ROOT / 'test/GtkControlHits.mjs').read_text().replace('export ', '')

        def prepared(view, result, _data):
            try:
                geometry = json.loads(view.call_async_javascript_function_finish(result).to_json(0))
                self.hit_window = self.focus_window()
                label, dial, dy = geometry['label'], geometry['dial'], geometry['dy']
                move = lambda p, dx=0, y=0: ('move', p['x'] + dx, p['y'] + y)
                quiet = 'state.requests.length===0&&!state.dragging'
                self.hit_steps = iter((
                    ('label', [move(label), ('down', 1), move(label, 20, -20), ('up', 1)], quiet),
                    ('right-down', [move(dial), ('down', 3)], f'{quiet}&&state.menus===0'),
                    ('right-up', [('up', 3)], f'{quiet}&&state.menus===1'),
                    ('right-outside', [('key', 'Escape'), move(dial), ('down', 3), move(label), ('up', 3)],
                     f'{quiet}&&state.menus===0'),
                    ('left-held', [('key', 'Escape'), move(dial), ('down', 1), ('down', 3), ('up', 3), ('up', 1)],
                     f'{quiet}&&state.menus===0'),
                    ('middle-held', [move(dial), ('down', 1), ('down', 2), ('up', 2), ('up', 1)],
                     f'{quiet}&&state.menus===0&&!state.captured'),
                    ('left-release', [move(dial), ('down', 1), ('down', 3), ('up', 1), move(dial, 3, -3), ('up', 3)],
                     f'{quiet}&&state.menus===1&&!state.captured'),
                    ('right-first', [('key', 'Escape'), move(dial), ('down', 3), ('down', 1), ('up', 1),
                                     move(dial, 3, -3), ('up', 3)], f'{quiet}&&state.menus===1&&!state.captured'),
                    ('keyboard-context', [('key', 'Escape'), ('key', 'shift+F10')], f'{quiet}&&state.menus===1&&'
                     'state.events.some(event=>event.type==="keydown"&&event.trusted&&event.key==="F10"&&event.shiftKey)'),
                    ('drag', [('key', 'Escape'), move(dial), ('down', 1), move(dial, 0, dy), ('up', 1)],
                     'state.requests.length===1&&!state.dragging&&!state.captured&&state.requests[0].kind==="preamp"&&'
                     f'state.requests[0].parameter==="sensitivity"&&state.requests[0].value==={geometry["expected"]}'),
                ))
                self.next_native_hit()
            except Exception as error:
                self.fail(error)
        source = self.hit_source + f'\nreturn prepareNativeControlHits({json.dumps(self.device.snapshot())});'
        self.view.call_async_javascript_function(source, -1, None, None, None, None, prepared, None)

    def hit_input(self, events):
        valid, bounds = self.view.compute_bounds(self.window)
        if not valid:
            raise RuntimeError('Cannot locate native pointer controls.')
        scale, window = self.view.get_scale_factor(), self.hit_window
        command = ['xdotool']
        for event in events:
            if event[0] == 'move':
                command += ['mousemove', '--window', window, str(round((bounds.get_x() + event[1]) * scale)),
                            str(round((bounds.get_y() + event[2]) * scale))]
            elif event[0] == 'key':
                command += ['key', '--clearmodifiers', event[1]]
            else:
                command += ['mousedown' if event[0] == 'down' else 'mouseup', str(event[1])]
            command += ['sleep', '0.05']
        self.hit_process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def next_native_hit(self):
        step = next(self.hit_steps, None)
        if step is None:
            def finished(view, result, _data):
                try:
                    view.call_async_javascript_function_finish(result)
                    print('PASS X11/GDK skin/label hits, release/chord/capture guards and modeled drag; '
                          'primary DOM events trusted, secondary DOM events match host receipts; '
                          'no parameter writes to hardware.', flush=True)
                    self.start_native_control() if self.write else self.start_save()
                except Exception as error:
                    self.fail(error)
            self.view.call_async_javascript_function(self.hit_source + '\nreturn await finishNativeControlHits();',
                                                     -1, None, None, None, None, finished, None)
            return
        name, events, self.hit_expected = step
        self.phase = f'native-pointer-{name}'
        self.hit_input(events)
        self.GLib.timeout_add(50, self.poll_native_hit_input)

    def poll_native_hit_input(self):
        try:
            if self.hit_process.poll() is None:
                return True
            _, error = self.hit_process.communicate(timeout=1)
            if self.hit_process.returncode:
                raise RuntimeError(f'Native pointer input failed: {error.decode(errors="replace")}')
            self.GLib.timeout_add(100, self.check_native_hit)
        except Exception as error:
            self.fail(error)
        return False

    def check_native_hit(self):
        script = self.hit_source + f'\nconst state=nativeControlHitState();return {{state,passed:({self.hit_expected})&&' \
                                  'state.events.some(event=>event.trusted)};'

        def checked(view, result, _data):
            try:
                self.menu_status = json.loads(view.call_async_javascript_function_finish(result).to_json(0))
                forwarded = [{key: event[key] for key in ('type', 'button', 'buttons', 'x', 'y',
                              'shiftKey', 'ctrlKey', 'altKey', 'metaKey')}
                             for event in self.menu_status['state']['events'] if not event['trusted']]
                receipts = self.native_pointer_events[self.hit_native_begin:]
                self.menu_status['host_events_match'] = forwarded == receipts
                if self.menu_status['passed'] and forwarded == receipts:
                    self.next_native_hit()
                else:
                    self.GLib.timeout_add(100, self.check_native_hit)
            except Exception as error:
                self.fail(error)
        self.view.call_async_javascript_function(script, -1, None, None, None, None, checked, None)
        return False

    def start_save(self):
        self.phase = 'download'
        self.key('ctrl+s')

    def start_load(self):
        prepared = run([self.device.bin_dir / 'octa-preset', 'prepare', self.saved,
                        self.directory / 'original.syx'])[11:-2]
        self.import_expected = {prepared}
        if prepared[2] == 3:
            retained = bytearray(prepared)
            retained[4] = bytes.fromhex(self.result['initial']['payload_hex'])[4]
            self.import_expected.add(bytes(retained))
        self.phase = 'open-focus'
        self.open_focused = False
        self.deadline = time.monotonic() + 45
        self.view.grab_focus()
        self.focus_window()
        self.GLib.timeout_add(100, self.open_load)

    def open_load(self):
        self.phase = 'open-dialog'
        self.key('ctrl+o', focus=False)
        return False

    def native_dialog(self, title=None, portal_only=False):
        pattern = '^' + re.escape(title) + '$' if title else '.'
        found = subprocess.run(['xdotool', 'search', '--onlyvisible', '--name', pattern],
                               capture_output=True, text=True, timeout=3)
        for identifier in found.stdout.splitlines():
            try:
                pid = subprocess.check_output(['xdotool', 'getwindowpid', identifier],
                                              text=True, stderr=subprocess.DEVNULL, timeout=3).strip()
                process = Path('/proc') / pid
                if process.stat().st_uid != os.getuid():
                    continue
                if portal_only and not (process / 'exe').resolve().name.startswith('xdg-desktop-portal-'):
                    continue
                if portal_only:
                    title = subprocess.check_output(['xdotool', 'getwindowname', identifier],
                                                    text=True, timeout=3).strip()
                    geometry = subprocess.check_output(['xdotool', 'getwindowgeometry', '--shell', identifier],
                                                       text=True, timeout=3)
                    dimensions = dict(line.split('=', 1) for line in geometry.splitlines() if '=' in line)
                    if (title == 'Save OCTA-CAPTURE settings' or
                            int(dimensions['WIDTH']) < 200 or int(dimensions['HEIGHT']) < 100):
                        continue
                return identifier
            except (OSError, subprocess.CalledProcessError):
                continue  # A chooser can disappear between X11 queries.
        return None

    def select_load_file(self):
        for window in self.Gtk.Window.list_toplevels():
            if window == self.window or not window.get_visible() or not window.get_modal() or not window.get_title():
                continue
            title = '^' + re.escape(window.get_title()) + '$'
            identifier = subprocess.run(['xdotool', 'search', '--all', '--onlyvisible', '--pid',
                                         str(os.getpid()), '--name', title],
                                        capture_output=True, text=True, check=True, timeout=3).stdout.splitlines()[0]
            if not self.open_focused:
                subprocess.run(['xdotool', 'windowfocus', identifier], check=True, timeout=3)
                self.open_focused = True
                self.phase = 'open-layout'
                self.GLib.timeout_add(100, self.open_layout_ready)
                return
            widgets, rows = [window], []
            while widgets:
                widget = widgets.pop()
                if isinstance(widget, (self.Gtk.Label, self.Gtk.Inscription)) and widget.get_mapped() and widget.get_text() == self.saved.name:
                    valid, bounds = widget.compute_bounds(window)
                    if valid and bounds.get_width() > 1 and bounds.get_height() > 1:
                        x = bounds.get_x() + bounds.get_width() / 2
                        y = bounds.get_y() + bounds.get_height() / 2
                        picked = window.pick(x, y, self.Gtk.PickFlags.DEFAULT)
                        visible = picked is not None and (picked == widget or widget.is_ancestor(picked))
                        if visible and 0 <= x < window.get_width() and 0 <= y < window.get_height():
                            rows.append((y, x))
                child = widget.get_first_child()
                while child:
                    widgets.append(child)
                    child = child.get_next_sibling()
            if not rows:
                return  # Wait for the native chooser's recent-file model.
            y, x = min(rows)
            subprocess.run(['xdotool', 'windowfocus', identifier, 'mousemove', '--window', identifier,
                            str(int(x)), str(int(y)), 'click', '--repeat', '2', '--delay', '100', '1'],
                           check=True, timeout=3)
            self.native_open_dialog = window
            self.native_open_identifier = identifier
            self.phase = 'open-accept'
            self.GLib.timeout_add(150, self.accept_load_file)
            return
        # Desktop portals own their chooser in a separate process. Exercise its
        # native location entry rather than modifying the HTML file input.
        identifier = self.native_dialog(portal_only=True)
        if identifier:
            subprocess.run(['xdotool', 'windowfocus', identifier, 'key', '--clearmodifiers', 'ctrl+l', 'ctrl+a'],
                           check=True, timeout=3)
            subprocess.run(['xdotool', 'type', '--clearmodifiers', '--delay', '1', '--', str(self.saved)],
                           check=True, timeout=5)
            self.native_open_identifier = identifier
            self.phase = 'portal-file-ready'

    def select_portal_file(self):
        import gi
        gi.require_version('Atspi', '2.0')
        from gi.repository import Atspi
        pid = int(subprocess.check_output(['xdotool', 'getwindowpid', self.native_open_identifier],
                                          text=True, timeout=3))
        desktop = Atspi.get_desktop(0)
        pending = [desktop.get_child_at_index(index) for index in range(desktop.get_child_count())]
        pending = [app for app in pending if app.get_process_id() == pid]
        examined = 0
        while pending and examined < 5000:
            node = pending.pop()
            examined += 1
            if node is None:
                continue
            states = node.get_state_set()
            if (node.get_role() == Atspi.Role.PUSH_BUTTON and node.get_name() in ('Select', 'Open') and
                    states.contains(Atspi.StateType.SHOWING) and states.contains(Atspi.StateType.SENSITIVE)):
                bounds = node.get_component_iface().get_extents(Atspi.CoordType.SCREEN)
                if bounds.width > 0 and bounds.height > 0:
                    subprocess.run(['xdotool', 'mousemove', '--sync', str(bounds.x + bounds.width // 2),
                                    str(bounds.y + bounds.height // 2), 'click', '1'], check=True, timeout=3)
                    self.phase = 'load-confirm'
                    return
            pending.extend(node.get_child_at_index(index) for index in range(node.get_child_count()))

    def open_layout_ready(self):
        self.phase = 'open-dialog'
        return False

    def accept_load_file(self):
        if self.native_open_dialog.get_visible():
            subprocess.run(['xdotool', 'windowfocus', self.native_open_identifier, 'key', 'Return'],
                           check=True, timeout=3)
        self.phase = 'load-confirm'
        return False

    def file_chooser(self, _view, request):
        self.open_request = request
        return False  # Keep WebKit's actual native chooser.

    def poll_load(self):
        self.pending = True
        script = "const error = document.querySelector('#toast');" \
                 "if (!error.hidden) return {error:error.textContent};" \
                 "const confirm = document.querySelector('#confirm');"
        if self.phase == 'load-confirm':
            script += "return {confirmed:confirm.open};"
        else:
            script += "return {ready:!confirm.open && !document.querySelector('#load-file').disabled};"

        def polled(view, result, _data):
            self.pending = False
            try:
                value = json.loads(view.call_async_javascript_function_finish(result).to_json(0)) or {}
                if value.get('error'):
                    raise RuntimeError('Native Open/import failed: ' + value['error'])
                if value.get('confirmed'):
                    if self.open_request is None or self.open_request.get_selected_files() != [str(self.saved)]:
                        raise RuntimeError('The native chooser did not select the newly saved settings file.')
                    self.phase = 'load-apply'
                    self.view.evaluate_javascript("document.querySelector('#confirm-yes').click();",
                                                  -1, None, None, None, None, None)
                elif value.get('ready'):
                    actual = bytes.fromhex(self.device.snapshot()['state']['payload_hex'])
                    if actual not in self.import_expected:
                        raise RuntimeError('Native Open/import did not match the prepared settings.')
                    print('PASS native Open chooser, saved settings import and complete hardware readback.', flush=True)
                    self.start_navigation()
            except Exception as error:
                self.fail(error)
        self.view.call_async_javascript_function(script, -1, None, None, None, None, polled, None)

    def start_navigation(self):
        self.phase = 'navigation'
        self.view.load_uri('https://example.invalid/should-be-blocked')

    def start_native_control(self):
        self.native_original = self.result['initial']['preamps'][0]['sensitivity_index']
        self.native_delta = 1 if self.native_original < 100 else -1
        self.phase = 'native-focus'
        self.view.grab_focus()
        self.focus_window()
        # Process X11/GTK FocusIn before setting the DOM slider focus.
        self.GLib.timeout_add(100, self.focus_native_dom)

    def focus_native_dom(self):
        script = "if(document.querySelector('#preamp-view').hidden)document.querySelector('#view-switch').click();" \
                 "document.querySelector('.preamp-strip .sensitivity').focus();" \
                 "({focused:document.hasFocus()&&document.activeElement.matches('.sensitivity')," \
                 "value:Number(document.activeElement.getAttribute('aria-valuenow'))});"

        def focused(view, result, _data):
            try:
                readiness = json.loads(view.evaluate_javascript_finish(result).to_json(0))
                if not readiness['focused'] or readiness['value'] != self.native_original:
                    self.GLib.timeout_add(100, self.focus_native_dom)
                    return
                self.phase = 'native-change'
                self.key('Up' if self.native_delta > 0 else 'Down', focus=False)
            except Exception as error:
                self.fail(error)
        self.view.evaluate_javascript(script, -1, None, None, None, focused, None)
        return False

    def native_progress(self):
        value = self.server.application.snapshot()['state']['preamps'][0]['sensitivity_index']
        changed = self.phase in ('native-change', 'native-wheel-change')
        expected = self.native_original + self.native_delta if changed else self.native_original
        if value != expected:
            return
        if self.phase == 'native-change':
            self.phase = 'native-restore'
            self.key('Down' if self.native_delta > 0 else 'Up', focus=False)
            return
        if self.phase == 'native-restore':
            self.phase = 'native-wheel-focus'
            self.focus_native_wheel()
            return
        if self.phase == 'native-wheel-change':
            self.phase = 'native-wheel-restore'
            self.native_wheel(-self.native_delta)
            return
        self.phase = 'native-wheel-complete'

        def checked(view, result, _data):
            try:
                event = json.loads(view.evaluate_javascript_finish(result).to_json(0))
                if not event['trusted'] or not event['prevented'] or not event['focused']:
                    raise RuntimeError(f'Native WebKit wheel event was not handled: {event}')
                print('PASS actual X11/WebKit wheel, focus, hardware change and restoration: '
                      f'deltaY={event["deltaY"]}, wheelDeltaY={event["wheelDeltaY"]}.', flush=True)
                self.finish_native_control()
            except Exception as error:
                self.fail(error)
        self.view.evaluate_javascript('({...window.__octaWheelTest,focused:document.activeElement.matches(".sensitivity")})',
                                      -1, None, None, None, checked, None)

    def native_wheel(self, delta):
        subprocess.run(['xdotool', 'click', '4' if delta > 0 else '5'], check=True, timeout=3)

    def focus_native_wheel(self):
        script = '(()=>{const node=document.querySelector(".preamp-strip .sensitivity"),r=node.getBoundingClientRect();' \
                 'return {x:r.x+r.width/2,y:r.y+r.height/2,value:Number(node.getAttribute("aria-valuenow"))};})();'

        def positioned(view, result, _data):
            try:
                point = json.loads(view.evaluate_javascript_finish(result).to_json(0))
                if point['value'] != self.native_original:
                    self.GLib.timeout_add(100, self.focus_native_wheel)
                    return
                valid, bounds = self.view.compute_bounds(self.window)
                if not valid:
                    raise RuntimeError('Cannot locate the native WebKit wheel target.')
                scale = self.view.get_scale_factor()
                subprocess.run(['xdotool', 'mousemove', '--window', self.focus_window(),
                                str(round((bounds.get_x() + point['x']) * scale)),
                                str(round((bounds.get_y() + point['y']) * scale))], check=True, timeout=3)
                self.phase = 'native-wheel-prepare'
                source = 'document.querySelector("#view-switch").focus();' \
                         'document.addEventListener("wheel",e=>window.__octaWheelTest=' \
                         '{trusted:e.isTrusted,prevented:e.defaultPrevented,deltaY:e.deltaY,wheelDeltaY:e.wheelDeltaY},{once:true});'
                self.view.evaluate_javascript(source, -1, None, None, None, prepared, None)
            except Exception as error:
                self.fail(error)

        def prepared(view, result, _data):
            try:
                view.evaluate_javascript_finish(result)
                self.phase = 'native-wheel-change'
                self.native_wheel(self.native_delta)
            except Exception as error:
                self.fail(error)
        self.view.evaluate_javascript(script, -1, None, None, None, positioned, None)
        return False

    def finish_native_control(self):
        self.phase = 'native-complete'

        def mixer(view, result, _data):
            try:
                if not json.loads(view.evaluate_javascript_finish(result).to_json(0)):
                    raise RuntimeError('Native control did not return to Direct Mixer.')
                print('PASS actual X11/WebKit key events, hardware change and restoration.', flush=True)
                self.start_save()
            except Exception as error:
                self.fail(error)
        self.view.evaluate_javascript("document.querySelector('#view-switch').click();"
                                      "!document.querySelector('#mixer-view').hidden;",
                                      -1, None, None, None, mixer, None)

    def tick(self):
        try:
            return self._tick()
        except Exception as error:
            self.fail(error)
            return False

    def _tick(self):
        if time.monotonic() > self.deadline:
            self.fail(f'GTK check timed out in {self.phase}. Last menu input: {getattr(self, "menu_status", None)}; '
                      f'caption: {getattr(self, "caption_status", None)}')
            return False
        if self.phase in ('native-change', 'native-restore', 'native-wheel-change', 'native-wheel-restore'):
            self.native_progress()
        if self.phase == 'download':
            identifier = self.native_dialog('Save OCTA-CAPTURE settings')
            if identifier:
                subprocess.run(['xdotool', 'windowfocus', identifier, 'key', 'ctrl+l', 'ctrl+a'], check=True, timeout=3)
                subprocess.run(['xdotool', 'type', '--clearmodifiers', '--delay', '1', '--', str(self.saved)],
                               check=True, timeout=5)
                subprocess.run(['xdotool', 'key', 'Return'], check=True, timeout=3)
                self.phase = 'saving'
        elif self.phase == 'saving' and self.saved.exists() and self.saved.stat().st_size == 1639:
            if not self.saved.read_bytes().startswith(b'OCTA-CAPTURE Control Panel Data\0Ver100\0'):
                self.fail('Invalid native settings download.')
                return False
            self.phase = 'saved'

            def inspect(view, result, _data):
                try:
                    value = json.loads(view.evaluate_javascript_finish(result).to_json(0))
                    if not value['connected'] or not value['mixer'] or self.loads != 1:
                        raise RuntimeError(f'Desktop lost its live view after Save: {value}; loads={self.loads}')
                    self.capture_saved_view()
                except Exception as error:
                    self.fail(error)
            self.view.evaluate_javascript('({connected:document.querySelector("#connection-message").hidden,'
                                          'mixer:!document.querySelector("#mixer-view").hidden})',
                                          -1, None, None, None, inspect, None)
        elif self.phase == 'open-dialog':
            self.select_load_file()
        elif self.phase == 'portal-file-ready':
            self.select_portal_file()
        elif self.phase in ('load-confirm', 'load-apply') and not self.pending:
            self.poll_load()
        elif self.phase == 'navigation' and not self.pending:
            self.pending = True
            self.GLib.timeout_add(500, self.check_navigation)
        return not self.errors

    def capture_saved_view(self):
        def captured(view, result, _data):
            try:
                texture = view.get_snapshot_finish(result)
                if not texture.save_to_png(str(self.directory / 'gtk.png')):
                    raise RuntimeError('Could not save the native WebKit snapshot.')
                self.start_load() if self.write else self.start_navigation()
            except Exception as error:
                self.fail(error)
        self.view.get_snapshot(self.WebKit.SnapshotRegion.VISIBLE, self.WebKit.SnapshotOptions.NONE,
                               None, captured, None)

    def check_navigation(self):
        if not self.view.get_uri().startswith(self.server.origin + '/'):
            self.fail('External navigation was not blocked.')
        else:
            self.phase = 'complete'
            script = "document.querySelector('[data-menu=driver]').click();" \
                     "[...document.querySelectorAll('#menus button')].find(node=>node.textContent.startsWith('Exit')).click();"
            self.view.evaluate_javascript(script, -1, None, None, None, None, None)
            self.GLib.timeout_add_seconds(3, lambda: self.fail('Driver Exit did not close the desktop window.'))
        return False


def run(command):
    return subprocess.run(list(map(str, command)), capture_output=True, check=True, timeout=20).stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installed-prefix', type=Path, help='test installed Python modules, assets and executables')
    parser.add_argument('--bin-dir', type=Path,
                        default=installed_prefix.resolve() / 'bin' if installed_prefix else ROOT / 'build/release')
    parser.add_argument('--write', action='store_true', help='change sensitivity and reimport native Save, then restore the full raw state')
    args = parser.parse_args()
    parent = ROOT / 'build/gui'
    parent.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='gtk-check-', dir=parent))
    application = Application(args.bin_dir, directory / 'recovery')
    before = application.snapshot()['state']
    body = bytes([1, 0, 0, 0]) + bytes.fromhex(before['payload_hex'])
    recovery = directory / 'original.syx'
    recovery.write_bytes(bytes.fromhex('f0411000004d12') + body + bytes([-sum(body) % 128, 247]))
    recovery.chmod(0o600)
    server = LocalServer(application, port=0)
    try:
        checker = Check(application, server, directory, args.write)
        status = checker.run()
        if status or checker.errors or checker.phase != 'complete':
            raise RuntimeError(f'GTK check failed: {checker.errors}; phase={checker.phase}; status={status}')
        run([args.bin_dir / 'octa-preset', 'inspect', checker.saved])
    finally:
        application.close()
        server.server_close()
        if args.write:
            run([args.bin_dir / 'octa-preset', 'restore', recovery])
        after = json.loads(run([args.bin_dir / 'octa-state']))
        if after != before:
            raise RuntimeError(f'Final state mismatch. Recovery snapshot: {recovery}')
    print(f'PASS GTK/WebKit live panel, controls, dialogs, native Save, navigation policy and raw preservation: {directory}')


if __name__ == '__main__':
    main()
