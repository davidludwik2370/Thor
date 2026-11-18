"""Real terminal diagnostics use severity colors without coloring JSON or pipes."""
import errno
import json
import os
import re
import select
import subprocess
import time
import unittest

from tests import test_rust_compiler as native


@unittest.skipUnless(os.name == 'posix', 'PTY integration checks require POSIX')
class DiagnosticColorTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp = native.RustCompilerTests.setUp

    def invoke(self, engine, flags=(), *, terminal=False, environment=None, command='check'):
        compiler = native.PYTHON if engine == 'python' else native.RUST
        args = [str(compiler), command, str(self.source), '--output-dir', str(self.root/engine), *flags]
        env = {**os.environ, 'TERM': 'xterm-256color', 'CLICOLOR': '1'}
        env.pop('NO_COLOR', None)
        env.pop('FORCE_COLOR', None)
        env.update(environment or {})
        if not terminal:
            result = subprocess.run(args, cwd=self.root, env=env, capture_output=True,
                                    text=True, timeout=60)
            return result.returncode, result.stderr
        import pty
        master, slave = pty.openpty()
        process = None
        try:
            process = subprocess.Popen(args, cwd=self.root, env=env,
                                       stdout=subprocess.DEVNULL, stderr=slave)
            os.close(slave)
            slave = None
            chunks = []
            deadline = time.monotonic()+60
            while True:
                ready, _, _ = select.select([master], [], [], max(0, deadline-time.monotonic()))
                if not ready:
                    self.fail('compiler did not finish its terminal diagnostic')
                try:
                    data = os.read(master, 65536)
                except OSError as error:
                    if error.errno == errno.EIO:
                        break
                    raise
                if not data:
                    break
                chunks.append(data)
            status = process.wait(timeout=5)
            return status, b''.join(chunks).decode().replace('\r\n', '\n')
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            if slave is not None:
                os.close(slave)
            os.close(master)

    def test_error_warning_and_early_parser_colors_preserve_plain_diagnostics(self):
        cases = [
            ('package main\nmain::proc(){/* 😀 */value:int="wrong";_=value}\n', [], 1, 'Error', '31'),
            ('package main\nmain::proc(){\tunused:=1;_=0}\n', ['--vet=unused'], 0, 'Warning', '33'),
            ('package main\nmain::proc(){value:=or_panic}\n', [], 1, 'Error', '31'),
        ]
        for source, flags, status, label, code in cases:
            self.source.write_text(source)
            for engine in ('python', 'rust'):
                with self.subTest(engine=engine, label=label, source=source):
                    plain_status, plain = self.invoke(engine, flags)
                    colored_status, colored = self.invoke(engine, flags, terminal=True)
                    self.assertEqual((plain_status, colored_status), (status, status), colored)
                    self.assertNotIn('\x1b[', plain)
                    self.assertIn(f'\x1b[{code}m{label}:\x1b[0m', colored)
                    self.assertRegex(colored, r'\):\n' + re.escape(f'\x1b[{code}m{label}:\x1b[0m'))
                    self.assertRegex(colored, '\x1b\\['+code+r'm\^+\x1b\[0m')
                    self.assertEqual(re.sub(r'\x1b\[[0-9;]*m', '', colored), plain)

    def test_terminal_color_opt_outs_and_terse_unix_positions(self):
        self.source.write_text('package main\nmain::proc(){value:int="wrong";_=value}\n')
        for engine in ('python', 'rust'):
            _, plain = self.invoke(engine)
            for environment in ({'NO_COLOR': '1'}, {'TERM': 'dumb'}, {'CLICOLOR': '0'}):
                with self.subTest(engine=engine, environment=environment):
                    status, output = self.invoke(engine, terminal=True, environment=environment)
                    self.assertEqual(status, 1, output)
                    self.assertEqual(output, plain)
            status, output = self.invoke(engine, ['-terse-errors', '-error-pos-style:unix'], terminal=True)
            self.assertEqual(status, 1, output)
            self.assertIn(':2:', output)
            self.assertIn('\x1b[31mError:\x1b[0m', output)
            self.assertRegex(output, r':2:\d+:\n' + re.escape('\x1b[31mError:\x1b[0m'))
            self.assertNotIn('^', output)

    def test_json_remains_machine_readable_in_a_terminal(self):
        for source, flags, status, severity in [
            ('package main\nmain::proc(){_=missing}\n', [], 1, 'error'),
            ('package main\nmain::proc(){unused:=1;_=0}\n', ['--vet=unused'], 0, 'warning'),
        ]:
            self.source.write_text(source)
            for engine in ('python', 'rust'):
                with self.subTest(engine=engine, severity=severity):
                    result, output = self.invoke(engine, ['-json-errors', *flags], terminal=True)
                    self.assertEqual(result, status, output)
                    self.assertNotIn('\x1b[', output)
                    packet = json.loads(output)
                    self.assertEqual(packet['errors'][0]['type'], severity)
                    self.assertEqual(packet['errors'][0]['pos']['file'], str(self.source))

    def test_runtime_stderr_keeps_its_original_text(self):
        self.source.write_text('package main\nimport "core:fmt"\nimport "core:os"\n'
                               'main::proc(){fmt.eprintln("Error: application text");os.exit(7)}\n')
        for engine in ('python', 'rust'):
            for flags in (['-debug'], native.UNCHECKED):
                with self.subTest(engine=engine, flags=flags):
                    status, output = self.invoke(engine, flags, command='run', terminal=True)
                    self.assertEqual(status, 7, output)
                    self.assertEqual(output, 'Error: application text\n')

    def test_vet_correction_underlines_use_the_edited_source(self):
        source = ('package main\nhelper::proc(unused_parameter:int)->int{return 1}\n'
                  'main::proc(){unused_value:=helper(2)}\n')
        for engine in ('python', 'rust'):
            self.source.write_text(source)
            status, output = self.invoke(engine, ['--apply'], command='vet', terminal=True)
            self.assertEqual(status, 0, output)
            self.assertIn('    helper::fn(unused_parameter:int)', output)
            self.assertIn('    main::proc(){unused_value:=helper(2)}', output)
            self.assertNotIn('helper::proc(', output)
            self.assertIn('\x1b[33mWarning:\x1b[0m', output)
