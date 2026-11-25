#!/usr/bin/env python3
"""Verify the deliberately frozen Python compiler without importing it."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def differences(root=ROOT):
    recorded = json.loads((root/'docs/python-reference-frozen.json').read_text())['sha256']
    paths = [root/'thor-python', *sorted((root/'thorlang').rglob('*.py'))]
    actual = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in paths if path.is_file()}
    return sorted(name for name in recorded.keys() | actual.keys()
                  if recorded.get(name) != actual.get(name))


def main():
    changed = differences()
    if changed:
        print('Frozen Python reference changed:\n'+'\n'.join(changed))
        return 1
    print('Frozen Python reference matches python-reference-2026-11-21.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
