"""Real CodeLLDB/DAP checks against executables built with ordinary Odin."""
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


def backend_path():
    configured = os.environ.get('THOR_CODELLDB')
    if configured:
        return Path(configured)
    extensions = Path.home() / '.vscode/extensions'
    matches = list(extensions.glob('vadimcn.vscode-lldb-*'))
    return max(matches, key=lambda p: p.stat().st_mtime) if matches else None


class DAP:
    def __init__(self, directory):
        self.log = tempfile.TemporaryFile()
        self.process = subprocess.Popen(['node', str(ROOT/'editors/vscode/debug-adapter.js'), str(backend_path())],
            cwd=directory, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log)
        self.messages = queue.Queue()
        self.events = []
        self.responses = {}
        self.seq = 0
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()

    def read(self):
        try:
            while True:
                headers = {}
                while True:
                    line = self.process.stdout.readline()
                    if not line:
                        self.messages.put({'eof': True})
                        return
                    if line == b'\r\n':
                        break
                    key, value = line.decode().split(':', 1)
                    headers[key.lower()] = value.strip()
                self.messages.put(json.loads(self.process.stdout.read(int(headers['content-length']))))
        except Exception as error:
            self.messages.put({'error': str(error)})

    def send(self, command, arguments=None):
        self.seq += 1
        data = json.dumps({'seq': self.seq, 'type': 'request', 'command': command, 'arguments': arguments or {}}).encode()
        self.process.stdin.write(f'Content-Length: {len(data)}\r\n\r\n'.encode()+data)
        self.process.stdin.flush()
        return self.seq

    def next(self, timeout):
        value = self.messages.get(timeout=timeout)
        if 'eof' in value or 'error' in value:
            self.log.seek(0)
            raise AssertionError((value,self.log.read().decode()))
        if value['type'] == 'event':
            self.events.append(value)
        elif value['type'] == 'response':
            self.responses[value['request_seq']] = value
        else:
            raise AssertionError(f'Unexpected reverse request: {value}')

    def response(self, seq, timeout=30):
        deadline = time.monotonic()+timeout
        while seq not in self.responses:
            self.next(max(.01,deadline-time.monotonic()))
        response = self.responses.pop(seq)
        if not response.get('success'):
            raise AssertionError(response)
        return response.get('body',{})

    def request(self, command, arguments=None):
        return self.response(self.send(command,arguments))

    def event(self, event, timeout=30):
        deadline = time.monotonic()+timeout
        while True:
            for i,message in enumerate(self.events):
                if message['event'] == event:
                    return self.events.pop(i).get('body',{})
            self.next(max(.01,deadline-time.monotonic()))

    def close(self):
        try:
            if self.process.poll() is None:
                self.request('disconnect',{'terminateDebuggee':True})
                self.process.stdin.close()
                self.process.wait(timeout=5)
        finally:
            if self.process.poll() is None:
                self.process.kill()
                self.process.wait(timeout=5)
            if not self.process.stdin.closed:
                self.process.stdin.close()
            self.process.stdout.close()
            self.reader.join(timeout=1)
            self.log.close()


@unittest.skipUnless(backend_path() and shutil.which('node') and shutil.which('odin'), 'CodeLLDB, Node and Odin required')
class DebuggerTests(unittest.TestCase):
    def test_breakpoint_in_shared_basic_uses_its_installed_source_map(self):
        directory,_=self.build('package main\nimport "thor:basic"\nmain::proc(){basic.print("shared breakpoint")}\n')
        basic=ROOT/'runtime/basic/basic.thor'
        line=next(i for i,text in enumerate(basic.read_text().splitlines(),1) if 'writer := os.to_stream(os.stdout)' in text)
        client=self.launch(directory,basic,[line])
        thread,frame=self.frame(client)
        self.assertEqual((frame['source']['path'],frame['line']),(str(basic),line),frame)
        arguments=client.request('evaluate',{'frameId':frame['id'],'expression':'args.len','context':'watch'})
        self.assertEqual(arguments['result'],'1',arguments)
        client.request('continue',{'threadId':thread})
        client.event('terminated')

    def test_checked_ordinary_procedure_keeps_its_frame_and_locals(self):
        text='''package main
leaf::proc(n:int)->int{
    value:=n+1
    return value
}
work::fn(n:int)->int{return leaf(n)}
main::proc(){n:=3;result:=work(n);assert(result==4)}
'''
        directory,source=self.build(text)
        client=self.launch(directory,source,[3])
        thread,frame=self.frame(client)
        self.assertEqual((frame['name'],frame['line']),('leaf',3),frame)
        value=client.request('evaluate',{'frameId':frame['id'],'expression':'n','context':'watch'})
        self.assertEqual(value['result'],'3')
        client.request('next',{'threadId':thread})
        thread,frame=self.frame(client)
        self.assertEqual(frame['line'],4,frame)
        client.request('continue',{'threadId':thread})
        client.event('terminated')

    def test_spread_extrema_step_over_and_optional_status_locals(self):
        text='''package main
main::proc(){
    values:=[]int{5,2,7}
    lowest,ok:=min(9,..values)
    after:=lowest+1
    assert(after==3&&ok)
}
'''
        directory,source=self.build(text)
        client=self.launch(directory,source,[4])
        thread,frame=self.frame(client)
        self.assertEqual((frame['source']['path'],frame['line']),(str(source),4))
        client.request('next',{'threadId':thread})
        thread,frame=self.frame(client)
        self.assertEqual(frame['line'],5,frame)
        scopes=client.request('scopes',{'frameId':frame['id']})['scopes']
        variables=[v for scope in scopes if not scope['expensive'] for v in client.request('variables',{'variablesReference':scope['variablesReference']})['variables']]
        self.assertTrue(any(v['name']=='lowest' and v['value']=='2' for v in variables),variables)
        self.assertTrue(any(v['name']=='ok' and v['value']=='true' for v in variables),variables)
        client.request('continue',{'threadId':thread})
        client.event('terminated')

    def build(self, text, files=None):
        temporary = tempfile.TemporaryDirectory(prefix='thor-dap-')
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name).resolve()
        source = directory/'main.thor'
        source.write_text(text)
        for name, content in (files or {}).items():
            file=directory/name
            file.parent.mkdir(parents=True,exist_ok=True)
            file.write_text(content)
        build = subprocess.run([str(ROOT/'thor'),'build',str(directory),'-debug','-o:none',
            '--output-dir',str(directory/'generated'),'-out:'+str(directory/'program')],capture_output=True,text=True,timeout=90)
        self.assertEqual(build.returncode,0,build.stdout+build.stderr)
        return directory,source

    def launch(self,directory,source,lines):
        client=DAP(directory)
        self.addCleanup(client.close)
        client.request('initialize',{'adapterID':'thor','linesStartAt1':True,'columnsStartAt1':True,'pathFormat':'path',
            'supportsRunInTerminalRequest':False,'supportsVariableType':True})
        launch=client.send('launch',{'program':str(directory/'program'),'cwd':str(directory),
            'manifest':str(directory/'generated/thor-manifest.json'),'terminal':'console'})
        client.event('initialized')
        bps=client.request('setBreakpoints',{'source':{'path':str(source)},'breakpoints':[line if isinstance(line,dict) else {'line':line} for line in lines]})
        self.assertTrue(all(bp['verified'] for bp in bps['breakpoints']),bps)
        client.request('configurationDone')
        client.response(launch)
        return client

    def frame(self,client):
        stop=client.event('stopped')
        frames=client.request('stackTrace',{'threadId':stop['threadId']})['stackFrames']
        self.assertTrue(frames)
        self.assertNotIn('__thor_',json.dumps(frames))
        return stop['threadId'],frames[0]

    def test_breakpoint_and_step_over_original_thor_lines(self):
        text='''package main
main :: proc() {
    values := [10, 20, 30]
    selector := 2
    chosen := switch selector {
        case 1: values[0]
        case: values[-1]
    }
    total := chosen + 5
    assert(total == 35)
}
'''
        directory,source=self.build(text)
        client=self.launch(directory,source,[4])
        thread,frame=self.frame(client)
        self.assertEqual((frame['source']['path'],frame['line']),(str(source),4),frame)
        client.request('next',{'threadId':thread})
        thread,frame=self.frame(client)
        self.assertEqual(frame['line'],5,frame)
        client.request('next',{'threadId':thread})
        thread,frame=self.frame(client)
        self.assertEqual(frame['line'],9,frame)
        scopes=client.request('scopes',{'frameId':frame['id']})['scopes']
        variables=[v for scope in scopes if not scope['expensive'] for v in client.request('variables',{'variablesReference':scope['variablesReference']})['variables']]
        self.assertTrue(any(v['name']=='chosen' and v['value']=='30' for v in variables),variables)
        client.request('continue',{'threadId':thread})
        client.event('terminated')

    def test_step_into_user_call_inside_switch_and_inspect_helper_locals(self):
        text='''package main
twice :: proc(value:int) -> int {
    doubled := value * 2
    return doubled
}
main :: proc() {
    values := [2,4,6]
    selected := switch values[-1] {
        case 6: twice(values[-1])
        case: 0
    }
    assert(selected == 12)
}
'''
        directory,source=self.build(text)
        client=self.launch(directory,source,[8])
        thread,frame=self.frame(client)
        self.assertEqual(frame['line'],8,frame)
        observed=[]
        for _ in range(12):
            client.request('stepIn',{'threadId':thread})
            thread,frame=self.frame(client)
            observed.append(frame['line'])
            if frame['line']==3:
                break
        self.assertEqual(frame['line'],3,(observed,frame))
        value=client.request('evaluate',{'frameId':frame['id'],'expression':'value','context':'watch'})
        self.assertEqual(value['result'],'6',value)
        client.request('stepOut',{'threadId':thread})
        thread,frame=self.frame(client)
        self.assertIn(frame['line'],[9,12],frame)
        outer=client.request('evaluate',{'frameId':frame['id'],'expression':'values[0]','context':'watch'})
        self.assertEqual(outer['result'],'2',outer)
        client.request('continue',{'threadId':thread})
        client.event('terminated')

    def test_switch_arm_breakpoint_reports_original_source_and_outer_variables(self):
        text='''package main
main :: proc() {
    values := [3,6,9]
    selected := switch values[-1] {
        case 9: values[0]+1
        case: 0
    }
    text := f"selected {selected}"
    defer delete(text)
    assert(text=="selected 4")
}
'''
        directory,source=self.build(text)
        client=self.launch(directory,source,[5])
        thread,frame=self.frame(client)
        self.assertEqual(frame['line'],5,frame)
        self.assertEqual(frame['source']['path'],str(source))
        scopes=client.request('scopes',{'frameId':frame['id']})['scopes']
        variables=[v for scope in scopes if not scope['expensive'] for v in client.request('variables',{'variablesReference':scope['variablesReference']})['variables']]
        self.assertTrue(any(v['name']=='values' for v in variables),variables)
        self.assertFalse(any(v['name'].startswith('__thor_') for v in variables),variables)
        client.request('next',{'threadId':thread})
        thread,frame=self.frame(client)
        self.assertEqual(frame['line'],8,frame)
        client.request('next',{'threadId':thread})
        thread,frame=self.frame(client)
        self.assertEqual(frame['line'],10,frame) # defer registration emits no instruction here
        client.request('continue',{'threadId':thread})
        client.event('terminated')

    def test_allocated_fn_body_has_original_name_and_runtime_breakpoint(self):
        text='''package main
render :: fn(n:int) -> string {
    text := f"hello {n}"
    return text
}
main :: proc() {
    result := render(7)
    defer delete(result)
    assert(result=="hello 7")
}
'''
        directory,source=self.build(text)
        client=self.launch(directory,source,[3])
        thread,frame=self.frame(client)
        self.assertEqual(frame['line'],3,frame)
        self.assertIn('render',frame['name'])
        value=client.request('evaluate',{'frameId':frame['id'],'expression':'n','context':'watch'})
        self.assertEqual(value['result'],'7')
        client.request('next',{'threadId':thread})
        thread,frame=self.frame(client)
        self.assertEqual(frame['line'],4,frame)
        client.request('continue',{'threadId':thread})
        client.event('terminated')

    def test_native_import_breakpoint_and_step_back_to_thor(self):
        directory,source=self.build('''package main
import "library"
main::proc(){
    value:=library.work(4)
    assert(value==12)
}
''',{'library/native.odin':'''package library
work::proc(value:int)->int {
    result:=value*3
    return result
}
'''})
        imported=directory/'library/native.odin'
        client=self.launch(directory,imported,[3])
        thread,frame=self.frame(client)
        self.assertEqual((frame['source']['path'],frame['line']),(str(imported),3))
        client.request('stepOut',{'threadId':thread})
        thread,frame=self.frame(client)
        self.assertEqual(frame['source']['path'],str(source),frame)
        client.request('continue',{'threadId':thread})
        client.event('terminated')

    def test_conditional_loop_breakpoint_and_variable_edit(self):
        directory,source=self.build('''package main
main::proc(){
    total:=0
    for i:=0;i<3;i+=1 {
        total+=i
    }
    assert(total==5)
}
''')
        client=self.launch(directory,source,[{'line':5,'condition':'i == 1'}])
        thread,frame=self.frame(client)
        self.assertEqual(frame['line'],5)
        scopes=client.request('scopes',{'frameId':frame['id']})['scopes']
        scope=next(scope for scope in scopes if not scope['expensive'] and any(v['name']=='total'
            for v in client.request('variables',{'variablesReference':scope['variablesReference']})['variables']))
        client.request('setVariable',{'variablesReference':scope['variablesReference'],'name':'total','value':'2'})
        value=client.request('evaluate',{'frameId':frame['id'],'expression':'total','context':'watch'})
        self.assertEqual(value['result'],'2')
        client.request('continue',{'threadId':thread})
        client.event('terminated')

    def test_no_breakpoint_on_compile_time_declaration_or_inactive_branch(self):
        directory,source=self.build('''package main
double::fn(n:int)->int{return n*2}
SIZE::#run double(3)
main::proc(){
    data:[SIZE]int
    #if false {
        missing()
    }
    assert(len(data)==6)
}
''')
        client=self.launch(directory,source,[9])
        bps=client.request('setBreakpoints',{'source':{'path':str(source)},'breakpoints':[{'line':3},{'line':7},{'line':9}]})
        self.assertEqual([bp['verified'] for bp in bps['breakpoints']],[False,False,True],bps)
        thread,frame=self.frame(client)
        self.assertEqual(frame['line'],9,frame)
        client.request('continue',{'threadId':thread})
        client.event('terminated')

    def test_helper_condition_and_edit_use_original_variable_names(self):
        directory,source=self.build('''package main
main::proc(){
    selector:=9
    chosen:=switch selector {
        case 9: selector+1
        case: 0
    }
    assert(chosen==11)
}
''')
        client=self.launch(directory,source,[{'line':5,'condition':'selector == 9'}])
        thread,frame=self.frame(client)
        self.assertEqual(frame['line'],5,frame)
        scopes=client.request('scopes',{'frameId':frame['id']})['scopes']
        client.request('setVariable',{'variablesReference':scopes[0]['variablesReference'],'name':'selector','value':'10'})
        value=client.request('evaluate',{'frameId':frame['id'],'expression':'selector','context':'watch'})
        self.assertEqual(value['result'],'10')
        client.request('continue',{'threadId':thread})
        client.event('terminated')

    def test_unicode_inline_breakpoint_uses_utf16_editor_columns(self):
        text='''package main
main::proc(){
    _="😀"; marker:=7
    assert(marker==7)
}
'''
        directory,source=self.build(text)
        prefix=text.splitlines()[2].split('marker')[0]
        column=len(prefix.encode('utf-16-le'))//2+1
        client=self.launch(directory,source,[{'line':3,'column':column}])
        thread,frame=self.frame(client)
        self.assertEqual((frame['line'],frame['column']),(3,column),frame)
        client.request('continue',{'threadId':thread})
        client.event('terminated')

    def test_deep_stack_paging_counts_user_frames_after_mapping(self):
        directory,source=self.build('''package main
descend::proc(n:int)->int {
    if n==0 {
        return 0
    }
    return descend(n-1)+1
}
main::proc(){assert(descend(130)==130)}
''')
        client=self.launch(directory,source,[4])
        stop=client.event('stopped')
        all_frames=client.request('stackTrace',{'threadId':stop['threadId']})
        self.assertGreaterEqual(all_frames['totalFrames'],132)
        page=client.request('stackTrace',{'threadId':stop['threadId'],'startFrame':110,'levels':10})
        self.assertEqual(len(page['stackFrames']),10)
        self.assertTrue(all('descend' in frame['name'] for frame in page['stackFrames']))
        self.assertEqual(page['totalFrames'],all_frames['totalFrames'])
        client.request('continue',{'threadId':stop['threadId']})
        client.event('terminated')


if __name__ == '__main__':
    unittest.main()
