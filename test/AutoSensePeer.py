# SPDX-License-Identifier: GPL-2.0-only
"""Test-only interactive CLI peer; never selected by the real application."""

import json
import signal
import sys
import threading
import time

mode = sys.argv[1]
requested = threading.Event()
action = None


def stop(number, _frame):
    global action
    action = 'cancel' if number == signal.SIGTERM else 'finish'
    requested.set()


if mode == 'launch-error':
    sys.exit(1)
signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGUSR1, stop)
# A queued cancellation must not be sent before the prepared event.
time.sleep(0.05)
print(json.dumps({'event': 'auto-sense-prepared'}), flush=True)
if mode == 'malformed':
    print('not-json', flush=True)
else:
    print(json.dumps({'event': 'auto-sense-started'}), flush=True)
if not requested.wait(timeout=3):
    sys.exit(2)
if action == 'cancel':
    if mode != 'recovery-error':
        print('Original raw state restored and verified.', file=sys.stderr, flush=True)
    sys.exit(130)
print(json.dumps({'action': 'finish', 'notifications': 2, 'sensitivity_indices': [0] * 8}), flush=True)
