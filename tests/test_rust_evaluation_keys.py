"""Toolchain, resource and emitted-dependency cache keys."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from thorlang.cache import EvaluationCache
from thorlang.compiler import Source
from thorlang.driver import frontend_binary
from tests import test_rust_evaluation_cache as storage


class RustEvaluationKeyTests(unittest.TestCase):
    maxDiff=None
    requests=storage.RustEvaluationCacheTests.requests
    setUp=storage.RustEvaluationCacheTests.setUp

    @classmethod
    def setUpClass(cls):
        storage.RustEvaluationCacheTests.setUpClass.__func__(cls)
        cls.frontend=frontend_binary('odin')[0]

    def run_native(self,operations,sources=None,collections=None,enabled=True):
        request=dict(directory=str(self.root/'cache'),operations=operations,enabled=enabled)
        if sources is not None:
            request.update(sources=[str(path) for path in sources],frontend=str(self.frontend),collections=collections or {})
        return self.requests([request])[0]

    def toolchain(self):
        native=self.root/'native'
        for name in ['base','core','vendor']:(native/name).mkdir(parents=True)
        (native/'core/math.odin').write_text('package math\nVALUE::1\n')
        support=self.root/'support'
        for name in ['rust/src','frontend','runtime']:(support/name).mkdir(parents=True)
        (support/'rust/src/compiler.rs').write_text('// compiler\n')
        (support/'frontend/parser.odin').write_text('package parser\n')
        (support/'runtime/pure.odin').write_text('package pure\n')
        (support/'runtime/basic.thor').write_text('package basic\nVALUE::1\n')
        compiler=native/'odin'
        compiler.write_text('#!/usr/bin/env python3\nimport sys\n'+
            f'print({str(native)!r} if sys.argv[1]=="root" else "identity test compiler")\n')
        compiler.chmod(0o755)
        return compiler,support

    def identity(self,compiler,support):
        return self.run_native([dict(op='identity',odin=str(compiler),root=str(support))])['answers'][0]

    def test_identity_reuses_validated_metadata_record(self):
        compiler,support=self.toolchain()
        before=self.identity(compiler,support)
        records=list((self.root/'cache/identity-v1').glob('*.json'))
        after=self.identity(compiler,support)
        final_records=list((self.root/'cache/identity-v1').glob('*.json'))
        self.assertIsInstance(before,str)
        self.assertEqual(len(before),64)
        self.assertEqual(before,after)
        self.assertEqual(final_records,records)

    def test_support_frontend_and_native_source_changes_invalidate(self):
        compiler,support=self.toolchain()
        for path in [support/'runtime/pure.odin',support/'frontend/parser.odin',
                     support/'rust/src/compiler.rs',self.root/'native/core/math.odin']:
            with self.subTest(path=path):
                before=self.identity(compiler,support)
                path.write_text(path.read_text()+'// changed\n')
                after=self.identity(compiler,support)
                self.assertNotEqual(before,after)

    def test_editable_thor_library_source_also_invalidates_identity(self):
        compiler,support=self.toolchain()
        before=self.identity(compiler,support)
        (support/'runtime/basic.thor').write_text('package basic\nVALUE::2\n')
        after=self.identity(compiler,support)
        self.assertNotEqual(before,after)

    def test_compiler_executable_change_invalidates_identity(self):
        compiler,support=self.toolchain()
        before=self.identity(compiler,support)
        compiler.write_text(compiler.read_text()+'# locally rebuilt executable\n')
        after=self.identity(compiler,support)
        self.assertNotEqual(before,after)

    def test_ignored_file_change_does_not_change_contents_identity(self):
        compiler,support=self.toolchain()
        before=self.identity(compiler,support)
        (self.root/'native/core/notes.txt').write_text('notes')
        after=self.identity(compiler,support)
        self.assertEqual(before,after)

    def test_invalid_saved_identity_is_recomputed(self):
        compiler,support=self.toolchain()
        before=self.identity(compiler,support)
        record=next((self.root/'cache/identity-v1').glob('*.json'))
        record.write_text('{')
        after=self.identity(compiler,support)
        self.assertEqual(before,after)

    def test_failed_compiler_query_is_optional(self):
        compiler,support=self.toolchain()
        compiler.write_text('#!/bin/sh\nexit 2\n')
        answer=self.identity(compiler,support)
        self.assertIsNone(answer)

    def source(self,expression):
        path=self.root/'main.thor';path.write_text('package main\nDATA::'+expression+'\nmain::proc(){}\n')
        return path

    def resources(self,paths,collections=None):
        actual=self.run_native([{'op':'resources'}],sources=paths,collections=collections)['answers'][0]
        sources={}
        for path in paths:
            source=Source(path,self.frontend,cache=False)
            source.collections={name:Path(path) for name,path in (collections or {}).items()}
            sources[path]=source
        expected=EvaluationCache(self.root/'reference').resources(SimpleNamespace(sources=sources))
        self.assertEqual(actual,expected)
        return actual

    def test_resource_keys_match_reference_for_file_directory_and_missing_paths(self):
        (self.root/'data').write_text('data')
        directory=self.root/'assets';directory.mkdir();(directory/'one').write_text('one')
        (directory/'nested').mkdir();(directory/'nested/ignored').write_text('ignored')
        for expression in ['#load("data")','#load_hash("data")','#load_directory("assets")','#exists("missing")']:
            with self.subTest(expression=expression):self.resources([self.source(expression)])

    def test_collection_resources_and_dynamic_resource_bypass(self):
        assets=self.root/'assets';assets.mkdir();(assets/'data').write_text('data')
        self.resources([self.source('#load("assets:data")')],{'assets':str(assets)})
        value=self.resources([self.source('#load(NAME)')])
        self.assertIsNone(value)
        value=self.resources([self.source('#load("unknown:resource")')])
        self.assertIsNone(value)

    def test_constant_resource_paths_track_aliases_concatenation_and_both_platform_branches(self):
        assets=self.root/'assets';assets.mkdir()
        first=assets/'first';first.write_text('first')
        second=assets/'second';second.write_text('second')
        path=self.root/'main.thor'
        for directive in ['#load','#load_hash','#load_directory','#exists']:
            with self.subTest(directive=directive):
                path.write_text('package main\nROOT::"assets/"\n'
                    'NAME::("first" when ODIN_OS==.Darwin else "second")\n'
                    f'ALIAS::ROOT+NAME\nDATA::{directive}(ALIAS)\nmain::proc(){{}}\n')
                before=self.run_native([{'op':'resources'}],sources=[path])['answers'][0]
                self.assertIsInstance(before,str)
                second.write_text(second.read_text()+'changed')
                after=self.run_native([{'op':'resources'}],sources=[path])['answers'][0]
                self.assertNotEqual(before,after)

    def test_unresolved_shadowed_cyclic_and_unbounded_resource_paths_bypass(self):
        path=self.root/'main.thor'
        programs=[
            'NAME::"file"\nwork::proc(NAME:string){_=#exists(NAME)}',
            'work::proc($NAME:string="file"){_=#exists(NAME)}',
            'A::B\nB::A\nDATA::#exists(A)',
            'NAME::#config(RESOURCE,"file")\nDATA::#exists(NAME)',
            'when ODIN_OS==.Darwin{NAME::"a"}else{NAME::"b"}\nDATA::#exists(NAME)',
            'NAME::"file"\nwork::proc(){NAME:="other";_=#exists(NAME)}',
            '\n'.join(f'PART{i}::("a{i}" when ODIN_DEBUG else "b{i}")' for i in range(7))+
                '\nNAME::'+'+'.join(f'PART{i}' for i in range(7))+'\nDATA::#exists(NAME)',
            'P0::"file"\n'+'\n'.join(f'P{i}::(P{i-1} when ODIN_DEBUG else P{i-1})' for i in range(1,20))+
                '\nDATA::#exists(P19)',
        ]
        for program in programs:
            with self.subTest(program=program):
                path.write_text('package main\n'+program+'\nmain::proc(){}\n')
                answer=self.run_native([{'op':'resources'}],sources=[path])['answers'][0]
                self.assertIsNone(answer)

    def key_operation(self,compiler,support,destination=None):
        destination=destination or self.root/'helper'
        return dict(op='key',odin=str(compiler),root=str(support),destination=str(destination),
            target=str(destination/'main.odin'),flags=['-o:speed','-collection:local='+str(destination/'local')],
            generated={str(destination/'main.odin'):'package main\n// '+str(destination)+'\nmain::proc(){}\n'})

    def test_helper_destination_is_normalized(self):
        compiler,support=self.toolchain()
        answers=self.run_native([self.key_operation(compiler,support,self.root/'one'),
                                 self.key_operation(compiler,support,self.root/'two')])['answers']
        self.assertIsInstance(answers[0],str)
        self.assertEqual(answers[0],answers[1])

    def test_emitted_dependencies_flags_and_target_change_keys(self):
        compiler,support=self.toolchain();original=self.key_operation(compiler,support)
        operations=[original]
        changed=copy.deepcopy(original);changed['flags'].append('-disable-assert');operations.append(changed)
        changed=copy.deepcopy(original);changed['target']+='-other';operations.append(changed)
        changed=copy.deepcopy(original);changed['generated'][next(iter(changed['generated']))]+='// changed\n';operations.append(changed)
        answers=self.run_native(operations)['answers']
        for answer in answers[1:]:self.assertNotEqual(answer,answers[0])

    def test_non_odin_outputs_do_not_change_result_keys(self):
        compiler,support=self.toolchain();original=self.key_operation(compiler,support)
        changed=copy.deepcopy(original);changed['generated'][str(self.root/'helper/manifest.json')]='metadata'
        answers=self.run_native([original,changed])['answers']
        self.assertEqual(answers[0],answers[1])

    def test_resource_change_invalidates_final_key(self):
        compiler,support=self.toolchain();operation=self.key_operation(compiler,support)
        resource=self.root/'data';resource.write_text('before');source=self.source('#load("data")')
        before=self.run_native([operation],sources=[source])['answers'][0]
        resource.write_text('after')
        after=self.run_native([operation],sources=[source])['answers'][0]
        self.assertNotEqual(before,after)

    def test_disabled_dynamic_or_failed_identity_bypasses_are_counted(self):
        compiler,support=self.toolchain();operation=self.key_operation(compiler,support)
        disabled=self.run_native([operation],enabled=False)
        self.assertEqual(disabled['answers'],[None])
        self.assertEqual(disabled['stats']['bypasses'],1)
        source=self.source('#load(NAME)')
        dynamic=self.run_native([operation],sources=[source])
        self.assertEqual(dynamic['answers'],[None])
        self.assertEqual(dynamic['stats']['bypasses'],1)
        compiler.write_text('#!/bin/sh\nexit 2\n')
        failed=self.run_native([operation])
        self.assertEqual(failed['answers'],[None])
        self.assertEqual(failed['stats']['bypasses'],1)


if __name__=='__main__':unittest.main()
