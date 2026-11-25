#!/usr/bin/env python3
"""Run maintained compiler tests, with the frozen reference explicitly opt-in.

The inventory lists historical implementation tests separately. Native mode
actively rejects attempts to execute the Python compiler or its lowering API.
"""
import argparse
import contextlib
import importlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def flatten(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from flatten(test)
        else:
            yield test


def inventory():
    return json.loads((ROOT/'tests/fixtures/python-reference-tests.json').read_text())


class ProgressResult(unittest.TextTestResult):
    def startTest(self, test):
        super().startTest(test)
        self.current_start = time.monotonic()

    def stopTest(self, test):
        super().stopTest(test)
        if not hasattr(self, 'timings'):
            self.timings = {}
        self.timings[test.id()] = time.monotonic()-self.current_start

    def addError(self, test, error):
        super().addError(test, error)
        self.stream.writeln(self._exc_info_to_string(error, test))

    def addFailure(self, test, error):
        super().addFailure(test, error)
        self.stream.writeln(self._exc_info_to_string(error, test))


def archive_reason(test, policy):
    module = test.__class__.__module__.removeprefix('tests.')
    if module in policy['modules']:
        return policy['modules'][module]
    for suffix, reason in policy['tests'].items():
        if test.id().removeprefix('tests.') == suffix:
            return reason
    if Path(str(getattr(test, 'compiler', ''))).name == 'thor-python':
        return 'Explicit frozen-Python variant of independently tested native fixtures.'
    return None


@contextlib.contextmanager
def native_only():
    from thorlang.driver import Project
    from thorlang.compiler import Source
    original = subprocess.Popen

    def forbidden(*args, **kwargs):
        raise RuntimeError('Native test attempted to execute the frozen Python compiler; '
                           'port its verdict or select the explicit reference suite.')

    def popen(args, *positional, **kwargs):
        if not isinstance(args, str):
            if any(Path(str(arg)).name == 'thor-python' for arg in list(args)[:4]):
                forbidden()
        return original(args, *positional, **kwargs)

    with patch.object(Project, '__init__', forbidden), \
            patch.object(Source, 'transpile', forbidden), \
            patch.object(subprocess, 'Popen', popen):
        yield


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', action='store_true', help='also run historical Python implementation checks')
    parser.add_argument('--match', default='.*', help='regular expression over full test names')
    parser.add_argument('--list', action='store_true', help='list selected and archived tests without running them')
    parser.add_argument('--output', type=Path, help='write a machine-readable final result')
    options = parser.parse_args()
    os.environ['THOR_TEST_PYTHON_REFERENCE'] = '1' if options.reference else '0'
    policy = inventory()
    tests = {}
    for path in sorted((ROOT/'tests').glob('test_*.py')):
        module = importlib.import_module('tests.'+path.stem)
        for test in flatten(unittest.defaultTestLoader.loadTestsFromModule(module)):
            tests.setdefault(test.id(), test)
    selected, archived = [], []
    for name, test in tests.items():
        if not re.search(options.match, name):
            continue
        reason = archive_reason(test, policy)
        if reason and not options.reference:
            archived.append({'test': name, 'reason': reason})
        else:
            selected.append(test)
    if options.list:
        print(json.dumps({'selected': [test.id() for test in selected], 'archived': archived}, indent=2))
        return 0
    from tools.storage import prune
    cleanup=prune(ROOT,apply=True)
    if cleanup['removed']:print(f"Removed {len(cleanup['removed'])} obsolete development artifacts.",flush=True)
    print(f'Running {len(selected)} tests; {len(archived)} historical implementation tests '
          'are available with --reference.', flush=True)
    started = time.monotonic()
    with contextlib.nullcontext() if options.reference else native_only():
        result = unittest.TextTestRunner(verbosity=2, resultclass=ProgressResult).run(unittest.TestSuite(selected))
    if result.wasSuccessful():prune(ROOT,apply=True)
    if options.output:
        packet = {'mode': 'reference' if options.reference else 'native',
                  'tests': result.testsRun, 'seconds': time.monotonic()-started,
                  'selected': [test.id() for test in selected],
                  'test_seconds': getattr(result, 'timings', {}),
                  'success': result.wasSuccessful(), 'archived': archived,
                  'skipped': [{'test': test.id(), 'reason': reason} for test, reason in result.skipped],
                  'failures': [{'test': test.id(), 'detail': detail} for test, detail in result.failures],
                  'errors': [{'test': test.id(), 'detail': detail} for test, detail in result.errors]}
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(json.dumps(packet, indent=2)+'\n')
    return not result.wasSuccessful()


if __name__ == '__main__':
    raise SystemExit(main())
