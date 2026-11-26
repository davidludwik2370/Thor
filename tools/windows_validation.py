#!/usr/bin/env python3
"""Run explicit Windows validation stages, retaining commands and results."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.storage import prune


def source_identity(root):
    manifest = json.loads((root / 'snapshot-manifest.json').read_text(encoding='utf-8'))
    digest = hashlib.sha256()
    for name, expected in sorted(manifest['files'].items()):
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()):
            raise RuntimeError(f'Snapshot path escapes the workspace: {name}')
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError(f'Snapshot hash mismatch: {name}')
        digest.update(name.encode('utf-8') + b'\0' + actual.encode('ascii') + b'\0')
    return digest.hexdigest()


class Validation:
    def __init__(self, root, stage):
        self.root, self.stage = root, stage
        self.output = root / 'windows-results'
        self.output.mkdir(exist_ok=True)
        self.records = []
        self.packet = {'stage': stage, 'root': str(root), 'source_sha256': source_identity(root),
            'platform': platform.platform(), 'python': sys.version, 'started': time.time(),
            'success': False, 'commands': self.records}
        self.write()

    def write(self):
        destination = self.output / (self.stage + '.json')
        temporary = destination.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(self.packet, indent=2) + '\n', encoding='utf-8')
        temporary.replace(destination)

    def command(self, name, args, *, cwd=None, timeout=900, expected=None):
        log = self.output / f'{self.stage}-{name}.log'
        record = {'name': name, 'argv': list(map(str, args)), 'cwd': str(cwd or self.root),
            'log': str(log), 'timeout_seconds': timeout}
        self.records.append(record)
        self.write()
        print(f'[{self.stage}] {name}', flush=True)
        started = time.monotonic()
        with log.open('wb') as stream:
            process = subprocess.Popen(record['argv'], cwd=cwd or self.root,
                stdout=stream, stderr=subprocess.STDOUT)
            record['pid'] = process.pid
            self.write()
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                # Clean up only the process tree launched for this recorded test.
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                    stdout=stream, stderr=subprocess.STDOUT, timeout=15)
                process.wait(timeout=15)
                record['timed_out'] = True
                code = process.returncode
        record.update(exit_code=code, seconds=time.monotonic()-started)
        text = log.read_text(encoding='utf-8', errors='replace')
        record['success'] = code == 0 and not record.get('timed_out')
        if expected is not None:
            record['expected_stdout'] = expected
            record['success'] = record['success'] and text.strip() == expected
        self.write()
        print(f'[{self.stage}] {name}: exit {code}, {record["seconds"]:.2f}s', flush=True)
        if not record['success']:
            print(text[-14000:], flush=True)
            raise RuntimeError(f'{name} failed; see {log}')
        return text

    def maintain(self, phase):
        record = {'phase': phase}
        self.packet.setdefault('maintenance', []).append(record)
        try:
            cleanup = prune(self.root, apply=True)
            record['removed'] = cleanup['removed']
        except Exception as error:
            record['error'] = str(error)
            raise
        finally:
            self.write()

    def run(self):
        self.maintain('before')
        try:
            self.run_stage()
        except BaseException as error:
            # Preserve the actual verdict before removing regenerable output.
            self.packet.update(success=False, error=str(error), finished=time.time())
            try:
                self.write()
            except Exception as report_error:
                self.packet['report_error'] = str(report_error)
            try:
                self.maintain('after')
            except Exception:
                # The maintenance record contains its error; keep the original
                # test failure/interrupt as the primary cause.
                pass
            raise
        else:
            try:
                self.maintain('after')
            except Exception as error:
                self.packet.update(success=False, error='Cleanup failed: '+str(error))
                self.write()
                raise

    def run_stage(self):
        stage = self.stage
        odin = os.environ.get('THOR_ODIN', 'odin')
        compiler = self.root / 'rust/target/release/thor-rust.exe'
        if stage == 'preflight':
            for name, argv in [('odin', [odin, 'version']), ('rustc', ['rustc', '-vV']),
                               ('cargo', ['cargo', '--version']), ('node', ['node', '--version'])]:
                self.command(name, argv, timeout=30)
        elif stage in ('build', 'build-rust'):
            self.command('rust', ['cargo', 'build', '--release', '--locked', '--manifest-path',
                'rust/Cargo.toml', '--bin', 'thor-rust', '--example', 'test_compiler_mock',
                '--example', 'integration_odin_proxy', '-j', '2'], timeout=1200)
            self.command('compiler-version', [compiler, 'version'], timeout=30)
            installed=self.root/'bin/thor.exe'
            installed.parent.mkdir(exist_ok=True)
            shutil.copy2(compiler,installed)
            self.command('native-launcher',[installed,'version'],timeout=30)
            if stage == 'build':
                self.command('tls', [sys.executable, 'tools/build_tls.py', '-thread-count:2'], timeout=180)
        elif stage == 'rust-units':
            self.command('rust', ['cargo', 'test', '--lib', '--locked', '--manifest-path',
                'rust/Cargo.toml', '-j', '2', '--', '--test-threads=2'], timeout=900)
        elif stage in ('tls-units', 'tls-unchecked'):
            args = [sys.executable, 'tools/build_tls.py', 'test', '-thread-count:2',
                    '-define:ODIN_TEST_THREADS=1']
            if stage == 'tls-unchecked':
                args += ['-disable-assert', '-no-bounds-check', '-no-type-assert']
            self.command('tls', args, timeout=300)
        elif stage == 'smoke':
            directory = self.output / 'smoke'
            for language in ('odin', 'thor'):
                folder = directory / language
                folder.mkdir(parents=True, exist_ok=True)
                source = folder / ('main.' + language)
                if language == 'odin':
                    source.write_text('package main\nimport "core:fmt"\nmain::proc(){fmt.println("native Windows OK")}\n', encoding='utf-8')
                    self.command('odin', [odin, 'run', source, '-file', '-out:' + str(folder / 'smoke.exe'), '-thread-count:2'], expected='native Windows OK')
                else:
                    source.write_text('''package main
import "core:fmt"
work::fn(n:int)->int{return n+2}
main::proc(){
 VALUE::#run work(4)
 values:>[1,2,3]
 text:=f"result {VALUE}, count {len(values)}"
 defer delete(text)
 fmt.println(text)
}
''', encoding='utf-8')
                    for name, flags in [('checked', ['-debug']), ('unchecked',
                        ['-o:speed', '-disable-assert', '-no-bounds-check', '-no-type-assert'])]:
                        self.command(name, [compiler, 'run', source, '--output-dir', directory / name,
                            '--track-allocations=off', *flags], expected='result 6, count 3')
        elif stage == 'acceptance':
            self.command('native-gate', [sys.executable, 'tools/test_compiler.py',
                '--output', self.output / 'native-gate.json'], timeout=7200)
        elif stage == 'allocation':
            self.command('native-gate', [sys.executable, 'tools/test_compiler.py', '--match',
                r'^tests\.test_no_alloc', '--output', self.output / 'allocation-gate.json'], timeout=1800)
        elif stage == 'windows-native':
            self.command('native', [sys.executable, '-m', 'unittest', 'tests.test_windows_native', '-v'], timeout=900)
        elif stage == 'debugger-full':
            self.command('dap', [sys.executable, '-m', 'unittest', 'tests.test_debugger',
                'tests.test_scoped_owners_debugger', '-v'], timeout=1800)
        elif stage == 'focused':
            self.command('native-gate', [sys.executable, 'tools/test_compiler.py', '--match',
                r'^tests\.(test_thor|test_rust_compiler|test_rust_native_evaluation|test_comptime_modes|test_scoped_owners)\.',
                '--output', self.output / 'focused-gate.json'], timeout=1800)
        elif stage == 'regressions':
            self.command('native', [sys.executable, '-m', 'unittest',
                'tests.test_thor.ThorTests.test_main_demo',
                'tests.test_rust_compiler.RustCompilerTests.test_runtime_index_failure_reports_original_source_even_when_launched_directly',
                'tests.test_rust_compiler.RustCompilerTests.test_format_contract_maps_support_views_and_native_path_spellings_to_the_field',
                '-v'], timeout=180)
        elif stage == 'tls-transport':
            self.command('lsp', [sys.executable, '-m', 'unittest', 'tests.test_hover_sizes_tls',
                'tests.test_hover_size_audit_tls', 'tests.test_slice_access_tls',
                'tests.test_slice_copy_hardening_tls', 'tests.test_tls', '-v'], timeout=1200)
        elif stage == 'tls-hover':
            self.command('lsp', [sys.executable, '-m', 'unittest', 'tests.test_hover_sizes_tls',
                'tests.test_hover_size_audit_tls', '-v'], timeout=180)
        elif stage == 'tls-regressions':
            tests = ['test_compile_time_parallel_map_fields_and_purity_diagnostic_recovery',
                'test_parallel_map_aliases_overrides_and_callback_variable_names',
                'test_parallel_map_and_result_type_query_hovers_and_completion',
                'test_parallel_map_inline_polymorphic_callback_fields',
                'test_parallel_map_result_errors_and_unsaved_callback_recovery',
                'test_parallel_map_standard_library_overloaded_callback_completion',
                'test_compiler_diagnostics_preserve_symlinked_editor_uri',
                'test_native_compiler_path_discovers_runtime_without_explicit_collection',
                'test_new_file_buffer_can_be_checked_before_first_save',
                'test_precision_formats_hover_original_tokens_and_unsaved_diagnostic_recovery']
            self.command('lsp', [sys.executable, '-m', 'unittest',
                *['tests.test_tls.TLSTests.' + name for name in tests],
                'tests.test_pmap_inputs_tls', 'tests.test_pmap_permissions_tls',
                'tests.test_pmap_specialization_tls', 'tests.test_native_paths', '-v'], timeout=300)
            self.command('mapping-runtime', [sys.executable, '-m', 'unittest',
                'tests.test_pmap.ParallelMapTests.test_polymorphic_callbacks_named_results_overrides_and_source_order',
                'tests.test_pmap_comptime.CompileTimeParallelMapTests.test_scalar_and_polymorphic_multi_result_callbacks',
                '-v'], timeout=180)
        elif stage == 'debugger':
            tests = ['test_breakpoint_and_step_over_original_thor_lines',
                'test_static_readonly_slice_literals_keep_source_breakpoints_and_values',
                'test_automatic_using_header_preserves_breakpoints_steps_and_arguments',
                'test_parallel_map_generic_callback_keeps_source_breakpoints_and_result_fields']
            self.command('dap', [sys.executable, '-m', 'unittest',
                *['tests.test_debugger.DebuggerTests.' + name for name in tests], '-v'], timeout=300)
        elif stage == 'editor-live':
            code = Path(os.environ['LOCALAPPDATA']) / 'Programs/Microsoft VS Code/Code.exe'
            for scope in ['hover-size', 'auto-closing', 'full']:
                self.command(scope, [sys.executable, 'tools/test_vscode.py', '--code', code,
                    '--scope', scope, '--timeout', '600'], timeout=660)
        elif stage == 'editor':
            self.command('npm-ci', ['npm.cmd', 'ci', '--ignore-scripts'], cwd=self.root / 'editors/vscode')
            self.command('node-tests', ['npm.cmd', 'test'], cwd=self.root / 'editors/vscode')
        else:
            raise ValueError(stage)
        self.packet.update(success=True, finished=time.time())
        self.write()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', required=True, choices=['preflight', 'build', 'build-rust', 'smoke',
        'rust-units', 'tls-units', 'tls-unchecked', 'focused', 'regressions', 'acceptance', 'tls-transport', 'tls-hover', 'tls-regressions', 'allocation', 'windows-native', 'debugger-full',
        'debugger', 'editor', 'editor-live'])
    options = parser.parse_args()
    if os.name != 'nt':
        parser.error('Run this tool on native Windows; it does not use WSL.')
    run = Validation(ROOT, options.stage)
    try:
        run.run()
    except Exception as error:
        run.packet.update(error=str(error), finished=time.time())
        run.write()
        print(str(error), file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
