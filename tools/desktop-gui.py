#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Launch the OCTA-CAPTURE GTK control panel as a regular user."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gui.desktop import main

if __name__ == '__main__':
    sys.exit(main())
