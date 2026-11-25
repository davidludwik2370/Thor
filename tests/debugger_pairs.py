"""One-to-one debug-info controls; native builds never invoke Thor lowering."""
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from tests.test_debugger import DAP, ROOT

FIXTURES=ROOT/'tests/support/debugger_pairs'
CASES=json.loads((FIXTURES/'cases.json').read_text())
GUARDS={'assert(result==5&&x==4)':'if result!=5||x!=4 {panic("result")}',
        'assert(selected == 12)':'if selected!=12 {panic("result")}',
        'assert(result=="hello 7")':'if result!="hello 7" {panic("result")}',
        'assert(marker==7)':'if marker!=7 {panic("result")}',
        '_=line':'if line!=11{panic("branch location")}'}


def fixture_source(name):
    """Keep native pairs tied to the existing live regression, not a toy case."""
    tree=ast.parse((ROOT/'tests/test_debugger.py').read_text())
    cls=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='DebuggerTests')
    method=next(node for node in cls.body if isinstance(node,ast.FunctionDef) and node.name==CASES[name])
    text=None
    for node in ast.walk(method):
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='text' for t in node.targets):
            text=ast.literal_eval(node.value);break
    if text is None:
        call=next(node for node in ast.walk(method) if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='build')
        text=ast.literal_eval(call.args[0])
    for old,new in GUARDS.items():text=text.replace(old,new)
    return text


def prepare(name,root,compiler,odin,flags):
    """Separate package directories prevent duplicate .odin/.thor entry points."""
    result={}
    for language in ('thor','odin'):
        directory=root/language;directory.mkdir(parents=True,exist_ok=True)
        source=directory/('main.'+language)
        source.write_bytes((FIXTURES/name/source.name).read_bytes())
        helper=FIXTURES/name/'helper'
        if language=='odin' and helper.exists():shutil.copytree(helper,directory/'helper',dirs_exist_ok=True)
        program=directory/('program.exe' if os.name=='nt' else 'program')
        argv=[str(compiler if language=='thor' else odin),'build',str(directory),*flags,'-out:'+str(program)]
        if language=='thor':argv+=['--output-dir',str(directory/'generated')]
        else:argv+=['-collection:thor='+str(ROOT/'runtime')]
        built=subprocess.run(argv,cwd=directory,capture_output=True,text=True,timeout=120)
        if built.returncode:raise RuntimeError(f'{argv}\n{built.stdout}\n{built.stderr}')
        ran=subprocess.run([str(program)],cwd=directory,capture_output=True,text=True,timeout=20)
        if ran.returncode:raise RuntimeError(f'Runtime check failed: {program}\n{ran.stdout}\n{ran.stderr}')
        result[language]={'directory':str(directory),'source':str(source),'program':str(program),'build_command':argv,
            'runtime_exit':ran.returncode,'runtime_stdout':ran.stdout,'runtime_stderr':ran.stderr,
            'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'program_sha256':hashlib.sha256(program.read_bytes()).hexdigest()}
    if result['odin']['runtime_stdout']!=result['thor']['runtime_stdout']:raise RuntimeError('Native runtime output differs')
    return result


def compare_sessions(sessions):
    """Compare each repeat separately so another repeat cannot hide a failure."""
    native_failures=set();thor_failures=set();identity_only=set();thor_only=set();native_only=set()
    for repeat in {s.get('repeat',0) for s in sessions}:
        by_mode={s['mode']:{c['key']:c for c in s.get('checks',[])}
                 for s in sessions if s.get('repeat',0)==repeat}
        native=by_mode.get('native',{})
        bad_native={key for key,check in native.items() if not check['matched']}
        native_failures.update(bad_native)
        for mode in ('thor','identity'):
            target=by_mode.get(mode,{})
            bad={key for key,check in target.items() if not check['matched']}
            different={key for key in set(target)|set(native)
                       if key not in target or key not in native or key in bad and key not in bad_native}
            if mode=='thor':
                thor_failures.update(bad);thor_only.update(different);native_only.update(bad_native-bad)
            else:identity_only.update(different)
    return {'native_failures':sorted(native_failures),'thor_failures':sorted(thor_failures),
            'reproduced_native':sorted(thor_failures & native_failures),
            'thor_only':sorted(thor_only),'identity_only':sorted(identity_only),'native_only':sorted(native_only)}


class Probe:
    def __init__(self,name,built,mode):
        self.name,self.built,self.mode=name,built,mode
        self.directory=Path(built['directory']);self.source=Path(built['source'])
        self.trace=[];self.checks=[];self.stops=[];self.assembly=[]
        self.client=DAP(self.directory,bridge=ROOT/'tests/support/codelldb_passthrough.js' if mode=='native' else None,trace=self.trace)

    def check(self,key,actual,expected):
        self.checks.append(dict(key=key,actual=actual,expected=expected,matched=actual==expected))

    def watch(self,frame,expression,expected,key=None):
        try:
            value=self.client.request('evaluate',{'frameId':frame['id'],'expression':expression,'context':'watch'})
            self.check(key or expression,value.get('result'),expected)
        except AssertionError as error:
            value={'error':str(error)};self.check(key or expression,value,expected)
        return value

    def stop(self):
        event=self.client.event('stopped')
        frames=self.client.request('stackTrace',{'threadId':event['threadId']})['stackFrames']
        if not frames:raise RuntimeError('No native frames')
        self.stops.append({'event':event,'frames':frames})
        frame=frames[0]
        pointer=frame.get('instructionPointerReference')
        if pointer:
            try:self.assembly.append(self.client.request('disassemble',{'memoryReference':pointer,'instructionCount':10,'resolveSymbols':True}))
            except AssertionError as error:self.assembly.append({'error':str(error)})
        return event['threadId'],frame

    def launch(self,source,lines):
        self.breakpoint_source=source
        self.client.request('initialize',{'adapterID':'lldb' if self.mode=='native' else 'thor','linesStartAt1':True,
            'columnsStartAt1':True,'pathFormat':'path','supportsRunInTerminalRequest':False,'supportsVariableType':True})
        arguments={'program':self.built['program'],'cwd':str(self.directory),'terminal':'console'}
        if self.mode!='native':
            manifest=self.directory/'generated/thor-manifest.json'
            if not manifest.exists():
                # Identity bridge control: same executable, no Thor source maps.
                manifest=self.directory/'identity-manifest.json'
                manifest.write_text(json.dumps({'debug_map_version':1,'odin_flags':['-debug','-o:none'],'generated':[],
                    'debug_program':{'path':self.built['program'],'sha256':self.built['program_sha256']}}))
            arguments['manifest']=str(manifest)
        request=self.client.send('launch',arguments)
        self.client.event('initialized')
        points=[line if isinstance(line,dict) else {'line':line} for line in lines]
        bps=self.client.request('setBreakpoints',{'source':{'path':str(source)},'breakpoints':points})
        for index,(point,actual) in enumerate(zip(points,bps['breakpoints'])):
            self.check('breakpoint:'+str(index),actual.get('verified'),True)
            self.checks[-1]['requested_line']=point['line']
        self.client.request('configurationDone');self.client.response(request)
        return bps

    def finish(self,thread):
        self.client.request('setBreakpoints',{'source':{'path':str(self.breakpoint_source)},'breakpoints':[]})
        self.client.request('continue',{'threadId':thread});self.client.event('terminated')

    def entry(self,source,line):
        self.launch(source,[line]);return self.stop()

    def run(self):
        c=self.client
        try:
            name=self.name
            if name=='allocated':
                thread,frame=self.entry(self.source,3)
                self.watch(frame,'n','7','entry:n')
                c.request('next',{'threadId':thread});thread,frame=self.stop()
                self.watch(frame,'n','7','after:n');self.watch(frame,'text.len','7','after:text.len')
                self.finish(thread)
            elif name=='using':
                thread,frame=self.entry(self.source,11)
                c.request('stepIn',{'threadId':thread});thread,frame=self.stop()
                if frame['line']==4:
                    c.request('next',{'threadId':thread});thread,frame=self.stop()
                self.check('entry:line',frame['line'],5);self.watch(frame,'record.x','4','entry:record.x')
                c.request('next',{'threadId':thread});thread,frame=self.stop()
                self.watch(frame,'record.x','4','after:record.x');self.watch(frame,'value','5','after:value')
                self.finish(thread)
            elif name=='basic':
                source=ROOT/'runtime/basic/basic.thor' if self.source.suffix=='.thor' else self.directory/'helper/basic.odin'
                line=next(i for i,text in enumerate(source.read_text().splitlines(),1) if 'writer := os.to_stream(os.stdout)' in text)
                thread,frame=self.entry(source,line)
                self.check('entry:line',frame['line'],line);self.watch(frame,'args.len','1','entry:args.len')
                c.request('next',{'threadId':thread});thread,frame=self.stop()
                self.check('after:scope',Path(frame.get('source',{}).get('path','')).resolve()==source.resolve(),True)
                self.check('after:line',frame['line'],line+1)
                self.watch(frame,'args.len','1','after:args.len');self.finish(thread)
            elif name=='deferred':
                self.launch(self.source,[11,4]);thread,frame=self.stop()
                if frame['line']!=4 or 'observe' not in frame['name']:
                    c.request('continue',{'threadId':thread});thread,frame=self.stop()
                self.check('observe:line',frame['line'],4)
                c.request('next',{'threadId':thread});thread,frame=self.stop()
                self.watch(frame,'line','11','after:line');self.finish(thread)
            elif name=='fallback':
                self.launch(self.source,[8,9]);thread,frame=self.stop()
                for _ in range(14):
                    c.request('stepIn',{'threadId':thread});thread,frame=self.stop()
                    if frame['source'].get('path') and Path(frame['source']['path'])==self.source and frame['line']==4:break
                self.check('entry:line',frame['line'],4);self.watch(frame,'x','7','entry:x')
                c.request('continue',{'threadId':thread});thread,frame=self.stop()
                self.check('after:line',frame['line'],9);self.watch(frame,'value','8','after:value');self.finish(thread)
            elif name=='pmap':
                self.launch(self.source,[5,13]);thread,frame=self.stop()
                self.check('entry:line',frame['line'],5)
                self.watch(frame,'a','2','entry:a');self.watch(frame,'b','3','entry:b')
                c.request('next',{'threadId':thread});thread,frame=self.stop()
                self.watch(frame,'a','2','after:a');self.watch(frame,'b','3','after:b')
                c.request('continue',{'threadId':thread});thread,frame=self.stop()
                self.check('result:line',frame['line'],13);self.watch(frame,'rows.data[0].sum','5','result:sum');self.finish(thread)
            elif name=='static':
                self.launch(self.source,[5,6]);thread,frame=self.stop()
                self.watch(frame,'values.len','2','values.len');self.watch(frame,'values.data[1]','8','values.data[1]')
                c.request('next',{'threadId':thread});thread,frame=self.stop()
                self.watch(frame,'total','16','total');self.finish(thread)
            elif name=='switch':
                thread,frame=self.entry(self.source,8)
                for _ in range(16):
                    c.request('stepIn',{'threadId':thread});thread,frame=self.stop()
                    if frame.get('source',{}).get('path') and Path(frame['source']['path'])==self.source and frame['line']==3:break
                self.check('entry:line',frame['line'],3);self.watch(frame,'value','6','entry:value')
                c.request('next',{'threadId':thread});thread,frame=self.stop()
                self.watch(frame,'doubled','12','after:doubled')
                c.request('stepOut',{'threadId':thread});thread,frame=self.stop()
                self.watch(frame,'values[0]','2','outer:values[0]');self.finish(thread)
            elif name=='unicode':
                prefix=self.source.read_text().splitlines()[2].split('marker')[0]
                column=len(prefix.encode('utf-16-le'))//2+1
                self.launch(self.source,[{'line':3,'column':column}]);thread,frame=self.stop()
                self.check('column',frame.get('column'),column);self.finish(thread)
            return {'checks':self.checks,'stops':self.stops,'assembly':self.assembly,'completed':True}
        finally:c.close()
