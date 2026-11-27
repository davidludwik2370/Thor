"""Execution failures remain bounded, source-located compiler diagnostics."""
import errno
import json
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from tests import test_thor as base
from thorlang.compiler import ThorError
from thorlang.driver import Project, frontend_binary
from thorlang.evaluation import Evaluator


class EvaluationHardeningTests(unittest.TestCase):
    invoke=base.ThorTests.invoke
    program=base.ThorTests.program

    def test_runtime_failures_have_an_evaluation_site_and_original_failure_note(self):
        for body,reason in [('panic("specific reason")','specific reason'),
                            ('assert(n!=7,"specific assertion");return n','specific assertion'),
                            ('data:=[1,2];return data[n]','out of range'),
                            ('return 10/(n-7)',None)]:
            for mode in ['fn','proc','automatic']:
                with self.subTest(body=body,mode=mode):
                    expression=('' if mode=='automatic' else '#run ')+'bad(7)'
                    text='// 😀\npackage main\nbad::'+('proc' if mode=='proc' else 'fn')+'(n:int)->int{/* 😀 */'+body+'}\nVALUE::'+expression+'\nmain::proc(){}\n'
                    _,result=self.invoke(text,command='check',flags=['-json-errors'],fail='execution failed')
                    errors=json.loads(result.stderr)['errors']
                    self.assertEqual(len(errors),1,errors)
                    self.assertEqual(errors[0]['pos']['offset'],text.encode().index(expression.encode()))
                    if reason:
                        self.assertIn(reason.lower(),result.stderr.lower())
                        self.assertTrue(errors[0].get('related'),errors)
                        note=errors[0]['related'][0]
                        self.assertEqual(note['pos']['line'],3,note)
                    else:
                        self.assertIn('without a runtime error message',result.stderr)
                        self.assertNotIn('division by zero',result.stderr)
                    self.assertNotIn('thor-run-',result.stderr)
                    self.assertNotIn('Thor expression',result.stderr)

    def test_timeouts_in_automatic_and_explicit_execution_keep_the_request_location(self):
        for marker,kind in [('#run ','proc'),('','fn')]:
            expression=marker+'spin(7)'
            text=self.program('_=VALUE','spin::'+kind+'(n:int)->int{for{};return n}\nVALUE::'+expression+'\n')
            _,result=self.invoke(text,command='check',flags=['--run-timeout','0.05','-json-errors'],fail='timeout')
            error=json.loads(result.stderr)['errors'][0]
            self.assertEqual(error['pos']['offset'],text.index(expression))
            self.assertIn('0.05',error['msgs'][0])

    def test_explicit_run_rejects_no_result_and_multiple_results_before_helper_inference(self):
        for declaration,count in [('bad::proc(){}',0),('bad::proc()->(int,int){return 1,2}',2)]:
            text=self.program('_=VALUE',declaration+'\nVALUE::#run bad()\n')
            _,result=self.invoke(text,command='check',flags=['-json-errors'],fail='exactly one supported result')
            errors=json.loads(result.stderr)['errors']
            self.assertEqual(len(errors),1,errors)
            self.assertEqual(errors[0]['pos']['offset'],text.index('#run bad()'))
            self.assertIn(f'{count} values',errors[0]['msgs'][0])

    def test_optional_status_and_allocating_helpers_keep_one_value_contexts(self):
        self.invoke(self.program('''if VALUE!=7{panic("optional status")}
text:=#run strings.clone("text");defer delete(text)
data:=#run slice.clone([]int{1,2});defer delete(data)
if text!="text"||data[1]!=2{panic("allocated result")}
''','''import "core:strings"
import "core:slice"
bad::proc(n:int)->(int,bool)#optional_ok{return n,true}
VALUE::#run bad(7)
'''))

    def test_runtime_arguments_and_slice_constant_context_explain_the_required_change(self):
        cases=[
            (self.program('n:>7;_= #run bad(n)','bad::fn(n:int)->int{return n}\n'),'runtime storage is not a constant','n)'),
            (self.program('_=VALUE','bad::fn()->[]int{return make([]int,2)}\nVALUE::#run bad()\n'),'runtime binding (:=)','#run bad()'),
            (self.program('_=VALUE','bad::proc "contextless"()->string{return "text"}\nVALUE::#run bad()\n'),'Contextless compile-time procedures cannot return strings/slices','string'),
        ]
        for text,message,needle in cases:
            with self.subTest(message=message):
                _,result=self.invoke(text,command='check',flags=['-json-errors'],fail=message)
                errors=json.loads(result.stderr)['errors']
                offset=text.rindex(needle)
                self.assertTrue(any(e['pos'] and e['pos']['offset']<=offset<e['pos']['end_offset'] for e in errors),errors)
                self.assertNotIn('Thor expression',result.stderr)

    def test_invalid_code_cannot_hide_behind_a_previously_cached_result(self):
        text=self.program('if VALUE!=8{panic("result")}', 'bad::fn(n:int)->int{return n+1}\nVALUE::#run bad(7)\n')
        root,_=self.invoke(text)
        source=root/'input/main.thor'
        broken=text.replace('bad::fn','state:int\nbad::fn').replace('return n+1','return state+n')
        source.write_text(broken)
        result=subprocess.run([str(base.THOR),'check',str(source),'-json-errors'],capture_output=True,text=True,timeout=40)
        self.assertNotEqual(result.returncode,0,result.stderr)
        errors=json.loads(result.stderr)['errors']
        self.assertTrue(any('outside the function' in ' '.join(e['msgs']) for e in errors),errors)
        self.assertTrue(any(e['pos']['offset']==broken.index('state+n') for e in errors),errors)

    def test_release_assertion_elision_preserves_language_semantics_but_not_purity_bypasses(self):
        text=self.program('if VALUE!=7{panic("result")}', 'bad::fn(n:int)->int{assert(false,"elided");return n}\nVALUE::#run bad(7)\n')
        self.invoke(text,flags=['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert'])
        text=text.replace('assert(false,"elided");','panic("still active");')
        self.invoke(text,command='build',flags=['-o:speed','-disable-assert'],fail='still active')


class EvaluationInfrastructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.frontend,cls.odin=frontend_binary('odin')

    def failure(self,phase,status=None,exception=None,stdout=b'',stderr=b''):
        with tempfile.TemporaryDirectory(prefix='thor-eval-failure-') as directory:
            source=Path(directory).resolve()/'main.thor'
            text='package main\nidentity::fn(n:int)->int{return n}\nVALUE::#run identity(7)\nmain::proc(){}\n'
            source.write_text(text)
            project=Project(source,self.frontend,[])
            evaluator=Evaluator(project,self.odin)
            real=subprocess.run
            def run(command,**kwargs):
                build=len(command)>2 and command[1]=='build' and any(str(arg).startswith('-out:') and 'helper' in str(arg) for arg in command)
                execute=len(command)==1 and Path(command[0]).name.startswith('helper')
                if (phase=='compile' and build) or (phase=='execute' and execute):
                    if exception:raise exception
                    return subprocess.CompletedProcess(command,status,stdout,stderr)
                return real(command,**kwargs)
            with patch('thorlang.evaluation.subprocess.run',side_effect=run):
                with self.assertRaises(ThorError) as caught:evaluator.evaluate_all()
            errors=caught.exception.diagnostics
            self.assertEqual(len(errors),1,errors)
            self.assertEqual(errors[0]['pos']['file'],str(source))
            self.assertEqual(errors[0]['pos']['offset'],text.index('#run identity'))
            self.assertNotIn('Traceback',json.dumps(errors))
            return errors[0]['msgs'][0]

    def test_backend_signal_is_named_and_attached_to_evaluation_request(self):
        message=self.failure('compile',status=-signal.SIGBUS)
        self.assertIn('SIGBUS',message);self.assertIn('backend compiler failure',message)

    def test_locationless_helper_checker_error_is_attached_to_its_request(self):
        error={'type':'error','pos':None,'msgs':['Native checker failed without an inner location']}
        message=self.failure('compile',status=1,stderr=json.dumps({'errors':[error]}).encode())
        self.assertIn('Native checker failed',message)

    def test_execution_signal_and_invalid_protocol_are_clear(self):
        message=self.failure('execute',status=-signal.SIGSEGV)
        self.assertIn('SIGSEGV',message);self.assertIn('execution failed',message)
        message=self.failure('execute',status=0,stdout=b'not a result packet')
        self.assertIn('invalid compile-time result',message)

    def test_helper_launch_failures_and_compile_timeout_are_diagnostics(self):
        for phase in ['compile','execute']:
            with self.subTest(phase=phase):
                message=self.failure(phase,exception=PermissionError(errno.EACCES,'Permission denied'))
                self.assertIn('could not start',message);self.assertIn('Permission denied',message)
        message=self.failure('compile',exception=subprocess.TimeoutExpired('odin',30))
        self.assertIn('helper compilation timed out',message)


if __name__=='__main__':unittest.main()
