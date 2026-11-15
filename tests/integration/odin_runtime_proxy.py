#!/usr/bin/env python3
"""Translate hard-coded runtime compiler calls in integration test programs."""
import os
from pathlib import Path
import sys


def main():
    native = os.environ['THOR_INTEGRATION_ODIN']
    arguments = sys.argv[1:]
    if not arguments or arguments[0] not in {'build', 'run', 'test', 'check'}:
        os.execv(native, [native, *arguments])
    compiler = os.environ['THOR_INTEGRATION_THOR']
    if len(arguments) > 1 and not arguments[1].startswith('-'):
        source = Path(arguments[1])
        if source.suffix == '.odin' and not source.exists() and source.with_suffix('.thor').exists():
            arguments[1] = str(source.with_suffix('.thor'))
    boundary = arguments.index('--') if '--' in arguments else len(arguments)
    arguments[boundary:boundary] = ['--odin-compatible', '--odin', native]
    os.execv(compiler, [compiler, *arguments])


if __name__ == '__main__':
    main()
