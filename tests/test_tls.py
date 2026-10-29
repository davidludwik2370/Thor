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
    def __init__(self, directory, *, compiler=True, compiler_command=None, unused_imports=False):
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
                'enable_unused_imports_reporting': unused_imports,
                'enable_unused_imports_on_change': unused_imports, 'enable_auto_import': False,
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
        self.assertEqual(error['range']['start'],self.position(changed,'state+n',0))
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
