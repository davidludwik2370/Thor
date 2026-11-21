#!/usr/bin/env python3
"""Prepare native runtime views through the maintained Rust compiler."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--odin',default=os.environ.get('THOR_ODIN','odin'))
    parser.add_argument('--compiler',type=Path,default=ROOT/'thor')
    options=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='thor-runtime-prepare-') as directory:
        temporary=Path(directory)
        source=temporary/'main.thor'
        source.write_text('package main\nimport _ "thor:basic"\nmain::proc(){}\n')
        output=temporary/'generated'
        result=subprocess.run([str(options.compiler.resolve()),'check',str(source),
            '--odin',options.odin,'--output-dir',str(output),
            '--cache-dir',str(ROOT/'.thor-cache'),'-thread-count:1'],
            env={**os.environ,'THOR_ROOT':str(ROOT)})
        if result.returncode:return result.returncode
        manifest=json.loads((output/'thor-manifest.json').read_text())
        for flag in manifest['odin_flags']:
            if flag.startswith('-collection:thor='):print(flag.split('=',1)[1])
    return 0


if __name__=='__main__':raise SystemExit(main())
