#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Launch the local OCTA-CAPTURE control panel without elevated privileges."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gui.server import main

if __name__ == '__main__':
    main()
