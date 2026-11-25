"""Incomplete proofs are notices; definite issues and native vet keep enforcing.

Unsafe fixtures are compiled/checked only. Executable fixtures keep their verdicts
enabled with assertions, bounds checks and type assertions disabled.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from tests import test_rust_compiler as native

UNKNOWN='work::proc(callback:proc(v:[]int)){v~:=make([]int,2);callback(v)}\n'

class UnverifiedReportingTests(unittest.TestCase):
    setUpClass=classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp=native.RustCompilerTests.setUp

    def write(self,body=None):
        self.source.write_text(body or 'package main\nimport "core:fmt"\n'+UNKNOWN+'main::proc(){fmt.println("executed")}\n',newline='')

    def call(self,*flags,command='vet',status=0,json_output=False,env=None,vet=True):
        args=[str(native.RUST),command,str(self.app),'--output-dir',str(self.root/'generated'),
            '--comptime=required','-thread-count:1',*(['--vet=lifetime,leak']if vet else []),
            *(['-json-errors']if json_output else []),*flags]
        if command=='build':
            from tests.native_paths import EXE_SUFFIX
            args+=['-out:'+str(self.root/('program'+EXE_SUFFIX))]
        p=subprocess.run(args,cwd=self.root,capture_output=True,text=True,
            env={**os.environ,**(env or {})},timeout=90)
        self.assertEqual(p.returncode,status,p.stdout+p.stderr)
        return json.loads(p.stderr)if json_output else p

    def test_default_summary_is_nonfatal_and_has_no_source_diagnostic_clutter(self):
        self.write()
        result=self.call()
        self.assertIn('Unverified analysis: 1 finding at 1 source location.',result.stderr)
        self.assertIn('not confirmed bugs',result.stderr)
        self.assertIn('thor vet --unverified',result.stderr)
        self.assertNotIn('Unverified lifetime:',result.stderr)
        self.assertNotIn('Warning:',result.stderr)
        self.assertNotIn(str(self.source),result.stderr)

    def test_summary_show_and_error_retain_identical_json_evidence(self):
        self.write()
        packets=[]
        for mode in ['summary','show','error']:
            packet=self.call('--unverified='+mode,status=int(mode=='error'),json_output=True)
            self.assertEqual(packet['unverified']['mode'],mode)
            self.assertEqual(packet['unverified']['count'],1)
            self.assertEqual(packet['unverified']['locations'],1)
            self.assertEqual(packet['error_count'],int(mode=='error'))
            finding=packet['errors'][0]
            self.assertEqual(finding['type'],'error'if mode=='error'else'info')
            self.assertEqual(finding['certainty'],'unverified')
            self.assertTrue(finding['reason'])
            self.assertTrue(finding['help'])
            self.assertIn(finding['help'],finding['msgs'])
            self.assertNotIn('fix',finding)
            packets.append({k:v for k,v in finding.items()if k!='type'})
        self.assertEqual(packets[0],packets[1]);self.assertEqual(packets[0],packets[2])

    def test_bare_unverified_means_show_and_later_options_win(self):
        self.write()
        shown=self.call('--unverified')
        self.assertIn('Info: Unverified lifetime:',shown.stderr)
        self.assertNotIn('Warning:',shown.stderr)
        hidden=self.call('--unverified=error','--unverified=summary')
        self.assertNotIn('Unverified lifetime:',hidden.stderr)
        self.call('--unverified=summary','--unverified=error',status=1)

    def test_strict_mode_allows_execution_when_there_are_no_unverified_findings(self):
        self.write('package main\nimport "core:fmt"\nmain::proc(){p~:=new(int);p^=7;if p^!=7{panic("value")};fmt.println("verified execution")}\n')
        for flags in [[],native.UNCHECKED]:
            result=self.call('--unverified=error',*flags,command='run')
            self.assertEqual(result.stdout.strip(),'verified execution')
            self.assertNotIn('Unverified analysis',result.stderr)

    def test_all_checking_commands_support_the_policy_in_checked_and_release_builds(self):
        self.write()
        for command in ['vet','check','build','run']:
            for flags in [[],native.UNCHECKED if command in ('build','run') else native.UNCHECKED[1:]]:
                for mode in ['summary','show','error']:
                    with self.subTest(command=command,flags=flags,mode=mode):
                        result=self.call('--unverified='+mode,*flags,command=command,status=int(mode=='error'))
                        self.assertEqual('executed'in result.stdout,command=='run'and mode!='error')
        self.write('package main\nimport "core:testing"\n'+UNKNOWN+'@(test) valid::proc(t:^testing.T){_=t}\n')
        for mode in ['summary','show','error']:
            result=self.call('--unverified='+mode,command='test',status=int(mode=='error'))
            self.assertIn('Unverified',result.stderr)

    def test_definite_issues_are_visible_and_fatal_on_build_in_every_mode(self):
        self.write('package main\n'+UNKNOWN+'main::proc(){v:=make([]int,2);delete(v);_=v[0]}\n')
        for mode in ['summary','show','error']:
            for flags in [[],native.UNCHECKED]:
                packet=self.call('--unverified='+mode,*flags,command='build',status=1,json_output=True)
                definite=[d for d in packet['errors']if d.get('certainty')=='definite']
                self.assertEqual(len(definite),1,packet)
                self.assertEqual(definite[0]['code'],'thor/lifetime')
                self.assertEqual(definite[0]['type'],'error')
        result=self.call(command='build',status=1)
        self.assertIn('Error: Definite lifetime:',result.stderr)
        self.assertIn(str(self.source),result.stderr)

    def test_normal_warnings_and_warning_flags_do_not_change_notice_policy(self):
        self.write()
        for policy in ['-warnings-as-errors','-ignore-warnings']:
            for mode in ['summary','show','error']:
                with self.subTest(policy=policy,mode=mode):
                    self.call(policy,'--unverified='+mode,status=int(mode=='error'))
        self.write('package main\n'+UNKNOWN+'main::proc(){value:=7;_=value;print(value)}\nimport "thor:basic" / print\n')
        packet=self.call('--vet=immutable,lifetime,leak',command='build',status=1,json_output=True)
        self.assertTrue(any(d['type']=='error'and d.get('code')=='thor/prefer-immutable'for d in packet['errors']),packet)
        self.assertTrue(all(d['type']=='info'for d in packet['errors']if d.get('certainty')=='unverified'))

    def test_native_vet_is_separate_and_composes_with_thor_vet(self):
        self.write()
        only_native=self.call('-vet',command='build',vet=False)
        self.assertNotIn('Unverified',only_native.stderr)
        self.call('-vet','--unverified',command='build',vet=False,status=2)
        self.call('-vet',command='build')
        self.call('-vet','--unverified=error',command='build',status=1)
        self.write('package main\nmain::proc(){unused:=7}\n')
        for mode in ['summary','show','error']:
            result=self.call('-vet-unused-variables','--unverified='+mode,command='build',status=1)
            self.assertIn('unused',result.stderr)

    def test_invalid_values_context_and_implicit_source_are_unambiguous(self):
        self.write()
        for mode in ['','off','warning','true','SHOW']:
            result=self.call('--unverified='+mode,status=2)
            self.assertIn('choose summary, show, or error',result.stderr)
        for command in ['transpile','ast','doc','strip-semicolon']:
            result=self.call('--unverified',command=command,status=2,vet=False)
            self.assertIn('--unverified requires Thor vet checks',result.stderr)
        result=subprocess.run([str(native.RUST),'vet','--unverified','--vet=lifetime,leak',
            '--output-dir',str(self.root/'generated')],cwd=self.app,capture_output=True,text=True,timeout=90)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('Info: Unverified lifetime:',result.stderr)

    def test_command_help_lists_every_unverified_mode_for_supported_commands(self):
        for command in ['vet','check','build','run','test','publish']:
            result=subprocess.run([str(native.RUST),'help',command],cwd=self.root,capture_output=True,text=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('--unverified[=MODE]',result.stdout)
            for mode in ['summary','show','error']:self.assertIn(mode,result.stdout)
        result=subprocess.run([str(native.RUST),'build','-help'],cwd=self.root,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('--unverified[=MODE]',result.stdout)

    def test_apply_keeps_unverified_ownership_manual_even_in_strict_mode(self):
        self.write('package main\n'+UNKNOWN+'main::proc(){}\n')
        before=self.source.read_bytes()
        packet=self.call('--apply','--unverified=error',status=1,json_output=True)
        self.assertEqual(self.source.read_bytes(),before)
        self.assertTrue(all('fix'not in d for d in packet['errors']))

    def test_apply_can_fix_independent_advice_and_preserves_remaining_source_ranges(self):
        self.write('package main\nwork::proc(callback:proc(v:[]int)){v~:=make([]int,2);number: int = 7;if number!=7{panic("number")};callback(v)}\nmain::proc(){}\n')
        packet=self.call('--vet=immutable,lifetime,leak','--apply','--unverified=error',status=1,json_output=True)
        updated=self.source.read_text()
        self.assertIn('number: int > 7',updated)
        finding=next(d for d in packet['errors']if d.get('certainty')=='unverified')
        self.assertEqual(finding['pos']['offset'],updated.encode().index(b'callback(v)'))
        self.assertEqual(finding['pos']['end_offset'],updated.encode().index(b'callback(v)')+len('callback(v)'))
        self.assertNotIn('fix',finding)
        self.assertIn('v~:=make([]int,2)',updated)

    def test_inconclusive_corrections_are_included_without_claiming_the_edit_is_valid(self):
        self.write('package main\nmain::proc(){value:=7;if value!=7{panic("value")}}\n')
        for mode in ['summary','show','error']:
            packet=self.call('--vet=immutable','--apply','--unverified='+mode,
                env={'THOR_VET_VALIDATION_BUDGET':'0'},status=int(mode=='error'),json_output=True)
            self.assertEqual(packet['unverified']['categories'],{'correction':1})
            self.assertEqual(packet['errors'][0]['reason'],'correction-validation')
            self.assertNotIn('fix',packet['errors'][0])
        self.assertIn('value:=7',self.source.read_text())

    def test_summary_counts_locations_separately_and_reporting_does_not_repeat_analysis(self):
        self.write('package main\n'+''.join(f'work{i}::proc(callback:proc(v:[]int)){{v~:=make([]int,2);callback(v)}}\n'for i in range(24))+'main::proc(){}\n')
        evidence=[]
        for mode in ['summary','show','error']:
            profile=self.root/(mode+'.json')
            packet=self.call('--no-cache','--unverified='+mode,status=int(mode=='error'),json_output=True,env={'THOR_PROFILE':str(profile)})
            counters=json.loads(profile.read_text())['counters']
            evidence.append({k:v for k,v in counters.items()if k.startswith(('lifetime.','vet.lifetime_'))})
            self.assertEqual(packet['unverified']['count'],24)
            self.assertEqual(packet['unverified']['locations'],24)
            self.assertEqual(counters.get('diagnostics.vet_passthrough'),24)
        self.assertEqual(evidence[0],evidence[1]);self.assertEqual(evidence[0],evidence[2])
        self.write('package main\nmain::proc(){v:=make([]int,2);_=v}\n')
        plain=self.call(json_output=True,vet=False,command='check')
        self.assertNotIn('unverified',plain)

    def test_runtime_arguments_after_separator_are_not_consumed(self):
        self.write('package main\nimport "core:fmt"\nimport "core:os"\n'+UNKNOWN+'main::proc(){fmt.println(os.args[1])}\n')
        result=self.call('--','--unverified=error',command='run')
        self.assertEqual(result.stdout.strip(),'--unverified=error')

    def test_unverified_policy_cannot_hide_source_errors_or_no_alloc_contracts(self):
        for body in ['package main\nmain::proc(){_=unverified_missing_name}\n',
                     'package main\n@(no_alloc) main::proc(){v:=make([]int,2);defer delete(v);_=v[0]}\n']:
            self.write(body)
            for mode in ['summary','show','error']:
                packet=self.call('--unverified='+mode,command='check',status=1,json_output=True)
                self.assertGreater(packet['error_count'],0)
                self.assertNotIn('unverified',packet)

    def test_one_budget_boundary_can_have_lifetime_and_leak_notices(self):
        self.write('package main\nread::proc(){p~:=new(int,allocator=context.allocator);_=p^}\nmain::proc(){'+
            ';'.join('read()'for _ in range(280))+'}\n')
        packet=self.call(json_output=True)
        details=[d for d in packet['errors']if d.get('certainty')=='unverified']
        locations={(d['pos']['file'],d['pos']['offset'])for d in details}
        self.assertEqual(packet['unverified']['count'],len(details))
        self.assertEqual(packet['unverified']['locations'],len(locations))
        self.assertGreater(len(details),len(locations))

    @unittest.skipUnless(sys.platform=='darwin','native macOS publishing')
    def test_publish_collects_notices_once_and_strict_mode_does_not_package(self):
        self.write('package main\n'+UNKNOWN+'main::proc(){}\n')
        for mode in ['summary','show','error']:
            directory=self.root/('published-'+mode)
            packet=self.call('--unverified='+mode,'--app-type=console','--app-name=PolicyTest',
                '--publish-dir='+str(directory),command='publish',status=int(mode=='error'),json_output=True)
            self.assertEqual(packet['unverified']['count'],1)
            self.assertEqual(packet['error_count'],int(mode=='error'))
            self.assertTrue(all('fix'not in d for d in packet['errors']))
            if mode=='error':self.assertFalse((directory/'PolicyTest.app').exists())

if __name__=='__main__':unittest.main()
