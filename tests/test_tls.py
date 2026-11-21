"""Exercise the TLS executable through its actual stdio LSP transport."""
from __future__ import annotations

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
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / 'tls' / 'bin' / ('tls.exe' if os.name == 'nt' else 'tls')
THOR = Path(os.environ.get('THOR_TEST_COMPILER', ROOT / 'thor'))


class Client:
    def __init__(self, directory, *, compiler=True, compiler_command=None, unused_imports=False, refresh=False, vet=False, checker_args="", collections=None):
        self.directory = directory
        self.messages = queue.Queue()
        self.notifications = []
        self.server_requests = []
        self.sequence = 0
        self.log = tempfile.TemporaryFile()
        self.process = subprocess.Popen([str(BINARY)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=self.log, cwd=directory,
                                        env={**os.environ, 'OLS_BUILTIN_FOLDER': str(ROOT / 'tls' / 'builtin')})
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()
        odin = os.environ.get('THOR_ODIN', 'odin')
        odin_root = subprocess.check_output([odin, 'root'], text=True).strip()
        self.capabilities = self.request('initialize', {
            'processId': os.getpid(), 'rootUri': directory.as_uri(),
            'workspaceFolders': [{'uri': directory.as_uri(), 'name': 'test'}],
            'capabilities': {'workspace': {'inlayHint': {'refreshSupport': refresh},
                                           'semanticTokens': {'refreshSupport': refresh}},
                            'textDocument': {'hover': {'contentFormat': ['markdown']},
                            'completion': {'completionItem': {'snippetSupport': True}},
                            'semanticTokens': {'requests': {'full': True}}}},
            'initializationOptions': {'odin_root_override': odin_root, 'enable_format': False,
                'collections': [{'name':'thor','path':str(ROOT/'runtime')}] if collections is None else collections,
                'odin_command': odin, 'thor_command': str(compiler_command or THOR) if compiler else '',
                'enable_parser_errors': True, 'enable_semantic_tokens': True,
                'enable_unused_imports_reporting': unused_imports,
                'enable_unused_imports_on_change': unused_imports, 'enable_auto_import': False,
                'enable_checker_workspace_diagnostics': False, 'thread_count': 1,
                'checker_args': ('--vet ' if vet else '')+checker_args}
        })['capabilities']
        self.send('initialized', {})

    def read(self):
        try:
            while True:
                headers = {}
                while True:
                    line = self.process.stdout.readline()
                    if not line:
                        self.messages.put({'eof': True})
                        return
                    if line in (b'\r\n', b'\n'):
                        break
                    name, value = line.decode().split(':', 1)
                    headers[name.lower()] = value.strip()
                size = int(headers['content-length'])
                payload = bytearray()
                while len(payload) < size:
                    part = self.process.stdout.read(size - len(payload))
                    if not part:
                        raise EOFError('incomplete LSP response')
                    payload.extend(part)
                self.messages.put(json.loads(payload))
        except Exception as error:
            self.messages.put({'reader_error': str(error)})

    def send(self, method, params, identifier=None):
        value = {'jsonrpc': '2.0', 'method': method, 'params': params}
        if identifier is not None:
            value['id'] = identifier
        self.write(value)

    def write(self, value):
        data = json.dumps(value, ensure_ascii=False).encode()
        self.process.stdin.write(f'Content-Length: {len(data)}\r\n\r\n'.encode() + data)
        self.process.stdin.flush()

    def next(self, timeout=15):
        value = self.messages.get(timeout=timeout)
        if 'eof' in value or 'reader_error' in value:
            self.log.seek(0)
            raise AssertionError(f'TLS stopped: {value}\n{self.log.read().decode(errors="replace")}')
        if 'method' in value and 'id' in value:
            self.server_requests.append(value)
            self.write({'jsonrpc': '2.0', 'id': value['id'], 'result': None})
        else:
            self.notifications.append(value)
        return value

    def request(self, method, params):
        self.sequence += 1
        identifier = self.sequence
        self.send(method, params, identifier)
        deadline = time.monotonic() + 15
        while True:
            value = self.next(max(0.01, deadline - time.monotonic()))
            if value.get('id') == identifier and 'method' not in value:
                if 'error' in value:
                    raise AssertionError(value['error'])
                return value.get('result')

    def diagnostic(self, uri, *, empty=False, code=None, contains=None, snapshot=None, timeout=45):
        def matching(value):
            return (value.get('method') == 'textDocument/publishDiagnostics'
                    and value['params'].get('uri') == uri
                    and (not empty or not value['params']['diagnostics'])
                    and (code is None or any(d.get('code') == code for d in value['params']['diagnostics']))
                    and (snapshot is None or any((d.get('data') or {}).get('snapshot') == snapshot
                                                 for d in value['params']['diagnostics']))
                    and (contains is None or any(contains in d['message'] for d in value['params']['diagnostics'])))
        # Bounded #run execution permits at least 30 seconds for helper
        # compilation. Leave time for that checker result and its notification.
        deadline = time.monotonic() + timeout
        while True:
            for value in self.notifications:
                if matching(value):
                    self.notifications.remove(value)
                    return value['params']['diagnostics']
            try:
                self.next(max(0.01, deadline - time.monotonic()))
            except queue.Empty:
                reports = [value['params']['diagnostics'] for value in self.notifications
                           if value.get('method') == 'textDocument/publishDiagnostics'
                           and value['params'].get('uri') == uri]
                raise AssertionError('Timed out waiting for matching diagnostics. '
                                     'Recent reports '+json.dumps(reports[-3:])[:4000]) from None

    def close(self):
        if self.process.poll() is None:
            try:
                self.request('shutdown', None)
                self.send('exit', None)
                self.process.wait(timeout=5)
            except Exception:
                self.process.kill()
                self.process.wait(timeout=5)
        self.process.stdin.close()
        self.process.stdout.close()
        self.reader.join(timeout=1)
        self.log.close()


class ClientTimeoutTests(unittest.TestCase):
    def test_diagnostic_timeout_reports_unexpected_diagnostics(self):
        client = Client.__new__(Client)
        uri = 'file:///fixture/main.thor'
        client.notifications = [{'method':'textDocument/publishDiagnostics',
                                 'params':{'uri':uri,'diagnostics':[{'message':'compiler failed'}]}}]
        with patch.object(client, 'next', side_effect=queue.Empty):
            with self.assertRaisesRegex(AssertionError, 'compiler failed'):
                client.diagnostic(uri, empty=True)


class TLSTests(unittest.TestCase):
    def test_removed_conditional_directive_errors_and_unsaved_native_when_recovery(self):
        for prefix, needle in [('#if true', 'if'), ('when false {} #else', 'else')]:
            with self.subTest(prefix=prefix):
                text='// 😀 original header\npackage main\n'+prefix+' {VALUE::42}\nmain::proc(){_=VALUE}\n'
                client,main=self.open(text)
                errors=client.diagnostic(main.as_uri(),contains="got '"+needle+"'")
                error=next(e for e in errors if "got '"+needle+"'" in e['message'])
                self.assertEqual(error['range']['start'],self.position(text,'#'+needle,1))
                fixed=text.replace('#if','when').replace('#else','else')
                client.notifications.clear()
                client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':fixed}]})
                client.diagnostic(main.as_uri(),empty=True)
                self.assertIn('42',json.dumps(self.hover(client,main,fixed,'VALUE}')))
                self.assert_original_identifier_tokens(main,fixed,self.semantic_spans(client,main,fixed))

    def test_type_operand_error_related_declaration_and_unsaved_recovery(self):
        text='''// 😀 header
package main
main::proc(){
    /* 😀 */ Rows::[3]struct{x:int}
    i:=1
    /* 😀 */ Rows[i]={1}
}
'''
        client,main=self.open(text)
        errors=client.diagnostic(main.as_uri(),contains='it names a type')
        self.assertEqual(len(errors),1,errors)
        error=errors[0]
        start=self.position(text,'Rows[i]',0)
        self.assertEqual(error['range'],{'start':start,'end':{**start,'character':start['character']+4}})
        note=error['relatedInformation'][0]
        self.assertEqual(note['location']['uri'],main.as_uri())
        self.assertEqual(note['location']['range']['start'],self.position(text,'Rows::',0))
        self.assertIn('declared as a type',note['message'])
        self.assertNotIn('Thor expression',json.dumps(errors))
        for version in range(2,6):
            changed=text.replace('Rows::','Rows:') if version%2==0 else text
            client.notifications.clear()
            client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':version},'contentChanges':[{'text':changed}]})
            client.diagnostic(main.as_uri(),empty=True) if version%2==0 else client.diagnostic(main.as_uri(),contains='it names a type')

    def test_type_operand_imported_unsaved_related_location(self):
        text='package main\nimport "records"\nmain::proc(){i:=1;_=records.Rows[i]}\n'
        records='package records\nRows::[3]int\n'
        client,main=self.open(text,files={'records/records.thor':records})
        uri=(main.parent/'records/records.thor').as_uri()
        note=client.diagnostic(main.as_uri(),contains='it names a type')[0]['relatedInformation'][0]
        self.assertEqual(note['location']['uri'],uri)
        self.assertEqual(note['location']['range']['start'],{'line':1,'character':0})
        changed='// 😀 unsaved\n'+records.replace('[3]int','[4]int')
        client.notifications.clear()
        client.send('textDocument/didOpen',{'textDocument':{'uri':uri,'languageId':'thor','version':1,'text':changed}})
        # This client deliberately disables workspace checking; request another
        # dependent-package check while the imported buffer remains unsaved.
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':text}]})
        note=client.diagnostic(main.as_uri(),contains='Rows: [4]int')[0]['relatedInformation'][0]
        self.assertEqual(note['location']['uri'],uri)
        self.assertEqual(note['location']['range']['start'],{'line':2,'character':0})

    def test_multiple_switch_result_hovers_and_original_tokens(self):
        text='''package main
Kind::enum{A,B}
A,B::#switch{case true:1,"global";case:undefined,undefined}
GLOBAL_N,GLOBAL_TEXT:=switch{case true:3,"runtime";case:4,"other"}
pair::proc()->(int,string){return 7,"pair"}
generic_pair::proc(x:$T)->(T,string){return x,"generic"}
main::proc(){
 a,b,k:=switch{case true:2,"ok",Kind.B;case:3,"no",Kind.A}
 c,d:=(switch{case true:pair();case:8,"other"})
 x,y:=#switch{case true:4,"local";case:undefined,undefined}
 p,q:=switch{case true:generic_pair(f32(7));case:generic_pair(f32(8))}
 _=a;_=b;_=k;_=c;_=d;_=x;_=y;_=A;_=B;_=GLOBAL_N;_=GLOBAL_TEXT;_=p;_=q
}
'''
        client,main=self.open(text)
        for word,typ in [('a','int'),('b','string'),('k','Kind'),('c','int'),('d','string'),('x','int'),('y','string'),('A',':: 1'),('B',':: "global"'),('GLOBAL_N','int'),('GLOBAL_TEXT','string'),('p','f32'),('q','string')]:
            with self.subTest(word=word):
                hover=client.request('textDocument/hover',{'textDocument':{'uri':main.as_uri()},'position':self.position(text,'_='+word,2)})
                self.assertIsNotNone(hover)
                self.assertIn(typ,hover['contents']['value'])
                self.assertNotIn('__thor_',hover['contents']['value'])
        self.assert_original_identifier_tokens(main,text,self.semantic_spans(client,main,text))

    def test_multiple_switch_context_uses_corresponding_enum_result(self):
        header='package main\nColor::enum{Red,Blue}\nOther::enum{Red,Green}\n'
        for body in [
            'main::proc(){a:int;b:Color;a,b=switch{case true:1,.R{*}ed;case:2,.Blue};_=a;_=b}',
            'f::proc()->(int,Color){return switch{case true:1,.R{*}ed;case:2,.Blue}}\nmain::proc(){}',
            'main::proc(){a,b:=switch{case true:1,.R{*}ed;case:2,Color.Blue};_=a;_=b}',
            'main::proc(){a:int;b:Color;a,b=switch{case true:1,.R{*}ed;case:break};_=a;_=b}',
            'take::proc(a:int,b:Color){}\nmain::proc(){take(switch{case true:1,.R{*}ed;case:2,.Blue})}',
        ]:
            with self.subTest(body=body):self.assert_contextual_enum(header+body+'\n')

    def test_multiple_switch_second_result_diagnostics_and_recovery(self):
        text='package main\nmain::proc(){a:int;b:string;a,b=switch{case true:1,"ok";case:2,"other"};_=a;_=b}\n'
        client,main=self.open(text)
        broken=text.replace('1,"ok"','1,99')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':broken}]})
        errors=client.diagnostic(main.as_uri(),contains='Cannot')
        error=next(e for e in errors if 'Cannot' in e['message'])
        self.assertEqual(error['range']['start'],self.position(broken,'99',0))
        self.assertNotIn('__thor_',error['message'])
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)

    def test_using_promoted_switch_field_context_hover_navigation_and_diagnostics(self):
        text='''package main
Kind::enum{A,B}
Other::enum{A,C}
Record::struct{kind:Kind}
write::proc(using record:^Record,selector:bool){
 kind=switch{
  case selector:.A
  case:true?.B:.A
 }
}
main::proc(){record:=Record{};write(&record,true)}
'''
        client,main=self.open(text)
        hover=self.hover(client,main,text,'kind=switch')
        self.assertIn('Kind',json.dumps(hover))
        position=self.position(text,'case selector:.A',len('case selector:.'))
        hover=client.request('textDocument/hover',{'textDocument':{'uri':main.as_uri()},'position':position})
        self.assertIn('Kind: .A',json.dumps(hover))
        definitions=client.request('textDocument/definition',{'textDocument':{'uri':main.as_uri()},'position':position})
        if isinstance(definitions,dict):definitions=[definitions]
        self.assertTrue(any(item['range']['start']==self.position(text,'A,B',0) for item in definitions),definitions)
        self.assert_original_identifier_tokens(main,text,self.semantic_spans(client,main,text))
        broken=text.replace('case selector:.A','case selector:"wrong"')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':broken}]})
        errors=client.diagnostic(main.as_uri(),contains='Cannot')
        error=next(error for error in errors if 'Cannot' in error['message'])
        self.assertEqual(error['range']['start'],self.position(broken,'"wrong"',0))
        self.assertIn('Kind',error['message'])
        self.assertNotIn('__thor_',error['message'])
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)

    def test_automatic_using_feature_hover_rename_and_unsaved_diagnostic_locations(self):
        text='''// 😀 original header
package main
Record::struct{x:int}
read::proc(using record:Record)->int{return x}
main::proc(){
 record:=Record{4}
 using record
 value:=read(record)
 _=value
}
'''
        client,main=self.open(text)
        self.assertIn('value: int',json.dumps(self.hover(client,main,text,'value:=')))
        member=client.request('textDocument/hover',{'textDocument':{'uri':main.as_uri()},
                              'position':self.position(text,'return x',len('return '))})
        self.assertIn('x: int',json.dumps(member))
        self.assert_original_identifier_tokens(main,text,self.semantic_spans(client,main,text))
        renamed=client.request('textDocument/rename',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(text,'value:='),'newName':'answer'})
        self.assertEqual(len(renamed['changes'][main.as_uri()]),2,renamed)
        broken=text.replace('_=value','_=missing')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':broken}]})
        errors=client.diagnostic(main.as_uri(),contains='Undeclared name: missing')
        error=next(e for e in errors if 'Undeclared name: missing' in e['message'])
        self.assertEqual(error['range']['start'],self.position(broken,'missing',0))
        self.assertNotIn('__thor_',error['message'])
        fixed=broken.replace('missing','value')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':fixed}]})
        client.diagnostic(main.as_uri(),empty=True)
        self.assertEqual(main.read_text(),text)

    def test_membership_context_hovers_navigation_and_completion(self):
        text='''package main
Color::enum{Red,Blue}
Record::struct{value:i8}
main::proc(){
 small:i8=4
 found:=small in [4,8]
 color:Color=.Red
 if color in [.Red,.Blue] {}
 record:=Record{4}
 if record in [{value=4},{value=8}] {}
 values:=[1,2,3]
 reverse:=6 in values
 colors:[2]Color={.Red,.Blue}
 reverse_enum:=.Blue in colors
 _=found;_=reverse;_=reverse_enum
}
'''
        client,main=self.open(text)
        for word,expected in [('small:i8','small: i8'),('found:=','found: bool'),
                              ('values:=','[3]int'),('reverse:=','reverse: bool')]:
            self.assertIn(expected,json.dumps(self.hover(client,main,text,word)))
        for fragment,expected in [('[.Red,.Blue]','Color: .Red'),('[{value=4}','value: i8')]:
            offset=2 if fragment.startswith('[.') else len('[{v')
            hover=client.request('textDocument/hover',{'textDocument':{'uri':main.as_uri()},
                                  'position':self.position(text,fragment,offset)})
            self.assertIn(expected,json.dumps(hover))
        definitions=client.request('textDocument/definition',{'textDocument':{'uri':main.as_uri()},
                                    'position':self.position(text,'[{value=4}',len('[{v'))})
        if isinstance(definitions,dict):definitions=[definitions]
        self.assertTrue(any(item['range']['start']==self.position(text,'value:i8',0) for item in definitions),definitions)
        self.assert_original_identifier_tokens(main,text,self.semantic_spans(client,main,text))
        incomplete=text.replace('[.Red,.Blue]','[.R,.Blue]')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':incomplete}]})
        completion=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},
                                    'position':self.position(incomplete,'[.R,.Blue]',3)})
        self.assertIn('Red',[item['label'] for item in completion['items']])
        reverse_hover=client.request('textDocument/hover',{'textDocument':{'uri':main.as_uri()},
                                      'position':self.position(incomplete,'reverse_enum:=.Blue',len('reverse_enum:=.')+1)})
        self.assertIn('Color: .Blue',json.dumps(reverse_hover))

    def test_membership_literal_overflow_unsaved_diagnostics_and_type_mismatch_recovery(self):
        text='package main\nmain::proc(){/* 😀 */ value:i8=4;found:=value in [4,128];_=found}\n'
        client,main=self.open(text)
        errors=client.diagnostic(main.as_uri(),contains='128')
        error=next(error for error in errors if '128' in error['message'])
        self.assertEqual(error['range']['start'],self.position(text,'128',0))
        self.assertNotIn('__thor_',error['message'])
        fixed=text.replace('[4,128]','[4,8]')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':fixed}]})
        client.diagnostic(main.as_uri(),empty=True)
        broken=fixed.replace('[4,8]','[4,i16(8)]')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':broken}]})
        errors=client.diagnostic(main.as_uri(),contains='Cannot')
        error=next(error for error in errors if 'Cannot' in error['message'])
        self.assertEqual(error['range']['start'],self.position(broken,'i16(8)',0))
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':4},'contentChanges':[{'text':fixed}]})
        client.diagnostic(main.as_uri(),empty=True)
        self.assertEqual(main.read_text(),text)

    def test_switch_panic_inference_context_hover_and_unsaved_diagnostic_recovery(self):
        text='''package main
Record::struct{value:int}
Color::enum{Red,Blue}
identity::proc(value:$T)->T{return value}
main::proc(){
 selector:=1
 value:=switch selector{case 0:panic("first");case:Record{7}}
 contextual:Record>switch{case selector==1:{7};case:panic("unreachable")}
 color:Color=switch{case false:panic("enum");case:.Red}
 generic:=switch{case true:identity(i32(7));case:panic("generic")}
 _=value.value;_=contextual.value;_=color;_=generic
}
'''
        client,main=self.open(text)
        for name in ['value:=','contextual:Record']:
            self.assertIn('Record',json.dumps(self.hover(client,main,text,name)))
        member=client.request('textDocument/hover',{'textDocument':{'uri':main.as_uri()},
                              'position':self.position(text,'value.value',len('value.')+1)})
        self.assertIn('int',json.dumps(member))
        self.assertIn('i32',json.dumps(self.hover(client,main,text,'generic:=')))
        self.assertIn('Color',json.dumps(self.hover(client,main,text,'.Red')))
        self.assert_original_identifier_tokens(main,text,self.semantic_spans(client,main,text))
        broken=text.replace('panic("unreachable")','panic(123)')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':broken}]})
        errors=client.diagnostic(main.as_uri(),contains='Cannot')
        error=next(error for error in errors if 'Cannot' in error['message'])
        self.assertEqual(error['range']['start'],self.position(broken,'123',0))
        self.assertNotIn('__thor_',error['message'])
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)

    def test_switch_user_proc_named_panic_still_supplies_hover_type(self):
        text='''package main
panic::proc(message:string)->int{return 7}
main::proc(){
 value:=switch{case:panic("ordinary value")}
 _=value
}
'''
        client,main=self.open(text,compiler=False)
        hover=self.hover(client,main,text,'value:=')
        self.assertIn('value: int',json.dumps(hover))

    def test_untyped_string_repetition_clears_cstring_argument_error_without_saving(self):
        text='''package main
PAD::string(" ")*3
TITLE::"Title"
take::proc(value:cstring){}
main::proc(){_="😀";take(PAD+TITLE+" (animation off)")}
'''
        client,main=self.open(text)
        errors=client.diagnostic(main.as_uri(),contains="to 'cstring'")
        error=next(e for e in errors if "to 'cstring'" in e['message'])
        self.assertEqual(error['range']['start'],self.position(text,'PAD+TITLE',0))
        fixed=text.replace('string(" ")*3','" "*3')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':fixed}]})
        client.diagnostic(main.as_uri(),empty=True)
        self.assert_original_identifier_tokens(main,fixed,self.semantic_spans(client,main,fixed))

    def test_string_repetition_hover_branch_selection_and_mapped_diagnostic_recovery(self):
        text='''package main
X::"a"*3
Y::4*X+"z"
when "a"*3+"b"=="aaab" { SELECTED::"ok" } else { SELECTED::17 }
main::proc(){
    value:=3*"ab"
    _=value;_=Y;_=SELECTED
}
'''
        client,main=self.open(text)
        self.assertIn('value: string',json.dumps(self.hover(client,main,text,'value:=')))
        self.assertIn('X ::',json.dumps(self.hover(client,main,text,'X::')))
        self.assertIn('Y ::',json.dumps(self.hover(client,main,text,'Y::')))
        selected=json.dumps(self.hover(client,main,text,'SELECTED\n'))
        self.assertIn('ok',selected)
        self.assertNotIn('17',selected)
        self.assert_original_identifier_tokens(main,text,self.semantic_spans(client,main,text))
        broken=text.replace('value:=3*"ab"','_="😀"; value:="ab"*(-1)')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':broken}]})
        errors=client.diagnostic(main.as_uri(),contains='nonnegative count')
        error=next(e for e in errors if 'nonnegative count' in e['message'])
        self.assertEqual(error['range']['start'],self.position(broken,'"ab"*(-1)',0))
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)

    def test_labels_have_a_distinct_consistent_category_across_layouts_and_branch_forms(self):
        text='''package main
maybe::proc()->(int,bool) #optional_ok{return 1,true}
main::proc(){
    row_check:=42
    row_check:
    for i in 0..<2 {
        _=row_check
        nested: for j in 0..<2 {
            if j==0 do continue row_check
            break nested
        }
    }
    block_label: { break block_label }
    conditional: if true { break conditional }
    chooser: switch 1 {case: break chooser}
    choice:any
    typed: switch v in choice {case int:break typed;case:break typed}
    optional: for {
        _=maybe() or_break optional
        _=maybe() or_continue optional
        break optional
    }
    _="😀"; café: for { break café }
    // break row_check
    _="continue row_check"
}
'''
        client,main=self.open(text,compiler=False)
        readonly=1 << client.capabilities['semanticTokensProvider']['legend']['tokenModifiers'].index('readonly')
        comptime=1 << client.capabilities['semanticTokensProvider']['legend']['tokenModifiers'].index('thorCompileTime')
        def check(source):
            spans=self.semantic_spans(client,main,source)
            self.assert_original_identifier_tokens(main,source,spans)
            counts={}
            for _,_,kind,name,mods in spans:
                if kind!='label':continue
                self.assertTrue(mods&readonly,name)
                self.assertFalse(mods&comptime,name)
                counts[name]=counts.get(name,0)+1
            self.assertEqual(counts,{'row_check':2,'nested':2,'block_label':2,'conditional':2,
                                     'chooser':2,'typed':3,'optional':4,'café':2})
            values=[(kind,mods) for _,_,kind,name,mods in spans if name=='row_check' and kind!='label']
            self.assertEqual(values,[('variable',0),('variable',0)])
        check(text)
        flat=text.replace('row_check:\n    for','row_check: for')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':flat}]})
        check(flat)
        renamed=client.request('textDocument/rename',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(flat,'row_check: for'),'newName':'scan_rows'})
        self.assertEqual(len(renamed['changes'][main.as_uri()]),2)
        self.assertIn('label',json.dumps(self.hover(client,main,flat,'row_check: for')))

    def test_parameters_and_value_loop_bindings_are_readonly_but_reference_bindings_are_not(self):
        text='''package main
visit::proc(input:[]int, pointer:^int, pointers:[]^int, enabled:=true,
            accept:=proc(candidate:int)->bool{return candidate>0})->(total:int){
    frozen:>1
    mutable:=1
    copied_pointer:=pointer
    for pointer_item in pointers { _=pointer_item }
    for &pointer_ref in pointers { _=pointer_ref }
    for value,index in input { _=value; _=index; total+=value }
    for &cell,ref_index in input { cell+=1; _=cell; _=ref_index }
    for number in 0..<3 { _=number }
    #unroll for unrolled in 0..<3 { _=unrolled }
    for letter,offset in "abc" { _=letter; _=offset }
    text:string
    for typed_letter,typed_offset in text { _=typed_letter; _=typed_offset }
    table:map[string]int
    for key,entry in table { _=key; _=entry }
    for ref_key,&entry_ref in table { _=ref_key; entry_ref+=1; _=entry_ref }
    growing:[dynamic]int
    for dynamic_value in growing { _=dynamic_value }
    fixed:[2]int
    for fixed_value in fixed { _=fixed_value }
    for &fixed_ref in fixed { fixed_ref+=1; _=fixed_ref }
    names:set[string]
    for set_value in names { _=set_value }
    Shade::enum{Red,Blue}
    flags:bit_set[Shade]
    for shade in flags { _=shade }
    for category in Shade { _=category }
    for counter:=0; counter<2; counter+=1 { _=counter }
    _=input; _=pointer; _=enabled; _=accept; _=accept(1)
    _=frozen; _=mutable; _=copied_pointer; _=pointers
    return
}
generic::proc(values:[]$T){
    for element in values { _=element }
    for &element_ref in values { _=element_ref }
}
read_pure::fn(pure_input:int)->int{return pure_input}
'''
        client,main=self.open(text,compiler=False)
        legend=client.capabilities['semanticTokensProvider']['legend']['tokenModifiers']
        readonly=1 << legend.index('readonly')
        comptime=1 << legend.index('thorCompileTime')
        immutable={'input','pointer','pointers','pointer_item','enabled','accept','candidate','value','index','ref_index','number','unrolled',
                   'letter','offset','key','entry','ref_key','dynamic_value','fixed_value','values','element','frozen'}
        immutable|={'typed_letter','typed_offset','set_value','shade','category','pure_input'}
        mutable={'cell','entry_ref','fixed_ref','element_ref','mutable','counter','total','copied_pointer','pointer_ref'}
        spans=self.semantic_spans(client,main,text)
        self.assert_original_identifier_tokens(main,text,spans)
        found={name:0 for name in immutable|mutable}
        for _,_,kind,name,modifiers in spans:
            if name not in found or kind=='function':continue # Calling a callback retains the call color.
            self.assertIn(kind,{'variable','parameter'},(name,kind))
            self.assertEqual(bool(modifiers & readonly),name in immutable,(name,kind,modifiers))
            self.assertFalse(modifiers & comptime,(name,kind,modifiers))
            found[name]+=1
        self.assertTrue(all(count>=2 for count in found.values()),found)

    def test_struct_member_colors_follow_value_storage_and_shallow_reference_boundaries(self):
        text='''package main
Leaf::struct{value:int}
Node::struct{nested:Leaf,pointer:^Leaf,fixed:[2]Leaf,view:[]Leaf,table:map[int]Leaf}
ByValue::struct{using leaf:Leaf}
ByPointer::struct{using leaf:^Leaf}
global_frozen:>Leaf{1}
global_copy:=global_frozen
inspect::proc(node:Node,pointer:^Node,items:[]Node,fixed:[2]Node,
              promoted:ByValue,indirect:ByPointer){
    _=node.nested.value
    _=(node).nested.value
    _=node.pointer.value
    _=node.fixed[0].value
    _=node.view[0].value
    _=node.table[0].value
    _=pointer.nested.value
    _=pointer^.nested.value
    _=items[0].nested.value
    _=fixed[0].nested.value
    _=promoted.value
    _=indirect.value
    frozen:>node
    _=frozen.nested.value
    _=frozen.pointer.value
    _=frozen.fixed[0].value
    _=frozen.view[0].value
    _=frozen.table[0].value
    mutable:=frozen
    _=mutable.nested.value
    _=global_frozen.value
    _=global_copy.value
    for value_node in items { _=value_node.nested.value }
    for &reference_node in items { _=reference_node.nested.value }
    for pointer_node in [pointer] { _=pointer_node.nested.value }
    { node:=node; _=node.nested.value }
    _=node.nested.value
    node.nested.value=1 // Still readonly even in an invalid write.
}
'''
        client,main=self.open(text,compiler=False)
        readonly=1 << client.capabilities['semanticTokensProvider']['legend']['tokenModifiers'].index('readonly')
        spans=self.semantic_spans(client,main,text)
        self.assert_original_identifier_tokens(main,text,spans)
        by_start={start:(kind,mods) for start,_,kind,_,mods in spans}
        for fragment,expected in [
            ('_=node.nested.value',[True,True]),
            ('_=(node).nested.value',[True,True]),
            ('_=node.pointer.value',[True,False]),
            ('_=node.fixed[0].value',[True,True]),
            ('_=node.view[0].value',[True,False]),
            ('_=node.table[0].value',[True,False]),
            ('_=pointer.nested.value',[False,False]),
            ('_=pointer^.nested.value',[False,False]),
            ('_=items[0].nested.value',[False,False]),
            ('_=fixed[0].nested.value',[True,True]),
            ('_=promoted.value',[True]),
            ('_=indirect.value',[False]),
            ('_=frozen.nested.value',[True,True]),
            ('_=frozen.pointer.value',[True,False]),
            ('_=frozen.fixed[0].value',[True,True]),
            ('_=frozen.view[0].value',[True,True]),
            ('_=frozen.table[0].value',[True,True]),
            ('_=mutable.nested.value',[False,False]),
            ('_=global_frozen.value',[True]),
            ('_=global_copy.value',[False]),
            ('_=value_node.nested.value',[True,True]),
            ('_=reference_node.nested.value',[False,False]),
            ('_=pointer_node.nested.value',[False,False]),
            ('{ node:=node; _=node.nested.value }',[False,False]),
            ('node.nested.value=1',[True,True]),
        ]:
            start=text.index(fragment)
            fields=[(kind,bool(mods&readonly)) for offset,(kind,mods) in sorted(by_start.items())
                    if start<=offset<start+len(fragment) and kind=='property']
            self.assertEqual(fields,[('property',value) for value in expected],fragment)

    def test_readonly_imported_generic_members_update_with_unsaved_pointer_edit_and_diagnostics(self):
        text='''package main
import "library"
read::proc(node:library.Box(library.Leaf)){node.item.value=1}
main::proc(){}
'''
        library='package library\nLeaf::struct{value:int}\nBox::struct($T:typeid){item:T}\n'
        client,main=self.open(text,files={'library/types.odin':library})
        readonly=1 << client.capabilities['semanticTokensProvider']['legend']['tokenModifiers'].index('readonly')
        def field_colors(source):
            return [(name,bool(mods&readonly)) for _,_,kind,name,mods in self.semantic_spans(client,main,source)
                    if kind=='property' and name in {'item','value'}]
        self.assertEqual(field_colors(text),[('item',True),('value',True)])
        errors=client.diagnostic(main.as_uri(),contains='procedure parameter')
        error=next(e for e in errors if 'procedure parameter' in e['message'])
        self.assertEqual(error['range']['start']['line'],2)
        write_start=text.splitlines()[2].index('node.item.value')
        self.assertTrue(write_start<=error['range']['start']['character']<write_start+len('node.item.value'))
        self.assertIn('value: int',json.dumps(self.hover(client,main,text,'value=1')))
        definition=client.request('textDocument/definition',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(text,'value=1')})
        self.assertIn('library/types.odin',json.dumps(definition))
        pointer=text.replace('node:library','node:^library')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':pointer}]})
        client.diagnostic(main.as_uri(),empty=True)
        self.assertEqual(field_colors(pointer),[('item',False),('value',False)])
        restored=text.replace('node.item.value=1','_=node.item.value; frozen:>node; _=frozen.item.value; mutable:=frozen; _=mutable.item.value')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':restored}]})
        client.diagnostic(main.as_uri(),empty=True)
        self.assertEqual(field_colors(restored),[('item',True),('value',True),('item',True),('value',True),('item',False),('value',False)])

    def test_type_switch_binding_colors_follow_value_and_reference_cases(self):
        text='''package main
Leaf::struct{field:int}
Choice::union{int,f64,Leaf,^Leaf}
inspect::proc(input:Choice,any_value:any){
    source:=input
    switch value in source {
    case int:
        value=2 // An invalid write must keep the original readonly color.
        _=value
        copy:=value;copy=3;_=copy
    case f64: _=value
    case Leaf: _=value.field
        switch 1 {case 1: _=value.field}
    case ^Leaf: _=value.field
    case nil: _=value
    case: _=value
    }
    switch &reference in source {
    case int: reference=2;_=reference
    case Leaf: reference.field=2;_=reference.field
    case: _=reference
    }
    switch many in source {
    case int,f64: _=many
    case: _=many
    }
    switch checked in any_value {
    case int: _=checked
    case: _=checked
    }
    switch shadow in source {
    case int:
        { shadow:=1;shadow+=1;_=shadow }
        _=shadow
    case: _=shadow
    }
    _=source
}
'''
        client,main=self.open(text,compiler=False)
        readonly=1 << client.capabilities['semanticTokensProvider']['legend']['tokenModifiers'].index('readonly')
        spans=self.semantic_spans(client,main,text)
        self.assert_original_identifier_tokens(main,text,spans)
        for name,expected in [('value',True),('reference',False),('many',True),('checked',True),('copy',False),('source',False)]:
            occurrences=[(kind,bool(mods&readonly)) for _,_,kind,word,mods in spans if word==name]
            self.assertGreaterEqual(len(occurrences),3,name)
            self.assertTrue(all(kind=='variable' and ro==expected for kind,ro in occurrences),(name,occurrences))
        self.assertEqual([bool(mods&readonly) for _,_,_,name,mods in spans if name=='shadow'],
                         [True,False,False,False,True,True])
        by_start={start:(kind,mods) for start,_,kind,_,mods in spans}
        for fragment,expected in [('case Leaf: _=value.field',True),('case ^Leaf: _=value.field',False),
                                  ('case Leaf: reference.field=2',False)]:
            at=text.index(fragment)+fragment.index('field')
            kind,mods=by_start[at]
            self.assertEqual((kind,bool(mods&readonly)),('property',expected),fragment)
        for fragment,expected in [('_=value\n',r'value: int\b'),('_=value.field\n',r'value: (?:[\w-]+\.)?Leaf\b')]:
            # For member access, ask on the bound value rather than its field.
            hover=client.request('textDocument/hover',{'textDocument':{'uri':main.as_uri()},
                'position':self.position(text,fragment,3)})
            self.assertRegex(json.dumps(hover),expected)
        renamed=client.request('textDocument/rename',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(text,'value in'),'newName':'selected'})
        edits=renamed['changes'][main.as_uri()]
        self.assertEqual(len(edits),sum(name=='value' for _,_,_,name,_ in spans))

    def test_type_switch_readonly_diagnostic_and_color_clear_after_unsaved_reference_edit(self):
        text='''package main
Possibility_Ary::[9]bool
Cell::union{int,Possibility_Ary}
main::proc(){
    board:[9][9]Cell
    for y in 0..<9 {
        switch v in board[y][0] {
        case int: v=2
        case Possibility_Ary: _=v[0]
        case: _=v
        }
        switch grouped in board[y][0] {
        case int,Possibility_Ary: _=grouped
        case: _=grouped
        }
    }
}
'''
        client,main=self.open(text)
        readonly=1 << client.capabilities['semanticTokensProvider']['legend']['tokenModifiers'].index('readonly')
        def binding_colors(source):
            return [(kind,bool(mods&readonly)) for _,_,kind,name,mods in self.semantic_spans(client,main,source) if name=='v']
        self.assertEqual(binding_colors(text),[('variable',True)]*4)
        self.assertEqual([bool(mods&readonly) for _,_,_,name,mods in self.semantic_spans(client,main,text) if name=='grouped'],[True]*3)
        errors=client.diagnostic(main.as_uri(),contains="Cannot assign to 'v'")
        error=next(e for e in errors if "Cannot assign to 'v'" in e['message'])
        self.assertEqual(error['range']['start'],self.position(text,'v=2',0))
        reference=text.replace('switch v in','switch &v in')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':reference}]})
        client.diagnostic(main.as_uri(),empty=True)
        self.assertEqual(binding_colors(reference),[('variable',False)]*4)
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),contains="Cannot assign to 'v'")
        self.assertEqual(binding_colors(text),[('variable',True)]*4)

    def test_binding_readonly_color_follows_shadowing_and_unsaved_loop_edits(self):
        text='''package main
read::proc(value:int,items:[]int){
    _=value
    { value:=2; _=value }
    for value in items { _=value }
    _=value
}
'''
        client,main=self.open(text,compiler=False)
        readonly=1 << client.capabilities['semanticTokensProvider']['legend']['tokenModifiers'].index('readonly')
        def modifiers(source):
            return [bool(mods & readonly) for _,_,_,name,mods in self.semantic_spans(client,main,source) if name=='value']
        self.assertEqual(modifiers(text),[True,True,False,False,True,True,True])
        reference=text.replace('for value in','for &value in')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':reference}]})
        self.assertEqual(modifiers(reference),[True,True,False,False,False,False,True])
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        self.assertEqual(modifiers(text),[True,True,False,False,True,True,True])

    def test_compile_time_color_marker_does_not_reclassify_runtime_bindings(self):
        text='''package main
import "library"
COUNT::12
ALIAS::COUNT
Record::struct{x:int}
VALUE::Record{x=1}
make_record::proc()->Record{return {x=2}}
RUN_VALUE::#run make_record()
callback::make_record
main::proc(pointer:^int){
    LOCAL::7
    immutable:>COUNT
    mutable:=COUNT
    frozen:>ALIAS
    values:=[1,2]
    for item in values{_=item}
    for &item in values{item+=1}
    _=pointer;_=LOCAL;_=immutable;_=mutable;_=frozen
    _=VALUE;_=RUN_VALUE;_=callback();_=library.EXPORTED
}
'''
        client,main=self.open(text,files={'library/main.odin':'package library\nEXPORTED::3\n'},compiler=False)
        legend=client.capabilities['semanticTokensProvider']['legend']['tokenModifiers']
        marker=1 << legend.index('thorCompileTime')
        positive={'COUNT','ALIAS','VALUE','RUN_VALUE','LOCAL','EXPORTED'}
        negative={'Record','make_record','callback','main','pointer','immutable','mutable','frozen','values','item','library'}
        found=set()
        for _,_,kind,name,modifiers in self.semantic_spans(client,main,text):
            if name in positive:
                self.assertEqual(kind,'variable',(name,kind))
                self.assertTrue(modifiers & marker,(name,modifiers))
                found.add(name)
            if name in negative:self.assertFalse(modifiers & marker,(name,kind,modifiers))
        self.assertEqual(found,positive)

    def test_immutable_array_slice_read_iteration_clears_reference_iteration_error(self):
        text='''package main
Coord::struct{x,y:int}
directions:>[2]Coord{{1,0},{0,1}}
main::proc(){
    limit:=1
    for &dir in directions[:limit]{dir.x=9}
}
'''
        client,main=self.open(text)
        errors=client.diagnostic(main.as_uri(),contains='read-only slice')
        error=next(e for e in errors if 'read-only slice' in e['message'])
        self.assertEqual(error['range']['start'],self.position(text,'directions[:limit]',0))
        fixed=text.replace('for &dir','for dir').replace('dir.x=9','_=dir.x')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},
                    'contentChanges':[{'text':fixed}]})
        client.diagnostic(main.as_uri(),empty=True)
        self.assert_original_identifier_tokens(main,fixed,self.semantic_spans(client,main,fixed))

    def test_odin_compatibility_diagnostics_follow_unsaved_fix_without_false_errors(self):
        text='''package main
Color::enum{Red,Blue}
make_board::proc()->(board:[2]int){
    i:=-1
    board[i]="wrong"
    return
}
main::proc(){
    values:=make_board()
    switch{case values[-1]==9:}
    defer for i in 0..<2{_=values[i]}
    for color in Color{_=color}
    x:=[1,2];y:=[3,4];zipped:=soa_zip(x=x[:],y=y[:])
    for value in zipped{_=value.x}
}
'''
        client,main=self.open(text)
        errors=client.diagnostic(main.as_uri(),contains='Cannot')
        error=next(e for e in errors if 'Cannot' in e['message'])
        self.assertEqual(error['range']['start'],self.position(text,'"wrong"',0))
        self.assertNotIn('__thor_',error['message'])
        fixed=text.replace('"wrong"','9')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},
                    'contentChanges':[{'text':fixed}]})
        client.diagnostic(main.as_uri(),empty=True)
        self.assert_original_identifier_tokens(main,fixed,self.semantic_spans(client,main,fixed))
        self.assertEqual(main.read_text(),text)

    def test_app_resource_helper_hover_definition_and_mapped_argument_error(self):
        text='''package main
import "thor:app"
main::proc(){
    path:=app.resource_path("assets/image.png")
    defer delete(path)
}
'''
        client,main=self.open(text)
        hover=self.hover(client,main,text,'resource_path')
        self.assertIsNotNone(hover)
        self.assertIn('string',hover['contents']['value'])
        target=client.request('textDocument/definition',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(text,'resource_path',2)})
        targets=target if isinstance(target,list) else [target]
        self.assertTrue(any(t and t['uri'].endswith('/runtime/app/app.odin') for t in targets),target)
        changed=text.replace('"assets/image.png"','123')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},
            'contentChanges':[{'text':changed}]})
        errors=client.diagnostic(main.as_uri(),contains='Cannot')
        self.assertTrue(any(e['range']['start']['line']==3 for e in errors),errors)

    def test_generic_map_optional_lookup_checks_and_unsaved_return_error(self):
        text='''package main
Box::struct{items:map[int]int}
read::proc(box:$B,key:int)->int{
    value,ok:=box.items[key]
    if !ok{return 0}
    return value
}
main::proc(){box:=Box{items=make(map[int]int)};defer delete(box.items);_=read(box,-1)}
'''
        client,main=self.open(text)
        self.assert_original_identifier_tokens(main,text,self.semantic_spans(client,main,text))
        changed=text.replace('return value','return "bad"')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        errors=client.diagnostic(main.as_uri(),contains='Cannot')
        error=next(e for e in errors if 'Cannot' in e['message'])
        self.assertEqual(error['range']['start'],self.position(changed,'"bad"',0))
        self.assertNotIn('__thor_',error['message'])
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)

    def test_graph_library_exports_hover_completion_and_original_diagnostic(self):
        text='''package main
import "thor:basic"
Node::struct{value:int,next:^Node}
main::proc(){
    source:=Node{value=7}
    source.next=&source
    copy,err:=basic.deepclone(&source)
    if err!=.None {panic("clone")}
    free_error:=basic.deepfree(copy)
    _=free_error
}
'''
        client,main=self.open(text)
        for name,typ in [('copy,err:=','^Node'),('free_error:=','Error')]:
            hover=self.hover(client,main,text,name)
            self.assertIsNotNone(hover)
            if typ=='^Node':
                self.assertRegex(hover['contents']['value'],r'\^[^\n]*Node')
            else:
                self.assertIn(typ,hover['contents']['value'])
        for helper in ['deepclone','deepfree']:
            hover=self.hover(client,main,text,helper)
            self.assertIn(helper,hover['contents']['value'])
            completion=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},
                'position':self.position(text,'basic.'+helper,len('basic.')+len(helper)),
                'context':{'triggerKind':1}})
            self.assertIn(helper,{item['label'] for item in completion['items']})
            target=client.request('textDocument/definition',{'textDocument':{'uri':main.as_uri()},
                'position':self.position(text,helper)})
            targets=target if isinstance(target,list) else [target]
            self.assertTrue(any(item and ('runtime/basic/basic.thor' in item['uri'] or
                'runtime/graph/api.odin' in item['uri']) for item in targets),target)
        self.assert_original_identifier_tokens(main,text,self.semantic_spans(client,main,text))
        changed=text.replace('basic.deepclone(&source)','basic.deepclone(source)')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},
            'contentChanges':[{'text':changed}]})
        errors=client.diagnostic(main.as_uri(),contains="Did you mean '&source'?")
        self.assertTrue(any(e['range']['start']['line']==6 for e in errors),errors)
        self.assertNotIn('Thor expression',json.dumps(errors))
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},
            'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)

    def test_parallel_map_and_result_type_query_hovers_and_completion(self):
        text='''package main
import "thor:basic"
import ti "thor:intrinsics"
Job::struct{left,right:int}
calculate::proc(a,b:$T)->(sum,product:T){return a+b,a*b}
Result::ti.result_type_of(calculate(**Job{}))
main::proc(){
    jobs:=[]Job{{2,3},{4,5}}
    rows:=basic.pmap(jobs,calculate)
    defer delete(rows)
    item:Result={5,6}
    _=item.sum
    _=rows[0].product
}
'''
        client,main=self.open(text)
        client.diagnostic(main.as_uri(),empty=True)
        for name in ('Result::','rows:='):
            hover=self.hover(client,main,text,name)
            self.assertIsNotNone(hover,name)
            if name=='Result::':
                self.assertIn('sum',hover['contents']['value'])
                self.assertIn('product',hover['contents']['value'])
                self.assertIn('int',hover['contents']['value'])
            else:self.assertIn('[]struct',hover['contents']['value'])
        for phrase in ('item.sum','rows[0].product'):
            completion=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},
                'position':self.position(text,phrase,phrase.index('.')+1),'context':{'triggerKind':1}})
            self.assertTrue({'sum','product'}<={item['label'] for item in completion['items']},completion)

    def test_parallel_map_aliases_overrides_and_callback_variable_names(self):
        text='''package main
import b "thor:basic"
Job::struct{x:int}
calculate::proc(x:$T)->(number:T,valid:bool){return x*2,true}
Override::struct{value:int,ok:bool}
main::proc(){
    jobs:=[]int{3}
    mapper::b.pmap
    named:=mapper(jobs,calculate,result_type=Override)
    callback:proc(int)->(answer:int,success:bool)=calculate
    indirect:=mapper(jobs,callback)
    _=named[0].value
    _=indirect[0].answer
    delete(named);delete(indirect)
}
'''
        client,main=self.open(text)
        client.diagnostic(main.as_uri(),empty=True)
        for phrase,fields in [('named[0].value',{'value','ok'}),('indirect[0].answer',{'answer','success'})]:
            items=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},
                'position':self.position(text,phrase,phrase.index('.')+1),'context':{'triggerKind':1}})
            self.assertTrue(fields<={item['label'] for item in items['items']},items)

    def test_parallel_map_result_errors_and_unsaved_callback_recovery(self):
        text='''package main
import "thor:basic"
Job::struct{x:int}
calculate::proc(x:$T)->(value:T,ok:bool){return x,true}
main::proc(){
    rows:=basic.pmap([]int{3},calculate)
    _=rows[0].value
    delete(rows)
}
'''
        client,main=self.open(text)
        client.diagnostic(main.as_uri(),empty=True)
        invalid=text.replace('calculate)', 'calculate,result_type=struct{bad:string})')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':invalid}]})
        errors=client.diagnostic(main.as_uri(),contains='result_type must match')
        error=next(error for error in errors if 'result_type must match' in error['message'])
        self.assertEqual(error['range']['start'],self.position(invalid,'basic.pmap',0))
        self.assertNotIn('Thor expression',error['message'])
        changed=text.replace('value:T','number:T').replace('rows[0].value','rows[0].number')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':changed}]})
        client.diagnostic(main.as_uri(),empty=True)
        items=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(changed,'rows[0].number',len('rows[0].')),'context':{'triggerKind':1}})
        names={item['label'] for item in items['items']}
        self.assertTrue({'number','ok'}<=names,names)
        self.assertNotIn('value',names)

    def test_parallel_map_standard_library_overloaded_callback_completion(self):
        text='''package main
import "thor:basic"
import "core:math"
main::proc(){
    rows:=basic.pmap([]f64{4,9},math.sqrt)
    _=rows[0]
    delete(rows)
}
'''
        client,main=self.open(text)
        client.diagnostic(main.as_uri(),empty=True)
        hover=self.hover(client,main,text,'rows:=')
        self.assertIsNotNone(hover)
        self.assertIn('[]f64',hover['contents']['value'])

    def test_parallel_map_inline_polymorphic_callback_fields(self):
        text='''package main
import "thor:basic"
main::proc(){
    rows:=basic.pmap([]int{4},proc(x:$T)->(value:T,ok:bool){return x,true})
    _=rows[0].value
    delete(rows)
}
'''
        client,main=self.open(text)
        client.diagnostic(main.as_uri(),empty=True)
        completion=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(text,'rows[0].value',len('rows[0].')),'context':{'triggerKind':1}})
        self.assertTrue({'value','ok'}<={item['label'] for item in completion['items']},completion)

    def test_compile_time_parallel_map_fields_and_purity_diagnostic_recovery(self):
        text='''package main
import "thor:basic"
GLOBAL:int
calculate::proc(a,b:$T)->(sum:T,ok:bool){return a+b,true}
main::proc(){
    _="😀";rows:=#run basic.pmap([]struct{a,b:int}{{2,3}},calculate)
    _=rows[0].sum
    delete(rows)
}
'''
        client,main=self.open(text)
        client.diagnostic(main.as_uri(),empty=True)
        hover=self.hover(client,main,text,'rows:=')
        self.assertIsNotNone(hover)
        completion=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(text,'rows[0].sum',len('rows[0].')),'context':{'triggerKind':1}})
        self.assertTrue({'sum','ok'}<={item['label'] for item in completion['items']},completion)
        broken=text.replace('return a+b,true','return a+b+GLOBAL,true')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':broken}]})
        errors=client.diagnostic(main.as_uri(),contains='runtime storage')
        error=next(error for error in errors if 'runtime storage' in error['message'])
        self.assertEqual(error['range']['start'],self.position(broken,'basic.pmap',0))
        self.assertNotIn('Thor expression',error['message'])
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)

    def test_data_first_mapping_callbacks_keep_inferred_result_hovers(self):
        text='''package main
import "thor:basic"
twice::proc(x:$T)->T{return x*2}
positive::proc(x:$T)->bool{return x>0}
main::proc(){
    values:=[]int{-2,0,3}
    mapped:=basic.fmap(values,twice)
    kept:=basic.filter(values,positive)
    delete(mapped);delete(kept)
}
'''
        client,main=self.open(text)
        client.diagnostic(main.as_uri(),empty=True)
        for name in ('mapped:=','kept:='):
            with self.subTest(name=name):
                hover=self.hover(client,main,text,name)
                self.assertIsNotNone(hover)
                self.assertIn('[]int',hover['contents']['value'])

    def test_new_basic_helpers_hover_navigation_completion_and_callback_error(self):
        text='''package main
import "thor:basic"
main::proc(){
    numbers:=basic.range(1,8,2)
    kept:=basic.filter(numbers,proc(x:int)->bool{return x>2})
    total:=basic.sum(kept)
    letter:=basic.chr(total)
    code:=basic.ord('界')
    sample:=basic.random(f64,0,1)
    table:=basic.table([]struct{n:int}{{total}})
    delete(numbers);delete(kept);delete(table)
    _=letter;_=code;_=sample
}
'''
        client,main=self.open(text)
        for name,typ in [('numbers:=','[]int'),('kept:=','[]int'),('total:=','int'),
                         ('letter:=','rune'),('code:=','int'),('sample:=','f64'),('table:=','string')]:
            with self.subTest(name=name):
                hover=self.hover(client,main,text,name)
                self.assertIsNotNone(hover)
                self.assertIn(typ,hover['contents']['value'])
        for helper in ['range','filter','sum','chr','ord','random','table']:
            target=client.request('textDocument/definition',{'textDocument':{'uri':main.as_uri()},
                'position':self.position(text,'basic.'+helper,len('basic.')+1)})
            targets=target if isinstance(target,list) else [target]
            self.assertTrue(any(t and t['uri'].endswith('/runtime/basic/basic.thor') for t in targets),target)
            completion=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},
                'position':self.position(text,'basic.'+helper,len('basic.')+len(helper)), 'context':{'triggerKind':1}})
            self.assertIn(helper,{i['label'] for i in completion['items']})
        self.assert_original_identifier_tokens(main,text,self.semantic_spans(client,main,text))
        changed=text.replace('proc(x:int)','proc(x:string)').replace('return x>2','return true')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        errors=client.diagnostic(main.as_uri(),contains='Cannot assign')
        error=next(e for e in errors if 'Cannot assign' in e['message'])
        self.assertEqual(error['range']['start'],self.position(changed,'proc(x:string)',0))
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)

    def test_run_overload_diagnostic_follows_unsaved_argument_and_clears(self):
        text='package main\nimport "thor:basic"\n/* 😀 */ CODE::#run basic.ord("界")\nmain::proc(){_=CODE}\n'
        client,main=self.open(text)
        errors=client.diagnostic(main.as_uri(),contains='runtime storage')
        error=next(e for e in errors if 'runtime storage' in e['message'])
        self.assertEqual(error['range']['start'],self.position(text,'#run basic.ord',0))
        self.assertNotIn('__thor_',error['message'])
        changed=text.replace('ord("界")',"ord('界')")
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        client.diagnostic(main.as_uri(),empty=True)

    def test_collection_conversion_hovers_and_exports_from_sets_and_basic(self):
        text='''package main
import "thor:basic"
import "thor:sets"
main::proc(){
    numbers := [1,2,2]
    unique := basic.to_set(numbers)
    copied := basic.to_slice(unique)
    strings := []string{"oak","elm"}
    words := sets.to_set(strings)
    word_values := sets.to_slice(words)
    growing := make([dynamic]f64)
    append(&growing,1.0,2.0)
    floats := sets.to_set(growing)
    delete(unique);delete(copied);delete(words);delete(word_values);delete(growing);delete(floats)
}
'''
        client,main=self.open(text)
        for name,expected in [('unique :=','set[int]'),('copied :=','[]int'),('words :=','set[string]'),
                              ('word_values :=','[]string'),('floats :=','set[f64]')]:
            with self.subTest(name=name):
                hover=self.hover(client,main,text,name)
                self.assertIsNotNone(hover)
                self.assertIn(expected,hover['contents']['value'])
        for fragment in ['basic.to_slice','sets.to_set']:
            position=self.position(text,fragment,fragment.index('.')+2)
            target=client.request('textDocument/definition',{'textDocument':{'uri':main.as_uri()},'position':position})
            self.assertTrue(target)
        changed=text.replace('basic.to_slice(unique)','basic.to_')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        completion=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(changed,'copied := basic.to_',len('copied := basic.to_')),'context':{'triggerKind':1}})
        labels={i['label'] for i in completion['items']}
        self.assertTrue({'to_set','to_slice'}<=labels,labels)

    def test_fn_ordinary_call_graph_checks_unsaved_leaf_and_clears(self):
        text='package main\nstate:int\nleaf::proc(n:int)->int{/* 😀 */return n+1}\nmid::proc(n:int)->int{return leaf(n)}\nwork::fn(n:int)->int{return mid(n)}\nmain::proc(){n:=3;_=work(n)}\n'
        client,main=self.open(text)
        self.assertIn('proc(n: int) -> int',self.hover(client,main,text,'leaf(n)')['contents']['value'])
        changed=text.replace('return n+1','return state+n')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        errors=client.diagnostic(main.as_uri(),contains='outside the function')
        error=next(e for e in errors if 'outside the function' in e['message'])
        self.assertEqual(error['range']['start'],self.position(changed,'mid(n)',0))
        self.assertTrue(any(note['location']['range']['start']==self.position(changed,'state+n',0)
                            for note in error['relatedInformation']))
        self.assertNotIn('__thor_',json.dumps(errors))
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)
        self.assertIn('proc(n: int) -> int',self.hover(client,main,text,'leaf(n)')['contents']['value'])
        self.assertEqual(main.read_text(),text)

    def test_native_run_standard_math_diagnostic_clears_with_unsaved_fix(self):
        text='package main\nimport "core:math" / round\n/* 😀 */ COUNT :: int(#run round("bad"))\nmain::proc(){data:[COUNT]int;_=data}\n'
        client,main=self.open(text)
        errors=client.diagnostic(main.as_uri(),contains='No procedures or ambiguous call')
        error=next(e for e in errors if 'No procedures or ambiguous call' in e['message'])
        self.assertEqual(error['range']['start'],self.position(text,'round("bad")',0))
        self.assertNotIn('thor_eval_',error['message'])
        self.assertNotIn('__thor_',error['message'])
        changed=text.replace('round("bad")','round(1000 ** .33333)')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        client.diagnostic(main.as_uri(),empty=True)
        hover=self.hover(client,main,changed,'round(1000')
        self.assertIn('round',hover['contents']['value'])
        self.assert_original_identifier_tokens(main,changed,self.semantic_spans(client,main,changed))

    def test_native_run_foreign_diagnostic_is_on_original_source_and_clears(self):
        text='package main\nimport "core:c/libc"\n/* 😀 */ VALUE :: #run libc.rand()\nmain::proc(){_=VALUE}\n'
        client,main=self.open(text)
        errors=client.diagnostic(main.as_uri(),contains='foreign')
        error=next(e for e in errors if 'foreign' in e['message'])
        self.assertEqual(error['range']['start'],self.position(text,'#run libc.rand()',0))
        changed=text.replace('"core:c/libc"','"core:math"').replace('libc.rand()','math.round(3.3)')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        client.diagnostic(main.as_uri(),empty=True)

    def test_wildcard_import_public_hover_definition_completion_and_unused_state(self):
        text='package main\nimport * "library"\nmain::proc(){answer:=chosen(2);counter=3;_=answer;_=counter}\n'
        library='package library\nchosen::proc(value:int)->int{return value}\ncounter:int\nsecret_public::7\n@private\nsecret::7\n'
        client,main=self.open(text,{'library/main.thor':library},unused_imports=True)
        hover=self.hover(client,main,text,'chosen(2)')
        self.assertIn('proc(value: int) -> int',hover['contents']['value'])
        params={'textDocument':{'uri':main.as_uri()},'position':self.position(text,'chosen(2)')}
        definition=client.request('textDocument/definition',params)
        self.assertTrue(definition['uri'].endswith('/library/main.thor'),definition)
        self.assertIn(': int',self.hover(client,main,text,'counter=')['contents']['value'])
        self.assertEqual(self.unused_imports(client,main),[])
        self.assert_original_identifier_tokens(main,text,self.semantic_spans(client,main,text))
        changed='package main\nimport * "library"\nmain::proc(){ cho }\n'
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        completion=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(changed,'cho }',len('cho')),'context':{'triggerKind':1}})
        self.assertIn('chosen',[item['label'] for item in completion['items']])
        changed=changed.replace(' cho }',' sec }')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':changed}]})
        completion=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(changed,'sec }',len('sec')),'context':{'triggerKind':1}})
        labels=[item['label'] for item in completion['items']]
        self.assertIn('secret_public',labels)
        self.assertNotIn('secret',labels)
        changed='package main\nimport * "library"\nmain::proc(){chosen:=2;_=chosen}\n'
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':4},'contentChanges':[{'text':changed}]})
        self.assertEqual(len(self.unused_imports(client,main)),1)

    def test_namespace_free_selections_resolve_distinct_imports_without_namespace(self):
        text='package main\nimport _ "one" / first\nimport _ "two" / second\nmain::proc(){_=first(1);_=second(2)}\n'
        files={'one/main.thor':'package one\nfirst::proc(value:int)->int{return value}\n',
               'two/main.thor':'package two\nsecond::proc(value:int)->int{return value}\n'}
        client,main=self.open(text,files,unused_imports=True)
        for name,folder in [('first','one'),('second','two')]:
            params={'textDocument':{'uri':main.as_uri()},'position':self.position(text,name+'(',2)}
            hover=client.request('textDocument/hover',params)
            self.assertIn('proc(value: int) -> int',hover['contents']['value'])
            self.assertNotIn('__thor_',hover['contents']['value'])
            definition=client.request('textDocument/definition',params)
            self.assertTrue(definition['uri'].endswith('/'+folder+'/main.thor'),definition)
        self.assertEqual(self.unused_imports(client,main),[])
        self.assert_original_identifier_tokens(main,text,self.semantic_spans(client,main,text))
        completed=text[:text.index('main::')]+"main::proc(){\n    \n}\n"
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':completed}]})
        completion=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},
            'position':{'line':4,'character':4},'context':{'triggerKind':1}})
        labels=[item['label'] for item in completion['items']]
        self.assertNotIn('one',labels)
        self.assertNotIn('two',labels)
        self.assertFalse(any('__thor_' in label for label in labels),labels)
        for version,prefix,wanted in [(3,'fir','first'),(4,'sec','second'),(5,'__thor',None)]:
            partial=completed.replace('    \n','    '+prefix+'\n')
            client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':version},'contentChanges':[{'text':partial}]})
            completion=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},
                'position':{'line':4,'character':4+len(prefix)},'context':{'triggerKind':1}})
            labels=[item['label'] for item in completion['items']]
            if wanted: self.assertIn(wanted,labels)
            self.assertFalse(any('__thor_' in label for label in labels),labels)
        changed=text.replace('_=first(1);_=second(2)','_=one.first(1);_=two.second(2)')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':6},'contentChanges':[{'text':changed}]})
        self.assertIsNone(self.hover(client,main,changed,'one.first'))
        self.assertIsNone(self.hover(client,main,changed,'two.second'))

    def test_wildcard_collision_diagnostic_clears_after_unsaved_correction(self):
        text='package main\nimport * "library"\n/* 😀 */ chosen::9\nmain::proc(){}\n'
        client,main=self.open(text,{'library/main.thor':'package library\nchosen::7\n'})
        errors=client.diagnostic(main.as_uri(),contains='conflicts with wildcard import')
        error=next(e for e in errors if 'conflicts with wildcard import' in e['message'])
        self.assertEqual(error['range']['start'],self.position(text,'chosen::',0))
        changed=text.replace('chosen::9','other::9')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        client.diagnostic(main.as_uri(),empty=True)

    def test_extrema_spread_hover_types_and_optional_status(self):
        text = '''package main
import "core:math"
largest :: max
main::proc(){
 values:=[]i16{2,7}
 low, present:=min(..values)
 high:=max(**[2]f32{1,3})
 seeded, seeded_ok:=max(9,..values)
 alias_value, alias_ok:=largest(..values)
 qualified, qualified_ok:=math.min(..values)
 _=low;_=present;_=high;_=seeded;_=seeded_ok;_=alias_value;_=alias_ok;_=qualified;_=qualified_ok
}
'''
        client, main = self.open(text)
        for word, expected in [('low,', 'i16'), ('present:=', 'bool'), ('high:=', 'f32'),
                               ('seeded,', 'i16'), ('seeded_ok:=', 'bool'),
                               ('alias_value,', 'i16'), ('alias_ok:=', 'bool'),
                               ('qualified,', 'i16'), ('qualified_ok:=', 'bool')]:
            hover = self.hover(client, main, text, word)
            self.assertIsNotNone(hover, word)
            self.assertIn(': '+expected, hover['contents']['value'], (word, hover))
        self.assert_original_identifier_tokens(main, text, self.semantic_spans(client, main, text))

    def test_extrema_live_error_at_original_spread_and_unsaved_fix(self):
        text = 'package main\nmain::proc(){/* 😀 */ value:=max(**[0]int{});_=value}\n'
        client, main = self.open(text)
        errors = client.diagnostic(main.as_uri(), contains='empty fixed array')
        error = next(e for e in errors if 'empty fixed array' in e['message'])
        self.assertEqual(error['range']['start'], self.position(text, '[0]int{}', 0))
        changed = text.replace('[0]int{}', '[1]int{7}')
        client.notifications.clear()
        client.send('textDocument/didChange', {'textDocument': {'uri': main.as_uri(), 'version': 2},
                    'contentChanges': [{'text': changed}]})
        client.diagnostic(main.as_uri(), empty=True)
        self.assertIn(': int', self.hover(client, main, changed, 'value:=')['contents']['value'])

    @classmethod
    def setUpClass(cls):
        if not shutil.which(os.environ.get('THOR_ODIN', 'odin')):
            raise unittest.SkipTest('Odin is required for TLS tests')
        subprocess.run(['python3', str(ROOT / 'tools' / 'build_tls.py')], check=True,
                       capture_output=True, timeout=90)

    def open(self, text, files=None, **client_options):
        temporary = tempfile.TemporaryDirectory(prefix='tls-test-')
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name).resolve()
        main = directory / 'main.thor'
        for name, content in {'main.thor': text, **(files or {})}.items():
            destination = directory / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content)
        client = Client(directory, **client_options)
        self.addCleanup(client.close)
        client.send('textDocument/didOpen', {'textDocument': {
            'uri': main.as_uri(), 'languageId': 'thor', 'version': 1, 'text': text}})
        return client, main

    @staticmethod
    def position(text, word, offset=1):
        index = text.index(word) + offset
        preceding = text[:index]
        return {'line': preceding.count('\n'),
                'character': len(preceding.rsplit('\n', 1)[-1].encode('utf-16-le')) // 2}

    def hover(self, client, main, text, word):
        return client.request('textDocument/hover', {
            'textDocument': {'uri': main.as_uri()}, 'position': self.position(text, word)})

    def unused_imports(self, client, main):
        # A request acts as a barrier after didOpen/didChange. Inspect the last
        # publication, not a stale diagnostic left in the notification queue.
        client.request('textDocument/documentSymbol', {'textDocument': {'uri': main.as_uri()}})
        publications = [n for n in client.notifications
                        if n.get('method') == 'textDocument/publishDiagnostics'
                        and n['params']['uri'] == main.as_uri()]
        diagnostics = publications[-1]['params']['diagnostics'] if publications else []
        return [d for d in diagnostics if d.get('code') == 'Unused']

    def test_generic_variadic_loop_values_are_variables_including_body_uses(self):
        text = '''package main
asc :: proc(first: $T, rest: ..T) -> bool {
    previous := first
    for v in rest {
        if previous > v do return false
        previous = v
    }
    return true
}
desc :: proc(first: $T, rest: ..T) -> bool {
    previous := first
    for v in rest {
        if previous < v do return false
        previous = v
    }
    return true
}
'''
        client, main = self.open(text, compiler=False)
        spans = self.semantic_spans(client, main, text)
        self.assertEqual([kind for _, _, kind, token, _ in spans if token == 'v'], ['variable']*6)
        self.assertTrue(all(kind == 'typeParameter' for _, _, kind, token, _ in spans if token == 'T'))
        for occurrence in ['v in rest', 'v do return', 'v\n']:
            hover = client.request('textDocument/hover', {'textDocument': {'uri': main.as_uri()},
                                   'position': self.position(text, occurrence, 0)})['contents']['value']
            self.assertIn('v: T', hover)
            self.assertNotIn('$v', hover)

    def test_generic_container_values_preserve_type_parameter_classification(self):
        text = '''package main
walk :: proc(first: $T, rows: []T, fixed: [2]T, growing: [dynamic]T, table: map[string]T) {
    for item, index in rows { _=item;_=index }
    for item in fixed { _=item }
    for item in growing { _=item }
    for key, value in table { _=key;_=value }
    typed:T=first;copied:=first;frozen:>first
    Alias::T
    another:Alias=first;_=typed;_=copied;_=frozen;_=another
}
'''
        client, main = self.open(text, compiler=False)
        spans = self.semantic_spans(client, main, text)
        for name in ['item', 'index', 'key', 'value', 'typed', 'copied', 'frozen']:
            kinds = [kind for _, _, kind, token, _ in spans if token == name]
            self.assertTrue(kinds, name)
            self.assertTrue(all(kind == 'variable' for kind in kinds), (name, kinds))
        for name in ['T', 'Alias']:
            kinds = [kind for _, _, kind, token, _ in spans if token == name]
            self.assertTrue(kinds, name)
            self.assertTrue(all(kind == 'typeParameter' for kind in kinds), (name, kinds))

    def test_unused_imports_dim_full_declarations_and_follow_unsaved_edits(self):
        text = '''package main
import "core:fmt"
/* 😀 */ import large "core:math/big"
main::proc(){fmt.println("large.Int is only text") /* large.Int is only a comment */}
'''
        client, main = self.open(text, compiler=False, unused_imports=True)
        errors = self.unused_imports(client, main)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]['severity'], 4)
        self.assertEqual(errors[0]['tags'], [1])  # LSP Unnecessary, interpreted by VS Code as dimmed text.
        self.assertEqual(errors[0]['range']['start'], self.position(text, 'import large', 0))
        self.assertEqual(errors[0]['range']['end'], self.position(text, 'import large "core:math/big"',
                                                              len('import large "core:math/big"')))
        used = text.replace('fmt.println(', 'value:large.Int;_=value;fmt.println(')
        for version, changed, expected in [(2, used, 0), (3, text, 1)]:
            client.notifications.clear()
            client.send('textDocument/didChange', {'textDocument': {'uri': main.as_uri(), 'version': version},
                        'contentChanges': [{'text': changed}]})
            self.assertEqual(len(self.unused_imports(client, main)), expected)
        self.assertEqual(main.read_text(), text)
        client.notifications.clear()
        client.send('textDocument/didClose', {'textDocument': {'uri': main.as_uri()}})
        client.diagnostic(main.as_uri(), empty=True)

    def test_unused_selective_imports_and_shadowing(self):
        text = '''package main
import "core:fmt" / println
import math "core:math" / sqrt
main::proc(){println("hello");sqrt::proc(){};sqrt()}
'''
        client, main = self.open(text, compiler=False, unused_imports=True)
        errors = self.unused_imports(client, main)
        self.assertEqual([d['range']['start']['line'] for d in errors], [2])
        self.assertEqual(errors[0]['range']['end'], self.position(text, '/ sqrt', len('/ sqrt')))
        used = text.replace('sqrt::proc(){};sqrt()', '_=sqrt(f64(4))')
        client.notifications.clear()
        client.send('textDocument/didChange', {'textDocument': {'uri': main.as_uri(), 'version': 2},
                    'contentChanges': [{'text': used}]})
        self.assertEqual(self.unused_imports(client, main), [])

    def test_selected_import_names_keep_their_symbol_colors_when_unused(self):
        library = '''package library
Record::struct{value:int}
Kind::enum{A,B}
LIMIT::3
counter:int
single::proc(){}
one::proc(value:int){}
two::proc(value:string){}
overloaded::proc{one,two}
'''
        text = '''package main
import "library" / Record, Kind, LIMIT, counter, single, overloaded
import "core:fmt" / print
main::proc(){}
'''
        client, main = self.open(text, {'library/main.thor': library}, compiler=False, unused_imports=True)
        spans = self.semantic_spans(client, main, text)
        kinds = {name: kind for _, _, kind, name, _ in spans}
        for name, kind in [('Record','struct'), ('Kind','enum'), ('LIMIT','variable'),
                           ('counter','variable'), ('single','function'), ('overloaded','function'),
                           ('print','function')]:
            self.assertEqual(kinds.get(name), kind, name)
        modifiers = client.capabilities['semanticTokensProvider']['legend']['tokenModifiers']
        readonly = 1 << modifiers.index('readonly')
        comptime = 1 << modifiers.index('thorCompileTime')
        by_name = {name: mods for _, _, _, name, mods in spans}
        self.assertFalse(by_name['counter'] & readonly)
        self.assertTrue(by_name['LIMIT'] & comptime)
        self.assertTrue(by_name['single'] & readonly)
        errors = self.unused_imports(client, main)
        self.assertEqual([d['range']['start']['line'] for d in errors], [1, 2])
        for diagnostic, line in zip(errors, [1, 2]):
            self.assertEqual(diagnostic['range']['end'], {'line':line,'character':len(text.splitlines()[line])})
            self.assertEqual(diagnostic['tags'], [1])

    def test_unused_selected_names_follow_live_usage_and_qualified_shadowed_names(self):
        text = '''package main
import "core:fmt" / print,
    println,
    printf
main::proc(){println("hello");fmt.print("qualified");printf:=1;_=printf}
'''
        client, main = self.open(text, compiler=False, unused_imports=True)
        errors = self.unused_imports(client, main)
        for diagnostic, name in zip(errors, ['print,', 'printf\n']):
            self.assertEqual(diagnostic['range']['start'], self.position(text, name, 0))
            self.assertEqual(diagnostic['range']['end'], self.position(text, name, len(name.rstrip(',\n'))))
            self.assertEqual(diagnostic['tags'], [1])
        self.assertEqual(len(errors), 2)
        used = text.replace('printf:=1;_=printf', 'printf("%d",1);print("direct")')
        unused = text.replace('println("hello");fmt.print("qualified");printf:=1;_=printf', '')
        for version, source, expected in [(2,used,0), (3,unused,1), (4,text,2)]:
            client.notifications.clear()
            client.send('textDocument/didChange', {'textDocument':{'uri':main.as_uri(),'version':version},
                        'contentChanges':[{'text':source}]})
            diagnostics = self.unused_imports(client, main)
            self.assertEqual(len(diagnostics), expected)
            if source == unused:
                self.assertEqual(diagnostics[0]['range']['end'], self.position(source, 'printf\n', len('printf')))
            spans = self.semantic_spans(client, main, source)
            imported = [(kind,name) for start, _, kind, name, _ in spans if start < source.index('main::')]
            self.assertEqual(imported, [('function','print'),('function','println'),('function','printf')])

    def test_selected_import_ranges_handle_unicode_crlf_and_eof(self):
        for ending in ['\n','\r\n','']:
            with self.subTest(ending=repr(ending)):
                text = 'package main\n/* 😀 */ import _ "core:fmt" / print' + ending
                client, main = self.open(text, compiler=False, unused_imports=True)
                errors = self.unused_imports(client, main)
                self.assertEqual(len(errors), 1)
                self.assertEqual(errors[0]['range']['start'], self.position(text, 'import _', 0))
                self.assertEqual(errors[0]['range']['end'], self.position(text, '/ print', len('/ print')))
                self.assertIn(('function','print'), [(kind,name) for _,_,kind,name,_ in self.semantic_spans(client,main,text)])

    def test_imports_in_thor_expressions_and_explicit_blank_imports_are_used(self):
        text = '''package main
import "core:fmt"
import "core:math"
import "thor:sets"
import _ "core:math/big"
main::proc(){
    value:=f"nested {f"{math.sqrt(f64(4))}"}"
    selected:=switch {case true:fmt.tprintf("%s",value);case:""}
    names:set[string];sets.insert(&names,selected)
    delete(names);delete(selected);delete(value)
}
'''
        client, main = self.open(text, compiler=False, unused_imports=True)
        self.assertEqual(self.unused_imports(client, main), [])

    def test_unused_imports_ignore_local_package_name_and_synthetic_set_import(self):
        text = '''package main
import "core:math"
import "thor:sets"
main::proc(){math:=struct{x:int}{1};_=math.x;values:=set[int]{1};delete(values)}
'''
        client, main = self.open(text, compiler=False, unused_imports=True)
        errors = self.unused_imports(client, main)
        self.assertEqual([d['range']['start']['line'] for d in errors], [1, 2])

    def test_unused_imports_distinguish_aliases_of_the_same_package(self):
        text = '''package main
import first "core:math"
import second "core:math"
import third "core:math" / PI
main::proc(){_=first.sqrt(f64(4));_=PI}
'''
        client, main = self.open(text, compiler=False, unused_imports=True)
        errors = self.unused_imports(client, main)
        self.assertEqual([d['range']['start']['line'] for d in errors], [2])

    def test_using_import_and_selected_types_count_as_used(self):
        text = '''package main
using import "core:fmt"
import "core:math/big" / Int
main::proc(){value:Int;println(value)}
'''
        client, main = self.open(text, compiler=False, unused_imports=True)
        self.assertEqual(self.unused_imports(client, main), [])

    def test_unused_imports_clear_while_syntax_is_incomplete(self):
        text = 'package main\nimport "core:math/big"\nmain::proc(){}\n'
        client, main = self.open(text, compiler=False, unused_imports=True)
        self.assertEqual(len(self.unused_imports(client, main)), 1)
        client.notifications.clear()
        client.send('textDocument/didChange', {'textDocument': {'uri': main.as_uri(), 'version': 2},
                    'contentChanges': [{'text': text + 'broken::\\\n'}]})
        self.assertEqual(self.unused_imports(client, main), [])

    def test_duplicate_procedure_is_immediate_without_a_compiler_and_clears(self):
        temporary=tempfile.TemporaryDirectory(prefix='tls-redeclaration-');self.addCleanup(temporary.cleanup)
        root=Path(temporary.name).resolve();main=root/'main.thor'
        declaration='allowed_size :: proc($N: int) -> int where N in [2, 4, 8] {return N}\n'
        text='package main\n'+declaration+'main::proc(){}\n';main.write_text(text)
        client=Client(root,compiler=False);self.addCleanup(client.close)
        client.send('textDocument/didOpen',{'textDocument':{'uri':main.as_uri(),'languageId':'thor','version':1,'text':text}})
        client.request('textDocument/documentSymbol',{'textDocument':{'uri':main.as_uri()}})
        client.notifications.clear()
        changed=text+'LABEL::"😀";'+declaration
        start=time.monotonic()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        errors=client.diagnostic(main.as_uri(),code='Declaration')
        self.assertLess(time.monotonic()-start,2,'Redeclarations should not wait for native evaluation')
        self.assertEqual(len(errors),1)
        error=errors[0];needle='LABEL::"😀";allowed_size';offset=len('LABEL::"😀";')
        self.assertEqual(error['range']['start'],self.position(changed,needle,offset))
        self.assertEqual(error['range']['end'],self.position(changed,needle,offset+len('allowed_size')))
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)
        self.assertEqual(main.read_text(),text)
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':4},'contentChanges':[{'text':changed}]})
        client.diagnostic(main.as_uri(),code='Declaration')
        client.request('textDocument/documentSymbol',{'textDocument':{'uri':main.as_uri()}})
        client.notifications.clear()
        client.send('textDocument/didClose',{'textDocument':{'uri':main.as_uri()}})
        client.diagnostic(main.as_uri(),empty=True)

    def test_fast_redeclarations_do_not_flag_branches_or_scoped_shadowing(self):
        text='''package main
when true {VALUE::2}else{VALUE::4}
when true {selected::proc(){}}else{selected::proc(){}}
_::1
_::2
main::proc(){value:=VALUE;{value:=3;_=value};_=value;selected()}
'''
        client,main=self.open(text)
        client.request('textDocument/documentSymbol',{'textDocument':{'uri':main.as_uri()}})
        for value in client.notifications:
            if value.get('method')=='textDocument/publishDiagnostics':
                self.assertFalse(any(e.get('code')=='Declaration' for e in value['params']['diagnostics']))

    def semantic_spans(self,client,main,text):
        result=client.request('textDocument/semanticTokens/full',{'textDocument':{'uri':main.as_uri()}})
        legend=client.capabilities['semanticTokensProvider']['legend']['tokenTypes']
        lines=text.splitlines(keepends=True);line=column=0;previous_end=0;out=[]
        for delta,char,length,kind,modifiers in zip(*[iter(result['data'])]*5):
            line+=delta;column=char if delta else column+char
            if delta:previous_end=0
            self.assertLess(line,len(lines))
            self.assertGreater(length,0)
            self.assertGreaterEqual(column,previous_end,'Overlapping semantic tokens')
            encoded=lines[line].rstrip('\r\n').encode('utf-16-le')
            self.assertLessEqual(2*(column+length),len(encoded))
            prefix=encoded[:column*2].decode('utf-16-le')
            token=encoded[column*2:(column+length)*2].decode('utf-16-le')
            start=len(''.join(lines[:line]).encode())+len(prefix.encode())
            out.append((start,start+len(token.encode()),legend[kind],token,modifiers))
            previous_end=column+length
        return out

    def assert_original_identifier_tokens(self,main,text,spans):
        # Ask the shared frontend for original identifier spans. Bounds alone
        # cannot detect a token shifted onto a string, comment or half a name.
        from thorlang.compiler import Source
        from thorlang.driver import frontend_binary
        source=Source(main,frontend_binary('odin')[0],raw=text.encode())
        allowed={(n.start,n.end) for n in source.nodes if n.kind=='Ident' and n.text.isidentifier()}
        for n in source.nodes:
            if n.kind=='Import' and n.data.get('name'):
                name=n.data['name'];start=n.start+len(n.text[:n.text.index(name)].encode())
                allowed.add((start,start+len(name.encode())))
        for selection in source.info.get('selections') or []:
            for name, position in zip(selection.get('names') or [], selection.get('positions') or []):
                start=position['offset']
                allowed.add((start,start+len(name.encode())))
        for start,end,kind,token,_ in spans:
            self.assertIn((start,end),allowed,f'{kind} token overrides non-identifier source {token!r} at byte {start}')

    def test_full_demo_semantic_tokens_match_original_identifiers(self):
        text=(ROOT/'demo/main.thor').read_text()
        client,main=self.open(text,{
            'support.odin':(ROOT/'demo/support.odin').read_text(),
            'library/library.odin':(ROOT/'demo/library/library.odin').read_text(),
            'assets/message.txt':(ROOT/'demo/assets/message.txt').read_text()})
        spans=self.semantic_spans(client,main,text)
        self.assertGreater(len(spans),700)
        self.assert_original_identifier_tokens(main,text,spans)
        by_offset={a:(kind,token) for a,_,kind,token,_ in spans}
        for text_before,name,kind in [
            ('{mark(1)}','mark','function'),('{mark(2)}','mark','function'),
            ('case 3..<5: [.Red','.Red','enumMember'),
            ('PURE_REPORT ::','PURE_REPORT','variable'),('report := PURE_REPORT','PURE_REPORT','variable'),
            ('Owned_Report ::','Owned_Report','struct')]:
            start=text.encode().index(text_before.encode())+text_before.index(name)+(1 if name.startswith('.') else 0)
            self.assertEqual(by_offset.get(start),(kind,name.lstrip('.')))
        for fragment in ['either := fruit ~ more_fruit','assert(len(combined) == 3 && len(either) == 2 && fruit != more_fruit)']:
            line_start=text.encode().index(fragment.encode())
            for name in ['fruit','more_fruit','combined','either']:
                start=fragment.find(name)
                if start>=0:self.assertEqual(by_offset.get(line_start+start),('variable',name))

    def test_record_values_and_type_aliases_have_distinct_semantic_tokens(self):
        text='package main\nReport::struct{value:int}\nOwned_Report::struct{rows:[]int}\nmake_report::fn(value:int)->Report{return {value}}\nPURE_REPORT::#run make_report(7)\nAUTO_REPORT::make_report(8)\nALIAS_REPORT::PURE_REPORT\nLITERAL_REPORT::Report{9}\nLITERAL_ALIAS::LITERAL_REPORT\nTYPED_REPORT:Report:PURE_REPORT\nReport_Alias::Report\nReport_From_Value::type_of(PURE_REPORT)\nBox::struct($T:typeid){value:T}\nInt_Box::Box(int)\nInt_Box_Alias::Int_Box\nmain::proc(){\n local_run::#run make_report(1)\n local_auto::make_report(2)\n Local_Type::Box(int)\n Local_From_Value::type_of(local_run)\n _=PURE_REPORT;_=AUTO_REPORT;_=ALIAS_REPORT;_=LITERAL_ALIAS\n _=local_run;_=local_auto;_=PURE_REPORT.value\n x:Int_Box;y:Local_Type;_=x;_=y\n}\n'
        client,main=self.open(text)
        spans=self.semantic_spans(client,main,text)
        self.assert_original_identifier_tokens(main,text,spans)
        values={'PURE_REPORT','AUTO_REPORT','ALIAS_REPORT','LITERAL_REPORT','LITERAL_ALIAS','TYPED_REPORT','local_run','local_auto'}
        types={'Report','Owned_Report','Report_Alias','Report_From_Value','Box','Int_Box','Int_Box_Alias','Local_Type','Local_From_Value'}
        for _,_,kind,name,modifiers in spans:
            if name in values:
                self.assertEqual(kind,'variable',(name,kind))
                self.assertTrue(modifiers & (1<<4),(name,modifiers))
            if name in types:self.assertEqual(kind,'struct',(name,kind))
        for name in values|types:
            self.assertTrue(any(token==name for _,_,_,token,_ in spans),name)
        hover=json.dumps(self.hover(client,main,text,'PURE_REPORT'))
        self.assertIn('#run make_report(7)',hover)
        self.assertNotIn('__thor_',hover)
        field=client.request('textDocument/hover',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(text,'PURE_REPORT.value',len('PURE_REPORT.')+1)})
        self.assertIn('value: int',json.dumps(field))

    def test_semantic_tokens_survive_reordered_switches_unicode_and_edits(self):
        text='''package main
Color::enum{Red,Blue}
main::proc(){
 café:=1;_=f"😀 {café}";_=f"outer {f"inner {café}"}"
 choice:[2]Color=switch café{case 1,2:[.Red,.Blue];case:[.Blue,.Red]}
 TEXT::#switch 2{case 1:"first";case 2:"second";case:"default"}
 for i:=0;i<2;i+=café{café+=1;_=choice}
 _=TEXT;_=café
}
'''
        client,main=self.open(text)
        self.assert_original_identifier_tokens(main,text,self.semantic_spans(client,main,text))
        for version,changed in enumerate([text.replace('\n','\r\n'),text.replace('café','value').replace('😀','界😀')],2):
            client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':version},'contentChanges':[{'text':changed}]})
            self.assert_original_identifier_tokens(main,changed,self.semantic_spans(client,main,changed))

    def test_parser_errors_use_exact_unicode_ranges_and_clear_after_fix(self):
        text='package main\nmain::proc(){_="😀";value:=;_=value}\n'
        client,main=self.open(text)
        errors=client.diagnostic(main.as_uri(),code='Syntax')
        error=next(e for e in errors if e['code']=='Syntax')
        self.assertEqual(error['range']['start'],self.position(text,';_=value',0))
        self.assertEqual(error['range']['end'],self.position(text,';_=value',1))
        changed=text.replace('value:=;','value:=1;')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        client.diagnostic(main.as_uri(),empty=True)

    def test_parser_error_at_eof_has_a_valid_zero_width_range(self):
        text='package main\nmain::proc(){_="😀";value:=1'
        client,main=self.open(text)
        errors=client.diagnostic(main.as_uri(),code='Syntax')
        eof={'line':1,'character':len(text.split('\n')[1].encode('utf-16-le'))//2}
        self.assertTrue(any(e['range']=={'start':eof,'end':eof} for e in errors),errors)

    def test_unicode_definitions_and_semantic_names_use_utf16(self):
        text='package main\nmain::proc(){_="😀界";café:=3;_=café}\n'
        client,main=self.open(text)
        before=text[:text.rindex('café')]
        position={'line':1,'character':len(before.split('\n')[-1].encode('utf-16-le'))//2+1}
        result=client.request('textDocument/definition',{'textDocument':{'uri':main.as_uri()},'position':position})
        self.assertEqual(result['range']['start'],self.position(text,'café',0))
        self.assertEqual(result['range']['end'],self.position(text,'café',len('café')))
        names=[token for *_,token,mods in self.semantic_spans(client,main,text)]
        self.assertEqual(names.count('café'),2)

    def test_contextual_enum_navigation_in_switch_array_arm(self):
        text='package main\nColor::enum{Red,Blue}\nmain::proc(){x:=1;choice:[2]Color=switch x{case 1:[.Red,.Blue];case:[.Blue,.Red]};_=choice}\n'
        client,main=self.open(text)
        self.assertIsNotNone(self.hover(client,main,text,'.Red'))

    def assert_contextual_enum(self, marked, *, enum='Color', files=None):
        before,after=marked.split('{*}')
        text=before+after
        position={'line':before.count('\n'),'character':len(before.rsplit('\n',1)[-1].encode('utf-16-le'))//2}
        client,main=self.open(text,files,compiler=False)
        params={'textDocument':{'uri':main.as_uri()},'position':position}
        hover=client.request('textDocument/hover',params)
        self.assertIsNotNone(hover,marked)
        self.assertIn(enum+': .Red',hover['contents']['value'])
        definition=client.request('textDocument/definition',params)
        target=definition[0] if isinstance(definition,list) else definition
        self.assertIsNotNone(target,marked)
        declaration=(files or {}).get('library/main.thor',text)
        enum_start=declaration.index(enum+'::enum')
        offset=declaration.index('Red',enum_start)
        prefix=declaration[:offset]
        expected={'line':prefix.count('\n'),'character':len(prefix.rsplit('\n',1)[-1].encode('utf-16-le'))//2}
        self.assertEqual(target['range']['start'],expected,definition)
        self.assertEqual(target['range']['end'],{**expected,'character':expected['character']+3})
        self.assertTrue(target['uri'].endswith('/library/main.thor' if files else '/main.thor'),target)
        completion=client.request('textDocument/completion',{**params,'context':{'triggerKind':2,'triggerCharacter':'.'}})
        items=completion.get('items',[]) if isinstance(completion,dict) else completion or []
        labels={item['label'] for item in items}
        self.assertIn('Red',labels)
        self.assertNotIn('Green' if enum=='Color' else 'Blue',labels)

    def test_contextual_enum_switch_arrays_across_expression_contexts(self):
        header='package main\nColor::enum{Red,Blue}\nOther::enum{Red,Green}\nBox::struct{colors:[2]Color}\n'
        cases={
            'declaration':'main::proc(){choice:[2]Color=switch 1{case 1:[.R{*}ed,.Blue];case:[.Blue,.Red]};_=choice}',
            'assignment':'main::proc(){choice:[2]Color;choice=switch 1{case 1:[.R{*}ed,.Blue];case:[.Blue,.Red]}}',
            'return':'work::proc()->[2]Color{return switch 1{case 1:[.R{*}ed,.Blue];case:[.Blue,.Red]}}',
            'argument':'take::proc(x:[2]Color){}\nmain::proc(){take(switch 1{case 1:[.R{*}ed,.Blue];case:[.Blue,.Red]})}',
            'named argument':'take::proc(count:int,x:[2]Color){}\nmain::proc(){take(x=switch 1{case 1:[.R{*}ed,.Blue];case:[.Blue,.Red]},count=1)}',
            'grouped parameters':'take::proc(a,b:[2]Color){}\nmain::proc(){take([.Blue,.Blue],switch 1{case 1:[.R{*}ed,.Blue];case:[.Blue,.Red]})}',
            'slice':'main::proc(){choice:[]Color=switch 1{case 1:[.R{*}ed,.Blue];case:[.Blue,.Red]};_=choice}',
            'nested arrays':'main::proc(){choice:[1][2]Color=[switch 1{case 1:[.R{*}ed,.Blue];case:[.Blue,.Red]}];_=choice}',
            'named field':'main::proc(){choice:=Box{colors=switch 1{case 1:[.R{*}ed,.Blue];case:[.Blue,.Red]}};_=choice}',
            'positional field':'main::proc(){choice:=Box{switch 1{case 1:[.R{*}ed,.Blue];case:[.Blue,.Red]}};_=choice}',
            'nested switches':'main::proc(){choice:[2]Color=switch 1{case 1:switch{case true:[.R{*}ed,.Blue];case:[.Blue,.Red]};case:[.Blue,.Red]};_=choice}',
            'compile switch':'main::proc(){choice:[2]Color=#switch 1{case 1:[.R{*}ed,.Blue];case:[.Blue,.Red]};_=choice}',
            'ternary in array':'main::proc(){choice:[2]Color=switch 1{case 1:[true ? .R{*}ed : .Blue,.Blue];case:[.Blue,.Red]};_=choice}',
            'multiple returns':'work::proc()->(int,[2]Color){return 1,switch 1{case 1:[.R{*}ed,.Blue];case:[.Blue,.Red]}}',
            'parentheses':'work::proc()->[2]Color{return (switch 1{case 1:([.R{*}ed,.Blue]);case:[.Blue,.Red]})}',
            'typed arm':'main::proc(){choice:=switch 1{case 1:[.R{*}ed,.Blue];case:[2]Color{.Blue,.Red}};_=choice}',
            'inner call':'choose::proc(x:Color)->Other{return .Green}\nmain::proc(){choice:Other=switch 1{case 1:choose(.R{*}ed);case:.Green};_=choice}',
            'scalar switch':'main::proc(){choice:Color=switch 1{case 1:.R{*}ed;case:.Blue};_=choice}',
            'struct result':'main::proc(){choice:Box=switch 1{case 1:{colors=[.R{*}ed,.Blue]};case:{colors=[.Blue,.Red]}};_=choice}',
            'generic argument':'take::proc($T:typeid,x:[2]T){}\nmain::proc(){take(Color,switch 1{case 1:[.R{*}ed,.Blue];case:[.Blue,.Red]})}',
            'overload argument':'take::proc{take_color,take_other}\ntake_color::proc(n:int,x:[2]Color){}\ntake_other::proc(n:string,x:[2]Other){}\nmain::proc(){take(1,switch 1{case 1:[.R{*}ed,.Blue];case:[.Blue,.Red]})}',
            'compile switch overload':'take::proc{take_color,take_other}\ntake_color::proc(n:int,x:[2]Color){}\ntake_other::proc(n:string,x:[2]Other){}\nmain::proc(){take(1,#switch 1{case 1:[.R{*}ed,.Blue];case:[.Blue,.Red]})}',
            'map value':'main::proc(){choice:=map[string][2]Color{"a"=switch 1{case 1:[.R{*}ed,.Blue];case:[.Blue,.Red]}};delete(choice)}',
            'map key':'main::proc(){choice:=map[Color]int{.R{*}ed=1};delete(choice)}',
            'enum indexed key':'main::proc(){choice:=[Color]int{.R{*}ed=1};_=choice}',
            'bitset':'main::proc(){choice:bit_set[Color]=switch 1{case 1:{.R{*}ed};case:{.Blue}};_=choice}',
            'comparison':'main::proc(){color:Color=.Blue;choice:bool=color==.R{*}ed;_=choice}',
        }
        for name,body in cases.items():
            with self.subTest(context=name):self.assert_contextual_enum(header+body+'\n')

    def test_contextual_switch_cases_keep_the_selector_type(self):
        self.assert_contextual_enum('package main\nColor::enum{Red,Blue}\nOther::enum{Red,Green}\nmain::proc(){mode:Other=.Green;choice:[2]Color=switch mode{case .R{*}ed:[.Red,.Blue];case .Green:[.Blue,.Red];case:[.Red,.Red]};_=choice}\n',enum='Other')

    def test_contextual_enum_through_imported_parameter_type(self):
        self.assert_contextual_enum('package main\nimport "library"\nmain::proc(){library.take(switch 1{case 1:[.R{*}ed,.Blue];case:[.Blue,.Red]})}\n',files={'library/main.thor':'package library\nColor::enum{Red,Blue}\ntake::proc(x:[2]Color){}\n'})

    def test_contextual_completion_in_incomplete_switch_expressions(self):
        cases={
            'array':'choice:[2]Color=switch 1{case 1:[.Red,.{*}];case:[.Blue,.Red]}',
            'partial array member':'choice:[2]Color=switch 1{case 1:[.Red,.R{*}];case:[.Blue,.Red]}',
            'bitset':'choice:bit_set[Color]=switch 1{case 1:{.Red,.{*}};case:{.Blue}}',
            'struct field':'choice:Box=switch 1{case 1:{co{*}};case:{colors=[.Red,.Blue]}}',
        }
        for name,body in cases.items():
            with self.subTest(context=name):
                before,after=('package main\nColor::enum{Red,Blue}\nOther::enum{Red,Green}\nBox::struct{colors:[2]Color}\nmain::proc(){'+body+';_=choice}\n').split('{*}')
                client,main=self.open(before+after,compiler=False)
                position={'line':before.count('\n'),'character':len(before.rsplit('\n',1)[-1].encode('utf-16-le'))//2}
                result=client.request('textDocument/completion',{'textDocument':{'uri':main.as_uri()},'position':position,'context':{'triggerKind':1}})
                items=result.get('items',[]) if isinstance(result,dict) else result or []
                labels={i['label'] for i in items}
                if name=='struct field':self.assertIn('colors',labels)
                else:
                    self.assertIn('Blue',labels)
                    self.assertNotIn('Green',labels)
                    if name=='bitset':self.assertNotIn('Red',labels)
                    else:self.assertIn('Red',labels)

    def test_contextual_enum_unsaved_diagnostics_and_navigation_update(self):
        text='package main\nColor::enum{Red,Blue}\nmain::proc(){/* 😀 */choice:[2]Color=switch 1{case 1:[.Red,.Blue];case:[.Blue,.Red]};_=choice}\n'
        client,main=self.open(text)
        changed=text.replace('1:[.Red','1:[.Missing')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        errors=client.diagnostic(main.as_uri(),contains='Missing')
        error=next(e for e in errors if 'Missing' in e['message'])
        self.assertEqual(error['range']['start'],self.position(changed,'.Missing',1))
        self.assertEqual(error['range']['end'],self.position(changed,'.Missing',8))
        fixed=changed.replace('.Missing','.Blue')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':fixed}]})
        client.diagnostic(main.as_uri(),empty=True)
        params={'textDocument':{'uri':main.as_uri()},'position':self.position(fixed,'1:[.Blue',5)}
        self.assertIn('Color: .Blue',client.request('textDocument/hover',params)['contents']['value'])
        target=client.request('textDocument/definition',params)
        self.assertEqual(target['range']['start'],self.position(fixed,'Blue}',0))
        self.assertEqual(main.read_text(),text)

    def test_type_query_validation_failure_maps_unsaved_text_and_clears(self):
        text='package main\nid::fn(n:int)->int{return n}\nmain::proc(){_="😀";text:="bad";N::id(size_of(text));_=N}\n'
        client,main=self.open(text)
        changed=text.replace('size_of(text)','size_of(cast(i32)text)')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        errors=client.diagnostic(main.as_uri(),contains='Cannot cast')
        error=next(e for e in errors if 'Cannot cast' in e['message'])
        self.assertEqual(error['range']['start'],self.position(changed,'text));',0))
        self.assertNotIn('__thor_',json.dumps(errors))
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)

    @unittest.skipIf(os.name=='nt','Uses an executable Python test compiler')
    def test_superseded_silent_check_is_cancelled_before_latest_snapshot(self):
        temporary=tempfile.TemporaryDirectory(prefix='tls-slow-check-');self.addCleanup(temporary.cleanup)
        root=Path(temporary.name).resolve();main=root/'main.thor';compiler=root/'compiler.py'
        text='package main\n// SLOW\nmain::proc(){}\n';main.write_text(text)
        compiler.write_text('''#!/usr/bin/env python3
import json,signal,sys,time
from pathlib import Path
root=Path(__file__).parent
overlay=json.loads(Path(sys.argv[sys.argv.index('--overlay')+1]).read_text())['Replace']
text=Path(overlay[str(root/'main.thor')]).read_text()
if 'SLOW' in text:
 def cancel(*args):
  (root/'cancelled').write_text('yes');raise SystemExit(143)
 signal.signal(signal.SIGTERM,cancel)
 (root/'started').write_text('yes')
 time.sleep(10)
(root/'completed').write_text(text)
print(json.dumps({'error_count':0,'errors':[]}),file=sys.stderr)
''');compiler.chmod(0o755)
        client=Client(root,compiler_command=compiler);self.addCleanup(client.close)
        client.send('textDocument/didOpen',{'textDocument':{'uri':main.as_uri(),'languageId':'thor','version':1,'text':text}})
        deadline=time.monotonic()+5
        while not (root/'started').exists() and time.monotonic()<deadline:time.sleep(.02)
        self.assertTrue((root/'started').exists())
        start=time.monotonic()
        for version in range(2,26):
            changed=f'package main\n// edit {version}\nmain::proc(){{}}\n'
            client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':version},'contentChanges':[{'text':changed}]})
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            if (root/'completed').exists() and (root/'completed').read_text()==changed:break
            time.sleep(.02)
        self.assertTrue((root/'cancelled').exists(),'Superseded check was not terminated')
        self.assertEqual((root/'completed').read_text(),changed)
        self.assertLess(time.monotonic()-start,5)

    @unittest.skipIf(os.name=='nt','Uses an executable Python test compiler')
    def test_shutdown_cancels_inflight_compiler_check(self):
        temporary=tempfile.TemporaryDirectory(prefix='tls-close-check-');self.addCleanup(temporary.cleanup)
        root=Path(temporary.name).resolve();main=root/'main.thor';compiler=root/'compiler.py'
        text='package main\nmain::proc(){}\n';main.write_text(text)
        compiler.write_text('''#!/usr/bin/env python3
import signal,time
from pathlib import Path
root=Path(__file__).parent
def cancel(*args):
 (root/'cancelled').write_text('yes');raise SystemExit(143)
signal.signal(signal.SIGTERM,cancel)
(root/'started').write_text('yes')
time.sleep(10)
''');compiler.chmod(0o755)
        client=Client(root,compiler_command=compiler);self.addCleanup(client.close)
        client.send('textDocument/didOpen',{'textDocument':{'uri':main.as_uri(),'languageId':'thor','version':1,'text':text}})
        deadline=time.monotonic()+5
        while not (root/'started').exists() and time.monotonic()<deadline:time.sleep(.02)
        self.assertTrue((root/'started').exists())
        client.close()
        self.assertTrue((root/'cancelled').exists(),'Closing TLS left its compiler check running')

    def test_capabilities_and_demo_have_no_parser_errors(self):
        text = (ROOT / 'demo' / 'main.thor').read_text()
        client, main = self.open(text, {
            'support.odin': (ROOT / 'demo' / 'support.odin').read_text(),
            'assets/message.txt': (ROOT / 'demo' / 'assets' / 'message.txt').read_text(),
            'library/library.odin': (ROOT / 'demo' / 'library' / 'library.odin').read_text()})
        self.assertFalse(client.capabilities['documentFormattingProvider'])
        self.assertEqual(client.capabilities['renameProvider'], {'prepareProvider': True})
        client.request('textDocument/documentSymbol', {'textDocument': {'uri': main.as_uri()}})
        diagnostics = [value['params']['diagnostics'] for value in client.notifications
                       if value.get('method') == 'textDocument/publishDiagnostics']
        self.assertTrue(all(not errors for errors in diagnostics), diagnostics)
        self.assertIsNone(client.process.poll())

    def test_hardening_pointer_xor_and_unsaved_lifetime_diagnostic(self):
        text='''package main
last::proc "c"(a:[]int,i:int)->int{return a[i]}
main::proc(){
 value:=1;p:=&value;p^=3;p^~=1
 a:=set[int]{1};b:=set[int]{2};a~=b
 result:=a~b
 for item in set[int]{1,2}{_=item}
 _=f"{(true ? f"inner {7}" : "borrowed")}"
 delete(a);delete(b);delete(result)
}
'''
        client,main=self.open(text)
        hover=self.hover(client,main,text,'result')
        self.assertIsNotNone(hover)
        self.assertIn('set[int]',hover['contents']['value'])
        changed=text.replace('value:=1','_="😀";view:=f"abc {1}"[1:];_=view;value:=1')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        errors=client.diagnostic(main.as_uri(),contains='named owner')
        error=next(e for e in errors if 'named owner' in e['message'])
        self.assertEqual(error['range']['start'],self.position(changed,'f"abc {1}"[1:]',0))
        self.assertNotIn('__thor_',json.dumps(errors))
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)
        self.assertEqual(main.read_text(),text)

    def test_hover_uses_unsaved_thor_text(self):
        text = 'package main\nmain :: proc() { values := [1,2,3]; _ = values }\n'
        client, main = self.open(text)
        position = text.rfind('values') + 2
        params = {'textDocument': {'uri': main.as_uri()}, 'position': {'line': 1, 'character': position - text.index('\n') - 1}}
        self.assertIn('[3]int', client.request('textDocument/hover', params)['contents']['value'])
        changed = text.replace('[1,2,3]', '[1.0,2.0]')
        client.send('textDocument/didChange', {'textDocument': {'uri': main.as_uri(), 'version': 2}, 'contentChanges': [{'text': changed}]})
        position = changed.rfind('values') + 2
        params['position']['character'] = position - changed.index('\n') - 1
        self.assertIn('[2]f64', client.request('textDocument/hover', params)['contents']['value'])
        self.assertEqual(main.read_text(), text)

    def test_pure_hover_run_inference_and_unsaved_diagnostics(self):
        text='package main\nsquare::fn(value:int)->int{return value*value}\nSIZE::#run square(3)\nmain::proc(){result:=#run square(4);_=result;data:[SIZE]int;_=data}\n'
        client,main=self.open(text)
        hover=self.hover(client,main,text,'square')
        self.assertIn('fn(value: int) -> int',hover['contents']['value'])
        self.assertIn('pure function',hover['contents']['value'])
        hover=self.hover(client,main,text,'result')
        self.assertIn('result: int',hover['contents']['value'])
        changed=text.replace('return value*value','_=context;return value*value')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        errors=client.diagnostic(main.as_uri(),contains='ambient context')
        error=next(e for e in errors if 'ambient context' in e['message'])
        self.assertEqual(error['range']['start'],self.position(changed,'context',0))
        self.assertNotIn('__thor_',json.dumps(errors))
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)
        self.assertEqual(main.read_text(),text)

    def test_pure_reference_iteration_unsaved_diagnostic_and_recovery(self):
        text='package main\nwork::fn(input:[]int)->int{values:=make([]int,len(input));copy(values,input);for i in 0..<len(values){values[i]+=1};return values[0]}\nmain::proc(){values:=[1,2];_=work(values[:])}\n'
        client,main=self.open(text)
        changed=text.replace('for i in 0..<len(values){values[i]+=1}', 'for &value in input{value+=1}')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        errors=client.diagnostic(main.as_uri(),contains='borrowed from an input')
        error=next(e for e in errors if 'borrowed from an input' in e['message'])
        self.assertEqual(error['range']['start'],self.position(changed,'value+=1',0))
        self.assertFalse(any('Undeclared name' in e['message'] for e in errors),errors)
        self.assertNotIn('__thor_',json.dumps(errors))
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)
        self.assertEqual(main.read_text(),text)

    def test_commented_walkthrough_semantics_and_hover(self):
        main=ROOT/'examples/demo/demo.thor'
        text=main.read_text()
        client=Client(main.parent,compiler=False)
        self.addCleanup(client.close)
        client.send('textDocument/didOpen',{'textDocument':{'uri':main.as_uri(),'languageId':'thor','version':1,'text':text}})
        spans=self.semantic_spans(client,main,text)
        self.assert_original_identifier_tokens(main,text,spans)
        self.assertGreater(len(spans),300)
        hover=self.hover(client,main,text,'staff: set[string]')
        self.assertIn('staff: set[string]',hover['contents']['value'])
        self.assertIn('```thor',hover['contents']['value'])
        for snippet,spelling in [('enormous: Integer', 'enormous: Integer'),
                                 ('decimal: Rational', 'decimal: Rational'),
                                 ('TOUR_BIG_BASE ::', 'Integer')]:
            self.assertIn(spelling,self.hover(client,main,text,snippet)['contents']['value'])
        for value in client.notifications:
            if value.get('method')=='textDocument/publishDiagnostics':
                self.assertFalse(any(e.get('code') in {'Syntax','Declaration'} for e in value['params']['diagnostics']),value)

    def test_allocated_run_hover_and_unsaved_constant_diagnostic(self):
        text='package main\nimport pure "thor:pure"\nnames::fn()->[]string{result:=make([]string,1);result[0]="hello";return result}\nmain::proc(){result:=#run names();pure.destroy(result)}\n'
        client,main=self.open(text)
        self.assertIn('result: []string',self.hover(client,main,text,'result:=#run')['contents']['value'])
        self.assertIn('pure.destroy',self.hover(client,main,text,'names')['contents']['value'])
        changed=text.replace('result:=#run names();pure.destroy(result)','result::#run names();_=result')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        errors=client.diagnostic(main.as_uri(),contains='slice-containing #run result')
        error=next(e for e in errors if 'slice-containing #run result' in e['message'])
        self.assertEqual(error['range']['start'],self.position(changed,'#run names()',0))
        self.assertNotIn('__thor_',json.dumps(errors))
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)
        self.assertEqual(main.read_text(),text)

    def test_automatic_constants_and_generic_unsaved_diagnostics(self):
        text='''package main
square::fn(value:int)->int{return value*value}
SIZE::square(3)
work::proc($N:int)->int{VALUE::#run square(N);return VALUE}
main::proc(){result:=square(SIZE);data:[SIZE]int;assert(work(3)==9);_=result;_=data}
'''
        client,main=self.open(text)
        self.assertIn('Known-argument calls',self.hover(client,main,text,'square')['contents']['value'])
        self.assertIn('result: int',self.hover(client,main,text,'result')['contents']['value'])
        changed=text.replace('VALUE::#run square(N)','value:=N;VALUE::#run square(value)')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        errors=client.diagnostic(main.as_uri(),contains='runtime storage is not a constant')
        error=next(e for e in errors if 'runtime storage is not a constant' in e['message'])
        self.assertEqual(error['range']['start'],self.position(changed,'square(value)',len('square(')))
        self.assertNotIn('__thor_',json.dumps(errors))
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)
        self.assertEqual(main.read_text(),text)

    def test_automatic_imported_failure_uses_unsaved_source(self):
        text='package main\nimport "library"\nVALUE::library.work(3)\nmain::proc(){_=VALUE}\n'
        original='package library\nwork::fn(n:int)->int{return n*n}\n'
        client,main=self.open(text,{'library/main.thor':original})
        library=main.parent/'library/main.thor'
        changed=original.replace('return n*n','_="😀";return missing')
        client.send('textDocument/didOpen',{'textDocument':{'uri':library.as_uri(),'languageId':'thor','version':1,'text':changed}})
        errors=client.diagnostic(library.as_uri(),contains='missing')
        error=next(e for e in errors if 'missing' in e['message'])
        self.assertEqual(error['range']['start'],self.position(changed,'missing',0))
        self.assertNotIn('thor-run-',json.dumps(errors))
        client.send('textDocument/didChange',{'textDocument':{'uri':library.as_uri(),'version':2},'contentChanges':[{'text':original}]})
        client.diagnostic(library.as_uri(),empty=True)
        self.assertEqual(library.read_text(),original)

    def test_run_imported_unsaved_purity_error(self):
        text='package main\nimport "library" / square\nVALUE::#run square(3)\nmain::proc(){_=VALUE}\n'
        original='package library\nsquare::fn(value:int)->int{return value*value}\n'
        client,main=self.open(text,{'library/main.thor':original})
        library=main.parent/'library/main.thor'
        changed=original.replace('return value*value','_=context;return value*value')
        client.send('textDocument/didOpen',{'textDocument':{'uri':library.as_uri(),'languageId':'thor','version':1,'text':changed}})
        errors=client.diagnostic(library.as_uri(),contains='ambient context')
        error=next(e for e in errors if 'ambient context' in e['message'])
        self.assertEqual(error['range']['start'],self.position(changed,'context',0))
        self.assertEqual(library.read_text(),original)

    def test_cached_imported_result_changes_with_unsaved_contents(self):
        text='package main\nimport "library"\nVALUE::#run library.work()\n#assert(VALUE=="old")\nmain::proc(){}\n'
        original='package library\nwork::fn()->string{return "old"}\n'
        client,main=self.open(text.replace('VALUE=="old"','VALUE=="wrong"'),{'library/main.thor':original})
        client.diagnostic(main.as_uri(),contains='assertion')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)
        library=main.parent/'library/main.thor'
        changed=original.replace('"old"','"new"')
        client.send('textDocument/didOpen',{'textDocument':{'uri':library.as_uri(),'languageId':'thor','version':1,'text':changed}})
        errors=client.diagnostic(main.as_uri(),contains='assertion')
        self.assertTrue(any('assertion' in e['message'].lower() for e in errors))
        client.send('textDocument/didChange',{'textDocument':{'uri':library.as_uri(),'version':2},'contentChanges':[{'text':original}]})
        client.diagnostic(main.as_uri(),empty=True)
        self.assertEqual(library.read_text(),original)

    def test_power_inference_precedence_tokens_and_unsaved_type_error(self):
        text='package main\nwhen 2**3**2==512 && -2**2 == -4 {SELECTED::42} else {SELECTED::false}\nmain::proc(){_="😀";base:f64=2;exponent:f64 = -3;result:=base**exponent;_=result;_=SELECTED}\n'
        client,main=self.open(text)
        self.assertIn('result: f64',json.dumps(self.hover(client,main,text,'result')))
        self.assertIn('42',json.dumps(self.hover(client,main,text,'SELECTED}')))
        self.assert_original_identifier_tokens(main,text,self.semantic_spans(client,main,text))
        client.notifications.clear()
        changed=text.replace('exponent:f64 = -3','exponent:int = 3')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        errors=client.diagnostic(main.as_uri(),contains='matching operand types')
        self.assertEqual(len(errors),1,errors)
        self.assertNotIn('Thor expression',json.dumps(errors))
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)

    def test_power_mismatch_in_immutable_loop_value_reports_only_original_operation_and_clears(self):
        text='''// original 😀 header
package main
import * "thor:basic"
Games::enum{roulette}
HOUSE_ODDS_TABLE::[Games]f64{.roulette=.5263157894736842}
main::proc(){
    GAME::Games.roulette
    HOUSE_ODDS::HOUSE_ODDS_TABLE[GAME]
    for i in 1..=10 {
        loss:>HOUSE_ODDS ** i
        _=loss
    }
}
'''
        client,main=self.open(text)
        errors=client.diagnostic(main.as_uri(),contains='matching operand types')
        self.assertEqual(len(errors),1,errors)
        error=errors[0]
        self.assertEqual(error['range'],{'start':{'line':9,'character':14},'end':{'line':9,'character':29}})
        self.assertIn('f64 vs int',error['message'])
        self.assertIn('Convert an operand explicitly',error['message'])
        self.assertNotIn('Thor expression',error['message'])
        self.assertNotIn('return statement',error['message'])
        client.notifications.clear()
        changed=text.replace('HOUSE_ODDS ** i','HOUSE_ODDS ** f64(i)')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        client.diagnostic(main.as_uri(),empty=True)

    def test_range_membership_constraints_hover_tokens_and_unsaved_errors(self):
        text='''package main
allowed::proc($N:int)->int where N in 0..<10{return N}
when -2 in -3..=0 {SELECTED::42} else {SELECTED::false}
main::proc(){value:=3;found:=value in 0..<10;result:=allowed(9);_=found;_=result;_=SELECTED}
'''
        client,main=self.open(text)
        self.assertIn('found: bool',json.dumps(self.hover(client,main,text,'found')))
        self.assertIn('42',json.dumps(self.hover(client,main,text,'SELECTED}')))
        self.assert_original_identifier_tokens(main,text,self.semantic_spans(client,main,text))
        client.notifications.clear()
        changed=text.replace('allowed(9)','allowed(10)')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        errors=client.diagnostic(main.as_uri(),contains='where')
        self.assertNotIn('__thor_',json.dumps(errors))
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)

    def test_expression_string_switch_and_set_hovers(self):
        text='''package main
main::proc(){
 name:="world"
 message:=f"hello {name}"
 choice:=switch 1 {case 1:2;case:3}
 floating:=switch 1 {case 1:2;case:f64(3)}
 names:=set[int]{1,2}
 _=message;_=choice;_=names;_=floating
 delete(message);delete(names)
}
'''
        client,main=self.open(text)
        for word,expected in [('message','string'),('choice','int'),('floating','f64'),('names','set[int]')]:
            with self.subTest(word=word):
                hover=self.hover(client,main,text,word)
                self.assertIsNotNone(hover)
                self.assertIn(expected,hover['contents']['value'])
        hover=client.request('textDocument/hover',{'textDocument':{'uri':main.as_uri()},'position':self.position(text,'{name}',2)})
        self.assertIn('string',hover['contents']['value'])
        definition=client.request('textDocument/definition',{'textDocument':{'uri':main.as_uri()},'position':self.position(text,'{name}',2)})
        self.assertTrue(definition)
        target=definition[0] if isinstance(definition,list) else definition
        self.assertEqual(target['range']['start'],self.position(text,'name:=',0))
        for word,expected in [('message','string'),('choice','int'),('names','set[int]')]:
            hover=client.request('textDocument/hover',{'textDocument':{'uri':main.as_uri()},'position':self.position(text,'_='+word,3)})
            self.assertIn(expected,hover['contents']['value'])
            self.assertNotIn('__thor_',hover['contents']['value'])

    def test_compile_switch_globals_and_selected_expression_type(self):
        text='''package main
#switch 2 {case 1:GLOBAL::"wrong";case 2:GLOBAL::7;case:GLOBAL::false}
SELECTED::#switch 2 {case 1:"wrong";case 2:7;case:false}
main::proc(){_=GLOBAL;_=SELECTED}
'''
        client,main=self.open(text)
        for word in ['_=GLOBAL','_=SELECTED']:
            result=client.request('textDocument/hover',{'textDocument':{'uri':main.as_uri()},'position':self.position(text,word,4)})
            self.assertIsNotNone(result)
            self.assertIn(':: 7',result['contents']['value'])

    def test_precision_formats_hover_original_tokens_and_unsaved_diagnostic_recovery(self):
        text='''package main
render::fn(value:Rational)->string{return f"{value:.6g}"}
main::proc(){
 ratio:=Rational(1,3)
 message:=f"😀 {ratio:.6g} {1.25:012,.2f} {\"é😀\":'界'^8.2s}"
 delete(message);delete(ratio)
}
'''
        client,main=self.open(text)
        self.assertIn('Rational',self.hover(client,main,text,'ratio:.6g')['contents']['value'])
        self.assert_original_identifier_tokens(main,text,self.semantic_spans(client,main,text))
        broken=text.replace('ratio:.6g','ratio:g')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':broken}]})
        errors=client.diagnostic(main.as_uri(),contains='requires explicit precision')
        error=next(e for e in errors if 'requires explicit precision' in e['message'])
        self.assertEqual(error['range']['start'],self.position(broken,'ratio:g',0))
        self.assertEqual(error['range']['end'],self.position(broken,'ratio:g',5))
        self.assertNotIn('runtime/',error['message']);self.assertNotIn('__thor_',error['message'])
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)
        self.assertEqual(main.read_text(),text)

    def test_removed_alignment_format_reports_suffix_after_unsaved_edit(self):
        text='package main\nmain::proc(){s:=f"{1:8d}";delete(s)}\n'
        client,main=self.open(text)
        self.assertIn('string',self.hover(client,main,text,'s:=')['contents']['value'])
        changed=text.replace('1:8d','1:>8d')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        error=next(e for e in client.diagnostic(main.as_uri(),contains='no longer uses') if 'no longer uses' in e['message'])
        self.assertEqual(error['range']['start'],self.position(changed,'>8d',0))
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)

    def test_quoted_format_fill_unsaved_validation_and_token_ranges(self):
        text='package main\nmain::proc(){text:=f`raw \\n {"é😀":\'界\'^5}`;delete(text)}\n'
        client,main=self.open(text)
        self.assert_original_identifier_tokens(main,text,self.semantic_spans(client,main,text))
        changed=text.replace("'界'",'界')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        errors=client.diagnostic(main.as_uri(),contains='single-quoted rune')
        error=next(e for e in errors if 'single-quoted rune' in e['message'])
        self.assertEqual(error['range']['start'],self.position(changed,'界^5',0))
        self.assertEqual(error['range']['end'],self.position(changed,'界^5',3))
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)
        self.assertEqual(main.read_text(),text)

    def test_interpolation_compiler_error_range_and_unsaved_fix(self):
        text='package main\nmain::proc(){text:=f"😀 {missing_value}";delete(text)}\n'
        client,main=self.open(text)
        errors=client.diagnostic(main.as_uri(),code='thor',contains='missing_value')
        error=next(d for d in errors if 'missing_value' in d['message'])
        self.assertEqual(error['range']['start'],self.position(text,'missing_value',0))
        self.assertNotIn('__thor_',error['message'])
        changed=text.replace('missing_value','7')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        client.diagnostic(main.as_uri(),empty=True)
        self.assertEqual(main.read_text(),text)

    def test_new_syntax_semantic_tokens_have_original_ranges(self):
        text='package main\nsquare::fn(value:int)->int{return value*value}\nSIZE::#run square(3)\nmain::proc(){x:>7;s:=f"value {x}";names:=set[int]{x};_=switch x{case 7:1;case:2};delete(s);delete(names)}\n'
        client,main=self.open(text)
        result=client.request('textDocument/semanticTokens/full',{'textDocument':{'uri':main.as_uri()}})
        line=column=0
        for a,b,length,kind,modifiers in zip(*[iter(result['data'])]*5):
            line+=a;column=b if a else column+b
            self.assertLess(line,len(text.splitlines()))
            self.assertLessEqual(column+length,len(text.splitlines()[line].encode('utf-16-le'))//2)

    def test_selected_import_hover_and_definition_in_closed_thor_package(self):
        text = 'package main\nimport "library" / selected\nmain :: proc() { _ = selected(2) }\n'
        client, main = self.open(text, {'library/main.thor': 'package library\nselected :: proc(value:int)->int {return value}\n'})
        params = {'textDocument': {'uri': main.as_uri()}, 'position': {'line': 2, 'character': 26}}
        hover = client.request('textDocument/hover', params)
        self.assertIn('proc(value: int) -> int', hover['contents']['value'])
        definitions = client.request('textDocument/definition', params)
        if isinstance(definitions, dict):
            definitions = [definitions]
        self.assertTrue(any(item['uri'].endswith('/library/main.thor') for item in definitions), definitions)

    def test_selected_import_declaration_hover_matches_function_value_and_call(self):
        text = '''package main
import "core:fmt" / print
main::proc(){callback:=print;callback("hello");print("hello")}
'''
        client, main = self.open(text, compiler=False, unused_imports=True)
        reference = self.hover(client, main, text, 'print;')
        call = self.hover(client, main, text, 'print("hello")')
        self.assertIsNotNone(reference)
        self.assertIn('fmt.print :: proc(', reference['contents']['value'])
        self.assertIn('Formats', reference['contents']['value'])
        definitions = [client.request('textDocument/definition', {
            'textDocument':{'uri':main.as_uri()},'position':self.position(text,word)})
            for word in ['print\n','print;']]
        self.assertTrue(definitions[0])
        self.assertEqual(definitions[0],definitions[1])
        for offset in range(len('print')):
            with self.subTest(offset=offset):
                selected = client.request('textDocument/hover', {
                    'textDocument':{'uri':main.as_uri()},'position':self.position(text,'print\n',offset)})
                self.assertIsNotNone(selected)
                self.assertEqual(selected['contents'], reference['contents'])
                self.assertEqual(selected['contents'], call['contents'])
                self.assertEqual(selected['range'], {
                    'start':self.position(text,'print\n',0), 'end':self.position(text,'print\n',len('print'))})
        unused = text.replace('callback:=print;callback("hello");print("hello")', '')
        client.send('textDocument/didChange', {'textDocument':{'uri':main.as_uri(),'version':2},
                    'contentChanges':[{'text':unused}]})
        self.assertEqual(len(self.unused_imports(client, main)), 1)
        self.assertEqual(self.hover(client,main,unused,'print\n')['contents'],reference['contents'])

    def test_multiline_selected_import_hovers_resolve_original_docs_and_types(self):
        library = '''package library
// A documented record.
Record::struct{value:int}
// A documented constant.
LIMIT::7
counter:int
// A documented generic.
identity::proc(value:$T)->T{return value}
as_int::proc(value:int){}
as_string::proc(value:string){}
// A documented overload group.
overloaded::proc{as_int,as_string}
// A documented Unicode procedure.
écho::proc(value:int)->int{return value}
'''
        text = '''package main
/* 😀 */ import _ "library" / Record,
    LIMIT, counter, identity, overloaded,
    écho
main::proc(){_=Record{};_=LIMIT;_=counter;_=identity;_=overloaded;_=écho}
'''
        client, main = self.open(text, {'library/main.thor':library}, compiler=False)
        for name, docs in [('Record','A documented record.'), ('LIMIT','A documented constant.'),
                           ('counter',None),
                           ('identity','A documented generic.'), ('overloaded','A documented overload group.'),
                           ('écho','A documented Unicode procedure.')]:
            with self.subTest(name=name):
                selected = self.hover(client,main,text,name)
                reference = client.request('textDocument/hover', {'textDocument':{'uri':main.as_uri()},
                    'position':self.position(text,'_='+name,3)})
                self.assertIsNotNone(selected)
                self.assertEqual(selected['contents'],reference['contents'])
                if docs is not None:
                    self.assertIn(docs,selected['contents']['value'])
                if name == 'counter':
                    self.assertIn('counter: int',selected['contents']['value'])
                self.assertNotIn('__thor_',selected['contents']['value'])
                self.assertEqual(selected['range'], {
                    'start':self.position(text,name,0), 'end':self.position(text,name,len(name))})
        modifiers = client.capabilities['semanticTokensProvider']['legend']['tokenModifiers']
        readonly = 1 << modifiers.index('readonly')
        comptime = 1 << modifiers.index('thorCompileTime')
        for _,_,kind,name,flags in self.semantic_spans(client,main,text):
            if name == 'counter':
                self.assertEqual(kind,'variable')
                self.assertEqual(flags & (readonly | comptime),0)

    def test_selected_completion_and_namespace(self):
        text = 'package main\nimport "library" / selected\nmain :: proc() { sel }\n'
        client, main = self.open(text, {'library/main.thor': 'package library\nselected :: proc() {}\n'})
        result = client.request('textDocument/completion', {'textDocument': {'uri': main.as_uri()},
                    'position': {'line': 2, 'character': 19}, 'context': {'triggerKind': 1}})
        self.assertIn('selected', [item['label'] for item in result['items']])

    def test_mixed_package_indexing(self):
        text = 'package main\nmain :: proc() { _ = helper() }\n'
        client, main = self.open(text, {'helper.thor': 'package main\nhelper :: proc()->int { return 2 in [1,2] ? 7 : 0 }\n'})
        self.assertIn('proc() -> int', self.hover(client, main, text, 'helper')['contents']['value'])

    def test_semantic_tokens_have_valid_original_source_ranges(self):
        text = 'package main\nimport "library" / selected\nf :: proc(x:$T)->T where T in [int,f64] { return x }\nmain :: proc() { a := [1,2]; found := selected(2) in a; _ = found }\n'
        client, main = self.open(text, {'library/main.thor': 'package library\nselected :: proc(x:int)->int {return x}\n'})
        result = client.request('textDocument/semanticTokens/full', {'textDocument': {'uri': main.as_uri()}})
        data = result['data']
        self.assertGreater(len(data), 0)
        self.assertEqual(len(data) % 5, 0)
        lines = text.splitlines()
        line, column, previous_end = 0, 0, 0
        for i in range(0, len(data), 5):
            delta, character, length, token_type, modifiers = data[i:i+5]
            line += delta
            column = character if delta else column + character
            if delta:
                previous_end = 0
            self.assertLess(line, len(lines))
            self.assertGreater(length, 0)
            self.assertGreaterEqual(column, previous_end)
            self.assertLessEqual(column + length, len(lines[line].encode('utf-16-le')) // 2)
            previous_end = column + length

    def test_set_hover_canonical_types_signatures_and_containers(self):
        text='package main\nimport sets "thor:sets"\nHolder::struct{names:sets.Set(string), groups:[]sets.Set(int), callback:proc(items:[]^sets.Set(string))->sets.Set(int)}\naccept::proc(names:^sets.Set(string))->sets.Set(string){return names^}\nmain::proc(){\n short:set[string]\n explicit:sets.Set(string)\n cloned:=sets.clone(explicit)\n table:map[string][]^sets.Set(int)\n holder:Holder\n _=short;_=cloned;_=table;_=holder.names\n sets.insert(&explicit,"apple")\n}\n'
        client,main=self.open(text)
        for name,expected in [('short','short: set[string]'),('explicit:','explicit: set[string]'),
                              ('cloned','cloned: set[string]'),('table:','map[string][]^set[int]'),
                              ('accept','proc(names: ^set[string]) -> set[string]')]:
            with self.subTest(name=name):
                hover=json.dumps(self.hover(client,main,text,name))
                self.assertIn(expected,hover)
                self.assertNotIn('__thor_',hover)
                self.assertNotIn('sets.Set(',hover)
                self.assertIn('```thor',hover)
        insertion=client.request('textDocument/hover',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(text,'sets.insert',len('sets.')+2)})
        self.assertIn('set: ^set[$T]',json.dumps(insertion))
        self.assertNotIn('Set(',json.dumps(insertion))
        holder=json.dumps(self.hover(client,main,text,'Holder::'))
        self.assertIn('proc(items: []^set[string]) -> set[int]',holder)
        member=client.request('textDocument/hover',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(text,'holder.names',len('holder.')+1)})
        self.assertIn('names: set[string]',json.dumps(member))
        definition=client.request('textDocument/definition',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(text,'holder.names',len('holder.')+1)})
        self.assertEqual(definition['range']['start'],self.position(text,'names:sets.Set',0))
        signature=client.request('textDocument/signatureHelp',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(text,'sets.insert(&explicit,',len('sets.insert(&explicit,'))})
        self.assertIn('^set[$T]',json.dumps(signature))
        self.assert_original_identifier_tokens(main,text,self.semantic_spans(client,main,text))

    def test_native_compiler_path_discovers_runtime_without_explicit_collection(self):
        text='package main\nimport sets "thor:sets"\nmain::proc(){names:sets.Set(string);other:=sets.clone(names);_=other}\n'
        from tests.test_rust_compiler import RUST
        temporary=tempfile.TemporaryDirectory(prefix='thor native compiler λ ')
        self.addCleanup(temporary.cleanup)
        compiler=Path(temporary.name)/('thor.exe' if os.name=='nt' else 'thor')
        if os.name=='nt':
            shutil.copy2(RUST,compiler)
        else:
            compiler.symlink_to(RUST)
        client,main=self.open(text,compiler_command=compiler,collections=[])
        for word in ['names:','other:=']:
            hover=self.hover(client,main,text,word)
            self.assertIn('set[string]',json.dumps(hover))
        client.diagnostic(main.as_uri(),empty=True)

    @unittest.skipIf(os.name=='nt','POSIX test launcher; Windows validation is separate')
    def test_runtime_discovery_respects_explicit_collections_and_bounds_failed_queries(self):
        with tempfile.TemporaryDirectory(prefix='tls root query ') as directory:
            root=Path(directory).resolve()
            compiler=root/'compiler';marker=root/'called'
            compiler.write_text('#!/bin/sh\ntouch "'+str(marker)+'"\nexit 1\n')
            compiler.chmod(0o755)
            client=Client(root,compiler_command=compiler)
            client.close()
            self.assertFalse(marker.exists(),'Explicit collections must avoid a root query')
            for body in ['exit 1','printf "relative/path"','exec sleep 5']:
                with self.subTest(body=body):
                    compiler.write_text('#!/bin/sh\n'+body+'\n')
                    started=time.monotonic()
                    client=Client(root,compiler_command=compiler,collections=[])
                    client.close()
                    self.assertLess(time.monotonic()-started,4.5)

    def test_set_hover_aliases_nested_types_and_unrelated_set_types(self):
        text='package main\nimport bag "thor:sets"\nimport fake "fake"\nSet::struct($T:typeid){value:T}\nTagged::distinct bag.Set(string)\nidentity::proc(value:bag.Set(string))->bag.Set(string){return value}\nmain::proc(){\n names:bag.Set(string)\n others:fake.Set(string)\n own:Set(string)\n tagged:Tagged\n nested:bag.Set([2]int)\n pointers:[2]^bag.Set(string)\n _=names;_=others;_=own;_=nested;_=pointers\n}\n'
        client,main=self.open(text,{'fake/main.thor':'package sets\nSet::struct($T:typeid){value:T}\n'})
        for name,expected in [('names:','names: set[string]'),('nested:','set[[2]int]'),
                              ('pointers:','[2]^set[string]'),('identity','proc(value: set[string]) -> set[string]')]:
            with self.subTest(name=name):self.assertIn(expected,json.dumps(self.hover(client,main,text,name)))
        self.assertIn('tagged: Tagged',json.dumps(self.hover(client,main,text,'tagged:')))
        for name in ['others:','own:']:
            hover=json.dumps(self.hover(client,main,text,name))
            self.assertIn('Set(string)',hover)
            self.assertNotIn('set[string]',hover)

    def test_imported_set_signatures_generic_arguments_and_native_file_hovers(self):
        text='package main\nimport "library"\nimport sets "thor:sets"\nBox::struct($T:typeid){value:T}\nmain::proc(){\n nested:Box(sets.Set(string))\n names:set[string]\n result:=library.copy_names(names)\n _=nested;_=result\n}\n'
        library='package library\nimport bag "thor:sets"\ncopy_names::proc(names:bag.Set(string))->bag.Set(string){return names}\n'
        client,main=self.open(text,{'library/main.odin':library})
        hover=client.request('textDocument/hover',{'textDocument':{'uri':main.as_uri()},
            'position':self.position(text,'library.copy_names',len('library.')+2)})
        self.assertIn('proc(names: set[string]) -> set[string]',json.dumps(hover))
        for word in ['nested:','result:=']:
            self.assertIn('set[string]',json.dumps(self.hover(client,main,text,word)))
        # Odin files keep their native spelling, even when opened by the same TLS.
        native=main.parent/'library/main.odin'
        client.send('textDocument/didOpen',{'textDocument':{'uri':native.as_uri(),'languageId':'odin','version':1,'text':library}})
        hover=client.request('textDocument/hover',{'textDocument':{'uri':native.as_uri()},'position':self.position(library,'copy_names')})
        self.assertIn('Set(string)',json.dumps(hover))
        self.assertNotIn('set[string]',json.dumps(hover))
        self.assertIn('```odin',json.dumps(hover))

    def test_runtime_import_default_names_resolve_and_keep_namespace_tokens(self):
        text='''package main
import "thor:format"
import "thor:sets"
import "thor:pure"
main::proc(){
 text:=format.group_integer(1234);delete(text)
 names:sets.Set(string);sets.insert(&names,"ok");sets.destroy(names)
 values:=pure.clone([]int{1,2});pure.destroy(values)
}
'''
        client,main=self.open(text)
        spans=self.semantic_spans(client,main,text)
        self.assert_original_identifier_tokens(main,text,spans)
        for namespace,member,directory in [('format','group_integer','format'),('sets','Set','sets'),('pure','clone','pure')]:
            self.assertTrue(any(kind=='namespace' and token==namespace for _,_,kind,token,_ in spans),namespace)
            self.assertIsNotNone(self.hover(client,main,text,namespace+'.'+member))
            result=client.request('textDocument/definition',{'textDocument':{'uri':main.as_uri()},'position':self.position(text,namespace+'.'+member,len(namespace)+2)})
            self.assertIn('/runtime/'+directory+'/',result['uri'])

    def test_import_paths_keep_string_coloring(self):
        text = ('package main\n'
                'import "core:fmt" / println\n'
                'import math "core:math" / sin\n'
                'import "library" / Record\n'
                'main :: proc() { println("core:fmt library"); _ = math.PI; _ = Record{} }\n')
        client, main = self.open(text, {'library/main.thor': 'package library\nRecord :: struct {}\n'})
        result = client.request('textDocument/semanticTokens/full', {'textDocument': {'uri': main.as_uri()}})
        data = result['data']
        lines = text.splitlines()
        namespace = client.capabilities['semanticTokensProvider']['legend']['tokenTypes'].index('namespace')
        line, column = 0, 0
        alias_highlighted = False
        namespace_use_highlighted = False
        for i in range(0, len(data), 5):
            delta, character, length, token_type, _ = data[i:i+5]
            line += delta
            column = character if delta else column + character
            if lines[line].startswith('import '):
                start = lines[line].index('"')
                end = lines[line].index('"', start + 1) + 1
                self.assertFalse(column < end and start < column + length,
                                 f'Semantic token overrides import string: {lines[line][column:column+length]!r}')
            if token_type == namespace and lines[line][column:column+length] == 'math':
                alias_highlighted |= line == 2
                namespace_use_highlighted |= line == 4
        self.assertTrue(alias_highlighted, 'Explicit import aliases should retain namespace coloring')
        self.assertTrue(namespace_use_highlighted, 'Qualified namespace uses should retain namespace coloring')

    def test_hidden_set_import_has_no_hover_at_the_first_comment(self):
        text = '// Introduction to this example.\npackage main\nimport sets "thor:sets"\nmain::proc(){names:set[string];sets.insert(&names,"ok");delete(names)}\n'
        client, main = self.open(text, compiler=False)
        for character in (0, 1, 3):
            self.assertIsNone(client.request('textDocument/hover', {'textDocument': {'uri': main.as_uri()},
                                             'position': {'line': 0, 'character': character}}))
        params = {'textDocument': {'uri': main.as_uri()}, 'position': self.position(text, 'import sets', len('import ')+1)}
        hover = client.request('textDocument/hover', params)
        self.assertIn('sets: package', hover['contents']['value'])
        self.assertEqual(hover['range']['start']['line'], 2)
        self.assertEqual(hover['range']['end']['line'], 2)

    def test_parser_diagnostics_clear_after_unsaved_fix(self):
        text = 'package main\nmain :: proc() { values := [1, }\n'
        client, main = self.open(text)
        errors = client.diagnostic(main.as_uri())
        self.assertTrue(errors)
        self.assertTrue(any(error['range']['start']['line'] == 1 for error in errors))
        self.assertTrue(all(0 <= error['range']['start']['line'] <= 2 for error in errors))
        fixed = 'package main\nmain :: proc() { values := [1,2]; _ = values }\n'
        client.send('textDocument/didChange', {'textDocument': {'uri': main.as_uri(), 'version': 2}, 'contentChanges': [{'text': fixed}]})
        self.assertEqual(client.diagnostic(main.as_uri(), empty=True), [])

    def test_live_compiler_type_error_maps_array_element(self):
        text = 'package main\nmain :: proc(){ a := [1,"wrong"]; _ = a }\n'
        client, main = self.open(text)
        errors = client.diagnostic(main.as_uri(), code='thor', contains='Cannot convert')
        error = next(e for e in errors if e.get('code') == 'thor')
        self.assertEqual(error['range']['start'], self.position(text, '"wrong"', 0))
        self.assertEqual(error['range']['end']['character'] - error['range']['start']['character'], 7)
        self.assertNotIn('__thor_', error['message'])

    def test_generic_default_callback_has_its_exact_body_range_and_clears(self):
        broken = 'package basic\n/* 😀 */ sorted::proc(a:$T/[]$E,less:=proc(i,j:E)->bool{return missing})->[]E{_=less(a[0],a[1]);return a}\ncheck::proc(){_=sorted([]int{1,2})}\n'
        clean = 'package main\nmain::proc(){}\n'
        client, main = self.open(clean, {'basic/main.thor': broken})
        bad = main.parent / 'basic/main.thor'
        client.send('textDocument/didOpen', {'textDocument': {'uri': bad.as_uri(),
                    'languageId': 'thor', 'version': 1, 'text': broken}})
        errors = client.diagnostic(bad.as_uri(), code='thor', contains='Undeclared name: missing')
        error = next(e for e in errors if 'Undeclared name: missing' in e['message'])
        self.assertEqual(error['range']['start'], self.position(broken, 'missing', 0))
        self.assertEqual(error['range']['end'], self.position(broken, 'missing', len('missing')))
        self.assertNotIn('__thor_', error['message'])
        self.assertNotIn('did not provide a source location', error['message'])
        self.assertFalse(any(n.get('method') == 'textDocument/publishDiagnostics'
                             and n['params']['uri'] == main.as_uri()
                             and any('Undeclared name: missing' in d['message'] for d in n['params']['diagnostics'])
                             for n in client.notifications))
        fixed = broken.replace('return missing', 'return i<j')
        client.notifications.clear()
        client.send('textDocument/didChange', {'textDocument': {'uri': bad.as_uri(), 'version': 2},
                    'contentChanges': [{'text': fixed}]})
        client.diagnostic(bad.as_uri(), empty=True)
        self.assertEqual(bad.read_text(), broken)

    def test_generic_inline_default_hover_navigation_tokens_and_rename(self):
        text = 'package main\n/* 😀 */ sorted::proc(data:[]$E,less:=proc(i,j:E)->bool{return i<j})->[]E{_=less(data[0],data[1]);return data}\nmain::proc(){_=sorted([]int{1,2})}\n'
        client, main = self.open(text, compiler=False)
        document = {'uri': main.as_uri()}
        for needle, offset, expected in [('less(data', 1, 'less: proc(i, j: E) -> bool'),
                                         ('return i<j', 7, 'i: E'), ('i,j:E', 4, 'E')]:
            with self.subTest(needle=needle):
                hover = client.request('textDocument/hover', {'textDocument': document,
                    'position': self.position(text, needle, offset)})
                self.assertIsNotNone(hover, needle)
                self.assertIn(expected, hover['contents']['value'])
                self.assertNotIn('__thor_', hover['contents']['value'])
        definition = client.request('textDocument/definition', {'textDocument': document,
            'position': self.position(text, 'i,j:E', 4)})
        self.assertEqual(definition['uri'], main.as_uri())
        self.assertEqual(definition['range']['start'], self.position(text, '$E', 1))
        renamed = client.request('textDocument/rename', {'textDocument': document,
            'position': self.position(text, 'i,j:E', 4), 'newName': 'Element'})
        edits = renamed['changes'][main.as_uri()]
        self.assertEqual(len(edits), 3)
        for edit in edits:
            self.assertEqual(edit['newText'], 'Element')
        tokens = client.request('textDocument/semanticTokens/full', {'textDocument': document})['data']
        line = column = 0
        i_types = []
        for index in range(0, len(tokens), 5):
            delta, char, length, kind, _ = tokens[index:index+5]
            line += delta
            column = char if delta else column + char
            token = text.splitlines()[line].encode('utf-16-le')[column*2:(column+length)*2].decode('utf-16-le')
            if token == 'i':
                i_types.append(client.capabilities['semanticTokensProvider']['legend']['tokenTypes'][kind])
        self.assertEqual(i_types, ['parameter', 'parameter'])

    def test_callback_values_are_parameters_or_variables_and_calls_are_functions(self):
        text = '''package main
import "core:slice"
sorted::proc(a:$T/[]$E,less:=proc(i,j:E)->bool{return i<j},allocator:=context.allocator)->[]E{
    ret:=make([]E,len(a),allocator)
    copy(ret,a)
    slice.sort_by(ret,less)
    _=less(a[0],a[1])
    _=(less)(a[0],a[1])
    return ret
}
typed::proc(a:[]$E,compare:proc(E,E)->bool){slice.sort_by(a,compare);_=compare(a[0],a[1])}
main::proc(){local:=proc(a,b:int)->bool{return a<b};_=local;_=local(1,2)}
'''
        client, main = self.open(text, compiler=False)
        data = client.request('textDocument/semanticTokens/full', {'textDocument': {'uri': main.as_uri()}})['data']
        legend = client.capabilities['semanticTokensProvider']['legend']['tokenTypes']
        tokens = {}
        line = column = 0
        for index in range(0, len(data), 5):
            delta, char, length, kind, _ = data[index:index+5]
            line += delta
            column = char if delta else column+char
            tokens[line, column] = legend[kind]
        for fragment, offset, kind in [
            ('less:=', 0, 'parameter'), ('sort_by(ret,less)', len('sort_by(ret,'), 'parameter'),
            ('_=less(', 2, 'function'), ('_=(less)', 3, 'function'),
            ('sort_by(a,compare)', len('sort_by(a,'), 'parameter'),
            ('_=compare(', 2, 'function'),
            ('local:=', 0, 'variable'), ('_=local;', 2, 'variable'), ('_=local(', 2, 'function'),
        ]:
            with self.subTest(fragment=fragment):
                position = self.position(text, fragment, offset)
                self.assertEqual(tokens.get((position['line'], position['character'])), kind)

    @unittest.skipIf(os.name == 'nt', 'Uses an executable Python test compiler')
    def test_locationless_compiler_error_stays_with_its_package_and_clears(self):
        temporary = tempfile.TemporaryDirectory(prefix='tls-locationless-')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        main, bad, compiler = root/'main.thor', root/'basic/main.thor', root/'compiler.py'
        bad.parent.mkdir()
        clean = 'package main\nmain::proc(){}\n'
        broken = 'package basic\n// BROKEN\n'
        main.write_text(clean)
        bad.write_text(broken)
        compiler.write_text('''#!/usr/bin/env python3
import json,sys
from pathlib import Path
source=Path(sys.argv[2])/'main.thor'
overlay=json.loads(Path(sys.argv[sys.argv.index('--overlay')+1]).read_text())['Replace']
text=Path(overlay.get(str(source),source)).read_text()
errors=[{'type':'error','pos':None,'msgs':['Locationless test compiler error']}] if 'BROKEN' in text else []
print(json.dumps({'error_count':len(errors),'errors':errors}),file=sys.stderr)
raise SystemExit(bool(errors))
''')
        compiler.chmod(0o755)
        client = Client(root, compiler_command=compiler)
        self.addCleanup(client.close)
        for file, text in [(main, clean), (bad, broken)]:
            client.send('textDocument/didOpen', {'textDocument': {'uri': file.as_uri(),
                        'languageId': 'thor', 'version': 1, 'text': text}})
        errors = client.diagnostic(bad.as_uri(), code='thor', contains='Locationless test compiler error')
        self.assertTrue(any('did not provide a source location' in e['message'] for e in errors))
        self.assertFalse(any(n.get('method') == 'textDocument/publishDiagnostics'
                             and n['params']['uri'] == main.as_uri()
                             and any('Locationless test compiler error' in d['message'] for d in n['params']['diagnostics'])
                             for n in client.notifications))
        client.notifications.clear()
        client.send('textDocument/didChange', {'textDocument': {'uri': bad.as_uri(), 'version': 2},
                    'contentChanges': [{'text': 'package basic\n'}]})
        client.diagnostic(bad.as_uri(), empty=True)

    def test_compiler_warning_is_published_once_with_original_range(self):
        text = 'package main\nS :: struct #field_align(4) {x:int}\nmain :: proc(){}\n'
        client, main = self.open(text)
        errors = client.diagnostic(main.as_uri(), code='thor', contains='deprecated')
        warnings = [e for e in errors if 'deprecated' in e['message']]
        self.assertEqual(len(warnings), 1, errors)
        self.assertEqual(warnings[0]['severity'], 2)
        self.assertEqual(warnings[0]['range']['start'], self.position(text, 'field_align', 0))

    def test_immutable_hover_definition_and_readonly_semantic_tokens(self):
        text = 'package main\nmain :: proc(){ value :> 7; _ = value }\n'
        client, main = self.open(text)
        position = self.position(text, 'value }', 2)
        hover = client.request('textDocument/hover', {'textDocument': {'uri': main.as_uri()}, 'position': position})
        self.assertIn('immutable storage', hover['contents']['value'])
        self.assertIn('value: int', hover['contents']['value'])
        definitions = client.request('textDocument/definition', {'textDocument': {'uri': main.as_uri()}, 'position': position})
        if isinstance(definitions, dict):
            definitions = [definitions]
        self.assertTrue(any(d['uri'] == main.as_uri() for d in definitions), definitions)
        tokens = client.request('textDocument/semanticTokens/full', {'textDocument': {'uri': main.as_uri()}})['data']
        readonly = client.capabilities['semanticTokensProvider']['legend']['tokenModifiers'].index('readonly')
        line = column = 0
        occurrences = 0
        for i in range(0, len(tokens), 5):
            delta, character, length, _, modifiers = tokens[i:i+5]
            line += delta
            column = character if delta else column + character
            if text.splitlines()[line][column:column+length] == 'value':
                self.assertTrue(modifiers & (1 << readonly), tokens[i:i+5])
                occurrences += 1
        self.assertEqual(occurrences, 2)

    def test_immutable_imported_builtin_result_in_fn_loop_and_unsaved_recovery(self):
        text='''// 😀 original source
package main
import "core:math"
Board::[3]int
find::fn(board:Board)->int{
    total:=0
    for top_card in board {
        diff:=7-top_card
        guess_dist :> math.abs(diff)
        total+=guess_dist
    }
    return total
}
main::proc(){board:=Board{1,2,3};_=find(board)}
'''
        client,main=self.open(text)
        readonly=1 << client.capabilities['semanticTokensProvider']['legend']['tokenModifiers'].index('readonly')
        def check(source,expected):
            spans=[(kind,mods) for _,_,kind,name,mods in self.semantic_spans(client,main,source) if name=='guess_dist']
            count=source.count('guess_dist')
            self.assertEqual(len(spans),count,spans)
            self.assertEqual([(kind,bool(mods&readonly)) for kind,mods in spans],[('variable',expected)]*count)
            hover=self.hover(client,main,source,'guess_dist\n')['contents']['value']
            self.assertIn('guess_dist: int',hover)
            self.assertEqual('immutable storage' in hover,expected)
        check(text,True)
        mutable=text.replace('guess_dist :>','guess_dist :=')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':mutable}]})
        check(mutable,False)
        broken=text.replace('        total+=guess_dist','        guess_dist=0\n        total+=guess_dist')
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':3},'contentChanges':[{'text':broken}]})
        errors=client.diagnostic(main.as_uri(),contains="immutable storage 'guess_dist'")
        self.assertEqual(errors[0]['range']['start'],self.position(broken,'guess_dist=0',0))
        check(broken,True)
        client.notifications.clear()
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':4},'contentChanges':[{'text':text}]})
        client.diagnostic(main.as_uri(),empty=True)
        check(text,True)
        self.assertEqual(main.read_text(),text)

    def test_imported_builtin_aliases_keep_result_types_and_binding_mutability(self):
        text='''package main
import m "core:math"
import _ "core:math" / abs
import builtin "base:builtin"
import user "library"
magnitude::m.abs
again::magnitude
main::proc(){
    n:=-3
    aliased :> again(n)
    selected :> abs(n)
    explicit :> builtin.abs(n)
    floating :> m.abs(f64(n))
    bounded :> m.clamp(n,-7,7)
    minimum :> m.min(n,0)
    maximum :> m.max(n,0)
    mutable := m.abs(n)
    user_value :> user.abs(n)
    _=aliased;_=selected;_=explicit;_=floating;_=bounded;_=minimum;_=maximum;_=mutable
    _=user_value
}
'''
        client,main=self.open(text,files={'library/main.odin':'package library\nabs::proc(n:int)->string{return "user"}\n'},compiler=False)
        readonly=1 << client.capabilities['semanticTokensProvider']['legend']['tokenModifiers'].index('readonly')
        for name in ['aliased','selected','explicit','floating','bounded','minimum','maximum','mutable','user_value']:
            with self.subTest(name=name):
                tokens=[(kind,bool(mods&readonly)) for _,_,kind,word,mods in self.semantic_spans(client,main,text) if word==name]
                self.assertEqual(tokens,[('variable',name!='mutable')]*2,tokens)
                hover=self.hover(client,main,text,name+' :')['contents']['value']
                self.assertIn(name+(': f64' if name=='floating' else ': string' if name=='user_value' else ': int'),hover)

    def test_multi_expression_immutable_coordinates_hover_and_write_diagnostic(self):
        text='''package main
main::proc(){
    mousePos:=struct{x,y:f32}{175,225}
    GRID_SIZE::50
    x, y :> int(mousePos.x)/GRID_SIZE, int(mousePos.y)/GRID_SIZE
    _ = x + y
}
'''
        client,main=self.open(text)
        for name,needle,offset in [('x','x + y',0),('y','+ y',2)]:
            hover=client.request('textDocument/hover',{'textDocument':{'uri':main.as_uri()},
                'position':self.position(text,needle,offset)})
            self.assertIn('immutable storage',hover['contents']['value'])
            self.assertIn(name+': int',hover['contents']['value'])
        changed=text.replace('    _ = x + y','    y = 8\n    _ = x + y')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},
            'contentChanges':[{'text':changed}]})
        errors=client.diagnostic(main.as_uri(),code='thor',contains="immutable storage 'y'")
        error=next(e for e in errors if "immutable storage 'y'" in e['message'])
        self.assertEqual(error['range']['start'],self.position(changed,'y = 8',0))

    def test_immutable_write_diagnostic_and_unsaved_mutable_fix(self):
        text = 'package main\nmain :: proc(){ value :> 7; value = 9 }\n'
        client, main = self.open(text)
        errors = client.diagnostic(main.as_uri(), code='thor', contains='immutable storage')
        error = next(e for e in errors if 'immutable storage' in e['message'])
        self.assertEqual(error['range']['start'], self.position(text, 'value = 9', 0))
        self.assertNotIn('__thor_', error['message'])
        fixed = text.replace('value :> 7', 'value := 7')
        client.notifications.clear()
        client.send('textDocument/didChange', {'textDocument': {'uri': main.as_uri(), 'version': 2}, 'contentChanges': [{'text': fixed}]})
        self.assertEqual(client.diagnostic(main.as_uri(), empty=True), [])
        hover = client.request('textDocument/hover', {'textDocument': {'uri': main.as_uri()}, 'position': self.position(fixed, 'value = 9', 2)})
        self.assertNotIn('immutable storage', hover['contents']['value'])
        self.assertEqual(main.read_text(), text)

    def test_runtime_support_collection_resolves_in_editor(self):
        text = 'package main\nimport f "thor:format"\nmain :: proc(){ text := f.group_integer(1234); delete(text) }\n'
        client, main = self.open(text)
        params = {'textDocument': {'uri': main.as_uri()}, 'position': self.position(text, 'group_integer', 2)}
        hover = client.request('textDocument/hover', params)
        self.assertIn('string', hover['contents']['value'])
        definitions = client.request('textDocument/definition', params)
        if isinstance(definitions, dict):
            definitions = [definitions]
        self.assertTrue(any(d['uri'].endswith('/runtime/format/format.odin') for d in definitions), definitions)

    def test_bundled_basic_import_navigation_completion_and_diagnostics(self):
        text = '''package main
import "thor:basic"
main :: proc() {
    result := basic.sorted([]int{3, 1, 2})
    defer delete(result)
    basic.print(result)
}
'''
        client, main = self.open(text)
        params = {'textDocument': {'uri': main.as_uri()},
                  'position': self.position(text, 'basic.sorted', len('basic.')+2)}
        hover = client.request('textDocument/hover', params)
        self.assertIn('sorted', hover['contents']['value'])
        self.assertIn('less', hover['contents']['value'])
        definition = client.request('textDocument/definition', params)
        self.assertTrue(definition['uri'].endswith('/runtime/basic/basic.thor'), definition)
        self.assertIn('namespace', [kind for _, _, kind, token, _ in self.semantic_spans(client, main, text)
                                   if token == 'basic'])
        completion = client.request('textDocument/completion', {
            'textDocument': {'uri': main.as_uri()},
            'position': self.position(text, 'basic.sorted', len('basic.')),
            'context': {'triggerKind': 1}})
        labels = [item['label'] for item in completion['items']]
        self.assertIn('sorted', labels)
        completion = client.request('textDocument/completion', {
            'textDocument': {'uri': main.as_uri()},
            'position': self.position(text, 'basic.print', len('basic.')),
            'context': {'triggerKind': 1}})
        self.assertIn('print', [item['label'] for item in completion['items']])
        # A clean initial file need not publish an empty diagnostics notification.
        # Introduce and fix a real call error to verify the compiler path too.
        broken = text.replace('sorted([]int{3, 1, 2})', 'sorted([]int{3, 1, 2}, less=7)')
        client.send('textDocument/didChange', {'textDocument': {'uri': main.as_uri(), 'version': 2},
                    'contentChanges': [{'text': broken}]})
        errors = client.diagnostic(main.as_uri(), contains='Cannot convert')
        error = next(e for e in errors if 'Cannot convert' in e['message'])
        self.assertEqual(error['range']['start'], self.position(broken, 'less=7', len('less=')))
        client.notifications.clear()
        client.send('textDocument/didChange', {'textDocument': {'uri': main.as_uri(), 'version': 3},
                    'contentChanges': [{'text': text}]})
        client.diagnostic(main.as_uri(), empty=True)

    def test_bundled_basic_selected_import_resolves_to_original_thor_source(self):
        text = 'package main\nimport "thor:basic" / sorted\nmain::proc(){data:=sorted([]int{2,1});delete(data)}\n'
        client, main = self.open(text)
        params = {'textDocument': {'uri': main.as_uri()},
                  'position': self.position(text, 'data:=sorted', len('data:=')+2)}
        self.assertIn('less', client.request('textDocument/hover', params)['contents']['value'])
        definition = client.request('textDocument/definition', params)
        self.assertTrue(definition['uri'].endswith('/runtime/basic/basic.thor'), definition)

    def test_generic_immutable_error_is_mapped_to_operation(self):
        text = 'package main\nf::proc(value:$T){x:>value; x[0]=3}\nmain::proc(){f([1,2])}\n'
        client, main = self.open(text)
        errors = client.diagnostic(main.as_uri(), code='thor', contains='immutable storage')
        error = next(e for e in errors if 'immutable storage' in e['message'])
        self.assertEqual(error['range']['start'], self.position(text, 'x[0]=3', 0))
        self.assertNotIn('Thor expression', error['message'])

    def test_compiler_diagnostics_follow_unsaved_edits_and_clear(self):
        text = 'package main\nmain :: proc(){ _ = missing }\n'
        client, main = self.open(text)
        client.diagnostic(main.as_uri(), code='thor', contains='missing')
        changed = 'package main\nmain :: proc(){ a := [1,2]; _ = a\n _ = "😀"; _ = other_missing }\n'
        client.send('textDocument/didChange', {'textDocument': {'uri': main.as_uri(), 'version': 2}, 'contentChanges': [{'text': changed}]})
        errors = client.diagnostic(main.as_uri(), code='thor', contains='other_missing')
        error = next(e for e in errors if 'other_missing' in e['message'])
        self.assertEqual(error['range']['start'], self.position(changed, 'other_missing', 0))
        fixed = 'package main\nmain :: proc(){ a := [1,2]; _ = a }\n'
        client.notifications.clear()
        client.send('textDocument/didChange', {'textDocument': {'uri': main.as_uri(), 'version': 3}, 'contentChanges': [{'text': fixed}]})
        self.assertEqual(client.diagnostic(main.as_uri(), empty=True), [])
        self.assertEqual(main.read_text(), text)

    def test_helper_diagnostic_points_to_membership(self):
        text = 'package main\nmain :: proc(){ s := []int{1}; _ = s in [s] }\n'
        client, main = self.open(text)
        errors = client.diagnostic(main.as_uri(), code='thor', contains='Membership requires')
        error = next(e for e in errors if 'Membership requires' in e['message'])
        self.assertEqual(error['range']['start'], self.position(text, 's in [s]', 0))
        self.assertNotIn('__thor_', error['message'])

    def test_compiler_reports_imported_source_uri(self):
        text = 'package main\nimport "lib"\nmain :: proc(){lib.f()}\n'
        client, main = self.open(text, {'lib/lib.thor': 'package lib\nf :: proc(){ _ = missing }\n'})
        uri = (main.parent / 'lib/lib.thor').as_uri()
        errors = client.diagnostic(uri, code='thor', contains='missing')
        self.assertEqual(errors[0]['range']['start']['line'], 1)

    def test_rapid_edits_eventually_publish_latest_snapshot(self):
        text = 'package main\nmain :: proc(){ _ = first_missing }\n'
        client, main = self.open(text)
        for version in range(2, 7):
            changed = f'package main\nmain :: proc(){{ a := [1,2]; _ = a; _ = missing_{version} }}\n'
            client.send('textDocument/didChange', {'textDocument': {'uri': main.as_uri(), 'version': version}, 'contentChanges': [{'text': changed}]})
        errors = client.diagnostic(main.as_uri(), code='thor', contains='missing_6')
        self.assertTrue(all('missing_' not in e['message'] or 'missing_6' in e['message'] for e in errors), errors)

    def test_compiler_diagnostics_preserve_symlinked_editor_uri(self):
        temporary = tempfile.TemporaryDirectory(prefix='tls-symlink-')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        real = root / 'real'
        real.mkdir()
        alias = root / 'alias'
        alias.symlink_to(real, target_is_directory=True)
        main = alias / 'main.thor'
        text = 'package main\nmain :: proc(){_ = missing}\n'
        main.write_text(text)
        client = Client(alias)
        self.addCleanup(client.close)
        client.send('textDocument/didOpen', {'textDocument': {
            'uri': main.as_uri(), 'languageId': 'thor', 'version': 1, 'text': text}})
        errors = client.diagnostic(main.as_uri(), code='thor', contains='missing')
        self.assertEqual(errors[0]['range']['start'], self.position(text, 'missing', 0))

    def test_new_file_buffer_can_be_checked_before_first_save(self):
        temporary = tempfile.TemporaryDirectory(prefix='tls-new-file-')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        main = root / 'main.thor'
        text = 'package main\nmain :: proc(){a := [1,"wrong"]; _ = a}\n'
        client = Client(root, compiler=False)
        self.addCleanup(client.close)
        client.send('textDocument/didOpen', {'textDocument': {
            'uri': main.as_uri(), 'languageId': 'thor', 'version': 1, 'text': text}})
        errors = client.diagnostic(main.as_uri(), code='thor', contains='Cannot convert')
        self.assertEqual(errors[0]['range']['start'], self.position(text, '"wrong"', 0))
        self.assertFalse(main.exists(), 'Checking a new buffer must not create its source file')


if __name__ == '__main__':
    unittest.main()
