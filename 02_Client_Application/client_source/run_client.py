#!/usr/bin/env python3
"""Launch the SynCanvas desktop client."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for source_dir in (ROOT / "Client", ROOT / "Server", ROOT):
    if str(source_dir) not in sys.path:
        sys.path.insert(0, str(source_dir))

from client.main import main

if __name__ == "__main__":
    raise SystemExit(main())
