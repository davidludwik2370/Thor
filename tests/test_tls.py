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

ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / 'tls' / 'bin' / ('tls.exe' if os.name == 'nt' else 'tls')


class Client:
    def __init__(self, directory, *, compiler=True, compiler_command=None):
        self.directory = directory
        self.messages = queue.Queue()
        self.notifications = []
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
            'capabilities': {'textDocument': {'hover': {'contentFormat': ['markdown']},
                            'completion': {'completionItem': {'snippetSupport': True}},
                            'semanticTokens': {'requests': {'full': True}}}},
            'initializationOptions': {'odin_root_override': odin_root, 'enable_format': False,
                'odin_command': odin, 'thor_command': str(compiler_command or ROOT / 'thor') if compiler else '',
                'enable_parser_errors': True, 'enable_semantic_tokens': True,
                'enable_unused_imports_reporting': False, 'enable_auto_import': False,
                'enable_checker_workspace_diagnostics': False, 'thread_count': 1}
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

    def diagnostic(self, uri, *, empty=False, code=None, contains=None):
        def matching(value):
            return (value.get('method') == 'textDocument/publishDiagnostics'
                    and value['params'].get('uri') == uri
                    and (not empty or not value['params']['diagnostics'])
                    and (code is None or any(d.get('code') == code for d in value['params']['diagnostics']))
                    and (contains is None or any(contains in d['message'] for d in value['params']['diagnostics'])))
        deadline = time.monotonic() + 15
        while True:
            for value in self.notifications:
                if matching(value):
                    self.notifications.remove(value)
                    return value['params']['diagnostics']
            self.next(max(0.01, deadline - time.monotonic()))

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


class TLSTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which(os.environ.get('THOR_ODIN', 'odin')):
            raise unittest.SkipTest('Odin is required for TLS tests')
        subprocess.run(['python3', str(ROOT / 'tools' / 'build_tls.py')], check=True,
                       capture_output=True, timeout=90)

    def open(self, text, files=None):
        temporary = tempfile.TemporaryDirectory(prefix='tls-test-')
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name).resolve()
        main = directory / 'main.thor'
        for name, content in {'main.thor': text, **(files or {})}.items():
            destination = directory / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content)
        client = Client(directory)
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
#if true {selected::proc(){}}#else{selected::proc(){}}
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

    @unittest.expectedFailure
    def test_known_gap_contextual_enum_navigation_in_switch_array_arm(self):
        # The underlying OLS resolver does not propagate the array's expected
        # element type through Thor switch arms. Color ranges remain correct.
        text='package main\nColor::enum{Red,Blue}\nmain::proc(){x:=1;choice:[2]Color=switch x{case 1:[.Red,.Blue];case:[.Blue,.Red]};_=choice}\n'
        client,main=self.open(text)
        self.assertIsNotNone(self.hover(client,main,text,'.Red'))

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
        self.assertFalse(client.capabilities['renameProvider'])
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
        text='package main\nwork::fn()->int{values:=[1,2];for i in 0..<len(values){values[i]+=1};return values[0]}\nmain::proc(){_=work()}\n'
        client,main=self.open(text)
        changed=text.replace('for i in 0..<len(values){values[i]+=1}', 'for &value in values{value+=1}')
        client.send('textDocument/didChange',{'textDocument':{'uri':main.as_uri(),'version':2},'contentChanges':[{'text':changed}]})
        errors=client.diagnostic(main.as_uri(),contains='Pure code cannot iterate by mutable reference')
        error=next(e for e in errors if 'Pure code cannot iterate by mutable reference' in e['message'])
        self.assertEqual(error['range']['start'],self.position(changed,'for &value',0))
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
        text='package main\n#if 2**3**2==512 && -2**2 == -4 {SELECTED::42} #else {SELECTED::false}\nmain::proc(){_="😀";base:f64=2;exponent:f64 = -3;result:=base**exponent;_=result;_=SELECTED}\n'
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

    def test_range_membership_constraints_hover_tokens_and_unsaved_errors(self):
        text='''package main
allowed::proc($N:int)->int where N in 0..<10{return N}
#if -2 in -3..=0 {SELECTED::42} #else {SELECTED::false}
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
