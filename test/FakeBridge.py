#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Test-only line-protocol peer. Never selected by the production GUI."""

import json
import sys


def emit(value):
    print(json.dumps(value), flush=True)


emit({'event': 'ready'})
for line in sys.stdin:
    serial, operation, *arguments = line.split()
    if sys.argv[1] == 'eof':
        break
    identifier = int(serial) + (1 if sys.argv[1] == 'bad-id' else 0)
    if sys.argv[1] == 'auto-sense':
        channels = 1 if operation == 'state' else 0
        emit({'event': 'auto-sense', 'channels': channels, 'active': bool(channels)})
    if sys.argv[1] == 'auto-sense-bad' and operation == 'state':
        emit({'event': 'auto-sense', 'channels': 1, 'active': False})
    if operation == 'meters' and arguments == ['1']:
        emit({'event': 'meters', 'input_levels': [0] * 8, 'input_db': [-96] * 8})
    emit({'id': identifier, 'ok': True,
          'state': {'operation': operation, 'values': list(map(int, arguments))}})
