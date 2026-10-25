#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Prepare an experimental base/EXP ALSA configuration as a regular user."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gui.aggregate import main

if __name__ == '__main__':
    raise SystemExit(main())
