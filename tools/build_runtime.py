#!/usr/bin/env python3
"""Prepare the installation's common library output before building projects."""
import argparse
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from thorlang.driver import Project,frontend_binary
from thorlang.shared import Runtime


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--odin',default='odin')
    options=parser.parse_args()
    frontend,_=frontend_binary(options.odin)
    project=Project(ROOT/'runtime/basic',frontend,[],use_shared=False,cache_directory=ROOT/'.thor-cache')
    runtime=Runtime(project,ROOT)
    print(runtime.prepare(with_basic=False))
    if runtime.eligible(project.sources[ROOT/'runtime/basic/basic.thor']):print(runtime.prepare())


if __name__=='__main__':main()
