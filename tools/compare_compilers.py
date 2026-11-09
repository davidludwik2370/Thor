#!/usr/bin/env python3
"""Black-box Python/Rust differential checks; an unported feature is not a pass.

Generated files, parser/evaluation caches and binaries stay in temporary storage.
The report is suitable for tracking a native port without importing either engine.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Observation:
    status: int | None
    stdout: str
    diagnostics: list
    stderr: str
    seconds: float
    timeout: bool = False
    toolchain_failure: bool = False
    stderr_residue: str | None = None


def split_diagnostic_stream(text):
    """Separate the compiler's JSON envelope from any program/backend stderr."""
    leading = len(text)-len(text.lstrip())
    try:
        packet, end = json.JSONDecoder().raw_decode(text, leading)
    except ValueError:
        return {'errors': []}, text
    if not (isinstance(packet, dict) and isinstance(packet.get('error_count'), int)
            and isinstance(packet.get('errors'), list)
            and all(isinstance(error, dict) and isinstance(error.get('msgs', []), list)
                    for error in packet['errors'])):
        return {'errors': []}, text
    residue = text[end:]
    if residue.startswith('\r\n'):
        residue = residue[2:]
    elif residue.startswith('\n'):
        residue = residue[1:]
    return packet, residue


def canonical_diagnostics(packet):
    """Keep public error content/locations; discard scratch/backend-only fields."""
    output = []
    for error in packet.get('errors', []):
        position = error.get('pos') or {}
        messages = error.get('msgs', [])
        # Odin schedules instantiation/caller notes nondeterministically. Keep
        # the primary message and compare the remaining lines as a multiset;
        # blank separators carry no diagnostic content. Raw output stays in the
        # report for inspection. No message, source span or duplicate is removed.
        messages = messages[:1] + sorted(message for message in messages[1:] if message.strip())
        output.append({
            'severity': error.get('type'),
            'messages': messages,
            'file': position.get('file'),
            'line': position.get('line'),
            'column': position.get('column'),
        })
    return output


def observe(compiler, entry, output, cache, *, timeout, command='check', flags=()):
    argv = [str(compiler), command, str(entry), '--output-dir', str(output),
            '--cache-dir', str(cache), '-json-errors', *flags]
    started = time.monotonic()
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        def decoded(data):
            return data.decode(errors='replace') if isinstance(data, bytes) else data or ''
        return Observation(None, decoded(error.stdout), [], decoded(error.stderr),
                           time.monotonic()-started, True)
    packet, residue = split_diagnostic_stream(completed.stderr)
    toolchain_failure=any(error.get('stage')=='toolchain' or any(
        'Odin compiler terminated by' in message for message in error.get('msgs', []))
        for error in packet.get('errors', []))
    return Observation(completed.returncode, completed.stdout,
                       canonical_diagnostics(packet), completed.stderr,
                       time.monotonic()-started,toolchain_failure=toolchain_failure,
                       stderr_residue=residue)


def classify(python, rust):
    if python.timeout or rust.timeout:
        return 'timeout'
    if python.toolchain_failure or rust.toolchain_failure:
        return 'mismatch'
    if rust.status == 3 and any(message.startswith('Rust migration:')
                                for error in rust.diagnostics for message in error.get('messages', [])):
        return 'unported'
    if python.status is not None and python.status < 0 or rust.status is not None and rust.status < 0:
        return 'mismatch'
    if python.status != rust.status:
        return 'mismatch'
    if python.diagnostics != rust.diagnostics:
        return 'mismatch'
    if python.stdout != rust.stdout:
        return 'mismatch'
    python_stderr = python.stderr if python.stderr_residue is None else python.stderr_residue
    rust_stderr = rust.stderr if rust.stderr_residue is None else rust.stderr_residue
    if python_stderr != rust_stderr:
        return 'mismatch'
    return 'match'


def source_snapshot(entry):
    paths = [entry] if entry.is_file() else sorted(
        path for path in entry.rglob('*') if path.suffix in {'.thor', '.odin'}
        and not any(part.startswith('.') for part in path.relative_to(entry).parts))
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def comparison_flags(flags):
    # Odin's parallel checker can repeat instantiation notes nondeterministically.
    # Preserve every note and compare a deterministic native diagnostic stream.
    return list(flags) if any(flag.startswith('-thread-count:') for flag in flags) else [
        '-thread-count:1', *flags]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sources', nargs='*', type=Path,
                        help='files/packages to compare; defaults to regression fixtures')
    parser.add_argument('--python', type=Path, default=ROOT/'thor')
    parser.add_argument('--rust', type=Path, default=ROOT/'rust/target/release/thor-rust')
    parser.add_argument('--timeout', type=float, default=45)
    parser.add_argument('--command', choices=['check', 'run'], default='check')
    parser.add_argument('--flag', action='append', default=[])
    parser.add_argument('--report', type=Path)
    parser.add_argument('--report-only', action='store_true',
                        help='record migration gaps without returning a failing exit status')
    options = parser.parse_args(argv)
    flags = comparison_flags(options.flag)
    entries = [entry.resolve() for entry in options.sources]
    if not entries:
        entries = sorted((ROOT/'tests/fixtures').glob('*/main.thor'))
    if not options.rust.is_file():
        parser.error('build the Rust compiler first: cargo build --release --manifest-path rust/Cargo.toml')
    rows = []
    with tempfile.TemporaryDirectory(prefix='thor-engine-comparison-') as temporary:
        scratch = Path(temporary)
        for index, entry in enumerate(entries):
            before = source_snapshot(entry)
            python = observe(options.python.resolve(), entry, scratch/str(index)/'python',
                             scratch/str(index)/'cache', timeout=options.timeout,
                             command=options.command, flags=flags)
            rust = observe(options.rust.resolve(), entry, scratch/str(index)/'rust',
                           scratch/str(index)/'cache', timeout=options.timeout,
                           command=options.command, flags=flags)
            status = classify(python, rust)
            if source_snapshot(entry) != before:
                raise RuntimeError(f'compiler changed original source: {entry}')
            row = {'source': str(entry), 'comparison': status,
                   'python': asdict(python), 'rust': asdict(rust)}
            rows.append(row)
            print(f'{status:9} {entry}', flush=True)
    counts = {kind: sum(row['comparison'] == kind for row in rows)
              for kind in ['match', 'mismatch', 'unported', 'timeout']}
    report = {'schema_version': 1, 'complete': counts['match'] == len(rows), 'odin_flags':flags,
              'counts': counts, 'cases': rows}
    if options.report:
        options.report.parent.mkdir(parents=True, exist_ok=True)
        options.report.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(counts))
    return 0 if options.report_only or report['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
