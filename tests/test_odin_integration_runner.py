"""Resource bounds and persisted upstream comparison verdicts."""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests.integration import run_odin_projects as runner


class IntegrationRunnerTests(unittest.TestCase):
    def test_vetted_copies_exclude_managed_outputs_from_both_compilers(self):
        with tempfile.TemporaryDirectory(prefix='thor-integration-copy-') as directory:
            root=Path(directory);source=root/'odin/sample__project';source.mkdir(parents=True)
            migrated=root/'migrated/sample__project';migrated.mkdir(parents=True)
            (migrated/'main.thor').write_text('package main\nmain::proc(){}\n')
            for name in ('.thor-cache','.thor-build','.thor-rust-build'):
                cache=migrated/name;cache.mkdir();(cache/'generated.odin').write_text('generated')
            results=root/'results/sample__project';results.mkdir(parents=True)
            project={'repo':'sample/project','commit':'revision','commands':[],'vet':[]}
            args=SimpleNamespace(stage='vetted',thor=Path('/compiler'),odin='/odin',timeout=1,fresh=False)
            with patch.object(runner,'WORK',root), \
                    patch.object(runner.subprocess,'check_output',side_effect=['revision\n','version\n']):
                runner.run_locked(project,args,source,results)
            vetted=root/'vetted/sample__project'
            self.assertEqual((vetted/'main.thor').read_text(),(migrated/'main.thor').read_text())
            self.assertEqual(sorted(path.name for path in vetted.iterdir()),['main.thor'])

    def test_style_recipes_allow_findings_while_builds_keep_warning_policy(self):
        projects=json.loads(runner.MANIFEST.read_text())
        examples=next(project for project in projects if project['repo']=='odin-lang/examples')
        self.assertTrue(any('-warnings-as-errors' in command['args'] for command in examples['commands']))
        for project in projects:
            for recipe in project.get('vet',[]):
                self.assertNotIn('-warnings-as-errors',recipe,project['repo'])

    def test_verification_retains_the_recorded_vet_pass(self):
        with tempfile.TemporaryDirectory(prefix='thor-integration-verify-') as directory:
            root=Path(directory);source=root/'odin/sample__project';source.mkdir(parents=True)
            vetted=root/'vetted/sample__project';vetted.mkdir(parents=True)
            results=root/'results/sample__project';results.mkdir(parents=True)
            original={'baseline':[],'vet':[{'status':0,'proof':'retained'}]}
            (results/'report.json').write_text(json.dumps(original))
            project={'repo':'sample/project','commit':'revision','commands':[],'vet':[['{tree}']]}
            args=SimpleNamespace(stage='verify',thor=Path('/compiler'),odin='/odin',timeout=1,fresh=False)
            with patch.object(runner,'WORK',root), \
                    patch.object(runner.subprocess,'check_output',side_effect=['revision\n','version\n']), \
                    patch.object(runner,'execute',side_effect=AssertionError('verification invoked vet')):
                runner.run_locked(project,args,source,results)
            report=json.loads((results/'report.json').read_text())
            self.assertEqual(report['vet'],original['vet'])
            self.assertEqual(report['vetted'],[])

    def test_source_fixtures_preserve_literals_comments_and_line_structure(self):
        raw = b'package fixture\r\n// #file #directory\r\nvalue:="#file"\r\na:=#file; b:=#directory\r\n'
        native = Path('/native/example.odin')
        actual = runner.fixture_source(raw,native)
        self.assertEqual(actual.count(b'\r\n'),raw.count(b'\r\n'))
        self.assertIn(b'// #file #directory',actual)
        self.assertIn(b'"#file"',actual)
        self.assertTrue(actual.endswith(b'a:="/native/example.odin"; b:="/native"\r\n'))

    def test_unavailable_runtime_keeps_evidence_without_reusing_its_old_verdict(self):
        with tempfile.TemporaryDirectory(prefix='thor-integration-unavailable-') as directory:
            root=Path(directory);source=root/'odin/sample__project';source.mkdir(parents=True)
            (root/'vetted/sample__project').mkdir(parents=True)
            results=root/'results/sample__project';results.mkdir(parents=True)
            original={'baseline':[], 'vetted':[{'name':'graphics','status':1,
                'matches_baseline':False,'stdout':'/saved/stdout','stderr':'/saved/stderr'}]}
            (results/'report.json').write_text(json.dumps(original))
            project={'repo':'sample/project','commit':'revision','commands':[{
                'name':'graphics','args':['test','{tree}'],'skip_repeated_runtime':True,
                'runtime_note':'Unchanged native graphics initialization is intermittent.'}]}
            args=SimpleNamespace(stage='verify',thor=Path('/compiler'),odin='/odin',timeout=1,fresh=False)
            with patch.object(runner,'WORK',root), \
                    patch.object(runner.subprocess,'check_output',side_effect=['revision\n','version\n']), \
                    patch.object(runner,'execute',side_effect=AssertionError('unavailable runtime executed')):
                runner.run_locked(project,args,source,results)
            result=json.loads((results/'report.json').read_text())['vetted'][0]
            self.assertEqual(result['status'],'native-runtime-unavailable')
            self.assertEqual(result['previous_status'],1)
            self.assertEqual(result['stderr'],'/saved/stderr')
            self.assertNotIn('matches_baseline',result)

    def test_case_results_compare_equal_after_json_roundtrip(self):
        packet = {'total': 2, 'success': 1, 'packages': {
            'sample': [{'name': 'a', 'success': True}, {'name': 'b', 'success': False}]}}
        result = runner.test_result_summary(packet)
        self.assertEqual(json.loads(json.dumps(result)), result)
        self.assertEqual(result['cases'], [['sample', 'a', True], ['sample', 'b', False]])

    def test_native_failures_require_explicit_complete_case_comparison(self):
        failed = {'status': 1, 'test_results': {'cases': [['sample', 'a', False]]}}
        self.assertFalse(runner.usable_baseline(failed, {}))
        self.assertTrue(runner.usable_baseline(failed, {'compare_native_test_failures': True}))
        self.assertFalse(runner.usable_baseline({'status': -6}, {'compare_native_test_failures': True}))

    def test_vet_matches_native_target_defines_and_renamed_single_files(self):
        commands = [
            {'name':'native', 'args':['check','{tree}/main.odin','-file','-out:{artifact}']},
            {'name':'windows', 'args':['check','{tree}/main.odin','-file','-target:windows_amd64']},
            {'name':'defined', 'args':['check','{tree}/main.odin','-file','-define:MODE=2']},
            {'name':'tests', 'args':['test','{tree}/main.odin','-file']},
        ]
        values = {'tree':'/example'}
        for flags, name in [([], 'native'), (['-target:windows_amd64'], 'windows'),
                            (['-define:MODE=2'], 'defined'), (['-build-mode:test'], 'tests')]:
            with self.subTest(flags=flags):
                selected = runner.vet_baselines(['/example/main.thor','-file',*flags],commands,values)
                self.assertEqual([command['name'] for command in selected],[name])

    def test_vet_baseline_matching_ignores_runner_test_settings(self):
        commands = [{'name':'tests', 'args':['test','{tree}/tests',
            '-define:ODIN_TEST_FANCY=false', '-define:ODIN_TEST_THREADS=1',
            '-define:ODIN_TEST_JSON_REPORT=/tmp/results.json',
            '-define:ODIN_TEST_RANDOM_SEED=582019', '-define:MODE=2']}]
        selected = runner.vet_baselines(['/example/tests','-build-mode:test','-define:MODE=2'],
            commands,{'tree':'/example'})
        self.assertEqual(selected,commands)
        self.assertEqual(runner.vet_baselines(['/example/tests','-build-mode:test'],
            commands,{'tree':'/example'}),[])

    def test_process_memory_includes_descendants_and_excludes_other_processes(self):
        snapshot = 'PID PPID RSS\n10 1 100\n11 10 200\n12 11 300\n13 1 900\n'
        with patch.object(runner.subprocess, 'check_output', return_value=snapshot), \
                patch.object(runner, 'physical_footprint_kib', return_value=None):
            result = runner.process_tree_rss(10)
        self.assertEqual(result, 600)

    def test_compressed_process_memory_counts_towards_the_limit(self):
        snapshot = 'PID PPID RSS\n10 1 100\n11 10 200\n12 1 900\n'
        with patch.object(runner.subprocess, 'check_output', return_value=snapshot), \
                patch.object(runner, 'physical_footprint_kib', side_effect=lambda pid:{10:700,11:400}[pid]):
            result = runner.process_tree_rss(10)
        self.assertEqual(result,1100)

    @unittest.skipUnless(os.name == 'posix', 'Integration process groups use POSIX signals')
    def test_memory_limit_terminates_the_command_and_preserves_logs(self):
        with tempfile.TemporaryDirectory(prefix='thor-integration-resource-') as directory:
            path = Path(directory)
            environment = dict(os.environ, THOR_INTEGRATION_MAX_RSS_MB='32')
            result = runner.execute_locked([sys.executable, '-c',
                'import os,time; print("ready",flush=True); data=os.urandom(64*1024*1024); time.sleep(20)'],
                path, path/'memory', environment, 10)
            output = Path(result['stdout']).read_text()
        self.assertEqual(result['status'], 'memory-limit')
        self.assertGreater(result['peak_rss_mb'], 32)
        self.assertIn('ready', output)

    @unittest.skipUnless(os.name == 'posix', 'Integration process groups use POSIX signals')
    def test_memory_limit_also_stops_helpers_in_separate_process_groups(self):
        with tempfile.TemporaryDirectory(prefix='thor-integration-helper-') as directory:
            path = Path(directory)
            environment = dict(os.environ, THOR_INTEGRATION_MAX_RSS_MB='32')
            code = ('import os,subprocess,sys,time; '
                'child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(20)"],start_new_session=True); '
                'print(child.pid,flush=True); data=os.urandom(64*1024*1024); time.sleep(20)')
            result = runner.execute_locked([sys.executable, '-c', code],
                path, path/'helper', environment, 10)
            child = int(Path(result['stdout']).read_text().strip())
            snapshot = runner.subprocess.run(['ps', '-p', str(child), '-o', 'stat='],
                capture_output=True, text=True)
            running = snapshot.returncode == 0 and not snapshot.stdout.strip().startswith('Z')
        self.assertEqual(result['status'], 'memory-limit')
        self.assertFalse(running, snapshot.stdout)


if __name__ == '__main__':
    unittest.main()
