#!/bin/sh
set -eu
cd "$(dirname "$0")/.."

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3.11 or newer is required. Install Python, then try again." >&2
    exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
    python3 -m venv .venv
fi

if ! .venv/bin/python -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    echo "Python 3.11 or newer is required." >&2
    exit 1
fi

if ! .venv/bin/python -c "import PySide6; import PIL" >/dev/null 2>&1; then
    echo "Installing SynCanvas dependencies..."
    .venv/bin/python -m pip install -r requirements.txt
fi

exec .venv/bin/python run_client.py
