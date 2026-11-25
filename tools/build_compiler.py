#!/usr/bin/env python3
"""Build the maintained native compiler with bounded development retention."""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.storage import prune

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--debug',action='store_true',help='build the compiler with development line tables')
    options=parser.parse_args()
    prune(ROOT,apply=True)
    command=['cargo','build','--locked','--manifest-path',str(ROOT/'rust/Cargo.toml'),'--bin','thor-rust','-j','2']
    if not options.debug:command.append('--release')
    result=subprocess.run(command)
    prune(ROOT,apply=True)
    return result.returncode

if __name__=='__main__':raise SystemExit(main())
