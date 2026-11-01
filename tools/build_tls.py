#!/usr/bin/env python3
"""Build/check/test the local OLS fork against Thor's shared native parser."""
import argparse
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['build', 'check', 'test'], nargs='?', default='build')
    parser.add_argument('--odin', default=os.environ.get('THOR_ODIN', 'odin'))
    options, flags = parser.parse_known_args()
    directory = ROOT / 'tls' / 'bin'
    directory.mkdir(exist_ok=True)
    output = directory / ('tls-tests' if options.mode == 'test' else 'tls')
    if os.name == 'nt':
        output = output.with_suffix('.exe')
    command = [options.odin, options.mode, str(ROOT / 'tls' / ('tests' if options.mode == 'test' else 'src')),
               f'-collection:src={ROOT / "tls" / "src"}', f'-collection:thor={ROOT / "frontend"}',
               '-define:VERSION=thor-0.21.0+ols-0c9438f']
    if options.mode != 'check':
        command.extend(['-o:speed', f'-out:{output}'])
    if options.mode == 'test':
        command.extend(['-define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true', '-ignore-warnings'])
    command.extend(flags)
    env = {**os.environ, 'OLS_BUILTIN_FOLDER': str(ROOT / 'tls' / ('tests/builtin' if options.mode == 'test' else 'builtin'))}
    return subprocess.run(command, cwd=ROOT / 'tls', env=env).returncode


if __name__ == '__main__':
    raise SystemExit(main())
