#!/usr/bin/env python3
"""Check every checkout against its recorded immutable source SHA."""
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / 'upstream.lock.json').read_text())
failed = []
for item in manifest['repositories']:
    path = root / item['path']
    try:
        got = subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip()
        top = subprocess.check_output(['git', '-C', str(path), 'rev-parse', '--show-toplevel'], text=True).strip()
        ok = got == item['commit'] and Path(top).resolve() == path.resolve()
    except (subprocess.CalledProcessError, FileNotFoundError):
        ok = False
    print(('OK   ' if ok else 'FAIL ') + item['name'])
    if not ok:
        failed.append(item['name'])
if failed:
    sys.exit('Initialize sources: git submodule update --init --recursive --depth 1')
