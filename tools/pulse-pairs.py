#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Configure native OCTA-CAPTURE desktop endpoints as a regular user."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gui.pulse_audio import main

if __name__ == '__main__':
    raise SystemExit(main())
