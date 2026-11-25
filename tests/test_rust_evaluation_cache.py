"""Contents-cache storage and canonical keys against the frozen Python API."""
import hashlib
import json
import math
from pathlib import Path
import random
import struct
import subprocess
import tempfile
import unittest
from thorlang.cache import EvaluationCache, canonical
from tests.native_paths import rust_binary
from tests.cache_paths import record
from tests import test_rust_compiler as native


class RustEvaluationCacheTests(unittest.TestCase):
    maxDiff = None
    key='a'*64
    target='b'*64

    @classmethod
    def setUpClass(cls):
        binary=rust_binary('cache_protocol',example=True)
        if native.using_prebuilt_compiler(binary):
            cls.binary=binary
            return
        result=subprocess.run(['cargo','build','--release','--locked','--offline',
            '--manifest-path',str(native.ROOT/'rust/Cargo.toml'),'--example','cache_protocol'],
            capture_output=True,text=True,timeout=600)
        if result.returncode:
            raise RuntimeError(result.stdout+result.stderr)
        cls.binary=rust_binary('cache_protocol', example=True)

    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='thor-native-cache-storage-')
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve()

    def requests(self,requests):
        result=subprocess.run([str(self.binary)],input=''.join(json.dumps(item)+'\n' for item in requests),
            capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        return [json.loads(line) for line in result.stdout.splitlines()]

    def run_native(self,operations,directory=None,enabled=True):
        return self.requests([{'directory':str(directory or self.root/'native'),
                              'enabled':enabled,'operations':operations}])[0]

    def run_reference(self,operations,enabled=True):
        cache=EvaluationCache(self.root/'reference',enabled=enabled)
        answers=[]
        for operation in operations:
            op=operation['op'];key=operation.get('key');namespace=operation.get('namespace','prepared-v1')
            if op=='put':answer=cache.put(key,operation['value'])
            elif op=='get':answer=cache.get(key)
            elif op=='write_record':answer=cache.write_record(namespace,key,operation['data'])
            elif op=='read_record':answer=cache.read_record(namespace,key)
            elif op=='remember':answer=cache.remember(key,operation.get('target'))
            elif op=='prepared':answer=list(cache.prepared(key,operation.get('skip')))
            elif op=='clear':answer=cache.clear()
            else:raise ValueError(op)
            answers.append(answer)
        return dict(answers=answers,stats=cache.stats,prepared_hits=cache.prepared_hits)

    def compare(self,operations,enabled=True):
        expected=self.run_reference(operations,enabled)
        actual=self.run_native(operations,enabled=enabled)
        self.assertEqual(actual,expected)
        return actual

    def test_canonical_unicode_controls_and_sorted_keys(self):
        values=[{'z':'héllo 😀','a':['\x00\n\t\r\b\f\\"',None,True,False]},
                {'é':1,'界':2,'a':3}, {'nested':{'b':2,'a':1}}, [], {}, -0.0]
        actual=self.requests([{'canonical':value} for value in values])
        expected=[{'text':canonical(value).decode(),'sha256':hashlib.sha256(canonical(value)).hexdigest()} for value in values]
        self.assertEqual(actual,expected)

    def test_canonical_float_boundaries_and_generated_finite_values(self):
        rng=random.Random(1905)
        values=[0.0,-0.0,1.0,-1.0,1e-4,1e-5,1e15,1e16,1e20,1e21,1.2345e-9,5e-324]
        while len(values)<212:
            value=struct.unpack('!d',rng.randbytes(8))[0]
            if math.isfinite(value):values.append(value)
        actual=self.requests([{'canonical':value} for value in values])
        for value,answer in zip(values,actual):
            with self.subTest(value=value):
                expected=canonical(value).decode()
                self.assertEqual(answer['text'],expected)

    def test_scalar_packets_and_statistics(self):
        self.compare([{'op':'get','key':self.key},
            {'op':'put','key':self.key,'value':{'kind':'integer','value':'7'}},
            {'op':'get','key':self.key}, {'op':'put','key':None,'value':{'kind':'bool','value':True}},
            {'op':'get','key':None}])

    def test_owned_contents_are_portable_packets(self):
        value={'kind':'sequence','storage':'dynamic','nil':False,'capacity':8,
               'values':[{'kind':'record','fields':[['text',{'kind':'string','bytes':'68c3a9'}],
                    ['number',{'kind':'integer','value':'340282366920938463463374607431768211455'}]]}]}
        self.compare([{'op':'put','key':self.key,'value':value},{'op':'get','key':self.key}])

    def test_python_and_rust_read_each_others_packets(self):
        directory=self.root/'shared'
        reference=EvaluationCache(directory)
        value={'kind':'integer','value':'-170141183460469231731687303715884105728'}
        reference.put(self.key,value)
        answer=self.run_native([{'op':'get','key':self.key}],directory)
        self.assertEqual(answer['answers'],[value])
        value={'kind':'string','bytes':'00ff6869'}
        self.run_native([{'op':'put','key':self.target,'value':value}],directory)
        # The packet format remains readable by the frozen reference; bounded
        # native storage now uses a different directory layout.
        packet=record(directory,'eval-v1',self.target).read_bytes()
        (directory/'eval-v1'/(self.target+'.json')).write_bytes(packet)
        actual=reference.get(self.target)
        self.assertEqual(actual,value)

    def test_prepared_targets_hits_and_skips(self):
        self.compare([{'op':'prepared','key':self.key},
            {'op':'put','key':self.target,'value':{'kind':'integer','value':'9'}},
            {'op':'remember','key':self.key,'target':self.target},
            {'op':'prepared','key':self.key}, {'op':'prepared','key':self.key,'skip':self.target}])

    def test_metadata_records_and_disabled_record_storage(self):
        operations=[{'op':'write_record','namespace':'scalar-v1','key':self.key,'data':{'label':'界','n':1e-5}},
                    {'op':'read_record','namespace':'scalar-v1','key':self.key}]
        self.compare(operations)
        self.compare(operations,enabled=False)

    def test_corrupt_checksum_schema_key_and_partial_packets_are_misses(self):
        directory=self.root/'corrupt'
        value={'kind':'integer','value':'7'}
        self.run_native([{'op':'put','key':self.key,'value':value}],directory)
        path=record(directory,'eval-v1',self.key)
        original=json.loads(path.read_text())
        variants=['{', {**original,'schema':999}, {**original,'key':self.target},
                  {**original,'value':{'kind':'integer','value':'8'}}]
        for variant in variants:
            with self.subTest(variant=variant):
                path.write_text(variant if isinstance(variant,str) else json.dumps(variant))
                answer=self.run_native([{'op':'get','key':self.key}],directory)
                self.assertEqual(answer['answers'],[None])
                self.assertEqual(answer['stats']['corrupt'],1)

    def test_correct_checksum_does_not_allow_invalid_value_contents(self):
        directory=self.root/'invalid';(directory/'eval-v1').mkdir(parents=True)
        value={'kind':'integer','value':'not a number'}
        path=directory/'eval-v1'/(self.key+'.json')
        path.write_text(json.dumps(dict(schema=1,key=self.key,value=value,checksum=hashlib.sha256(canonical(value)).hexdigest())))
        answer=self.run_native([{'op':'get','key':self.key}],directory)
        self.assertEqual(answer['answers'],[None])
        self.assertEqual(answer['stats']['corrupt'],1)

    def test_invalid_publication_is_optional(self):
        self.compare([{'op':'put','key':self.key,'value':{'kind':'pointer','value':'123'}},
                      {'op':'get','key':self.key}])

    def test_unavailable_storage_is_optional(self):
        directory=self.root/'blocked';directory.write_text('file, not directory')
        answer=self.run_native([{'op':'put','key':self.key,'value':{'kind':'integer','value':'1'}},
                               {'op':'get','key':self.key}],directory)
        self.assertEqual(answer['answers'],[None,None])
        self.assertEqual(answer['stats']['writes'],0)
        contents=directory.read_text()
        self.assertEqual(contents,'file, not directory')

    def test_oversize_input_is_a_miss(self):
        directory=self.root/'large';(directory/'eval-v1').mkdir(parents=True)
        with (directory/'eval-v1'/(self.key+'.json')).open('wb') as stream:
            stream.seek(17*1024*1024);stream.write(b'x')
        answer=self.run_native([{'op':'get','key':self.key}],directory)
        self.assertEqual(answer['answers'],[None])
        self.assertEqual(answer['stats']['corrupt'],1)

    def test_atomic_concurrent_publication_has_no_partial_packet(self):
        directory=self.root/'parallel'
        values=[{'kind':'integer','value':str(i)} for i in range(24)]
        answer=self.run_native([{'op':'parallel','key':self.key,'values':values},
                               {'op':'get','key':self.key}],directory)
        self.assertEqual(answer['answers'][0],{'failed':False})
        self.assertIn(answer['answers'][1],values)
        temporary_files=list((directory/'eval-v1').glob('.entry-*'))
        self.assertEqual(temporary_files,[])

    def test_clear_preserves_unrelated_files_directories_and_symlinks(self):
        directory=self.root/'clear'
        self.run_native([{'op':'put','key':self.key,'value':{'kind':'integer','value':'7'}},
                         {'op':'write_record','key':self.target,'namespace':'scalar-v1','data':{'n':3}}],directory)
        (directory/'eval-v1').mkdir(exist_ok=True)
        keep=directory/'eval-v1/keep.json';keep.write_text('keep')
        outside=directory/'outside';outside.write_text('outside')
        link=directory/'eval-v1'/('c'*64+'.json');link.symlink_to(outside)
        folder=directory/'eval-v1'/('d'*64+'.json');folder.mkdir()
        self.run_native([{'op':'clear'}],directory)
        contents=keep.read_text()
        self.assertEqual(contents,'keep')
        self.assertTrue(link.is_symlink())
        self.assertTrue(folder.is_dir())
        contents=outside.read_text()
        self.assertEqual(contents,'outside')
        self.assertFalse((directory/'scalar-v1'/(self.target+'.json')).exists())

    def test_clear_does_not_follow_legacy_namespace_directory_links(self):
        directory=self.root/'linked';directory.mkdir()
        outside=self.root/'outside-records';outside.mkdir()
        record=outside/(self.key+'.json');record.write_text('preserve')
        (directory/'eval-v1').symlink_to(outside,target_is_directory=True)
        self.run_native([{'op':'clear'}],directory)
        self.assertEqual(record.read_text(),'preserve')
        self.assertTrue((directory/'eval-v1').is_symlink())


if __name__=='__main__':
    unittest.main()
