#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
git submodule update --init --recursive
python3 scripts/apply-runtime-patches.py
"${PYTHON:-python3.12}" -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
