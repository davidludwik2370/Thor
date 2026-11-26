"""Dependency snapshots, resources and invalidation against the Python model."""
from tests.native_paths import directory_alias, remove_directory_alias, dangling_directory_alias, file_symlink
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from thorlang.compiler import Source
from thorlang.dependencies import Inputs, file_digest, signature
from thorlang.driver import frontend_binary
from tests.native_paths import rust_binary
from tests import test_rust_compiler as native


class RustEvaluationInputTests(unittest.TestCase):
    maxDiff=None
    @classmethod
    def setUpClass(cls):
        result=subprocess.run(['cargo','build','--release','--locked','--offline','--manifest-path',
            str(native.ROOT/'rust/Cargo.toml'),'--example','inputs_protocol'],capture_output=True,text=True,timeout=600)
        if result.returncode:raise RuntimeError(result.stdout+result.stderr)
        cls.binary=rust_binary('inputs_protocol', example=True)
        cls.frontend=frontend_binary('odin')[0]

    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='thor-native-dependencies-')
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve()

    def run_native(self,operations,data=None,collections=None):
        request=dict(operations=operations,frontend=str(self.frontend),collections=collections or {})
        if data is not None:request['data']=data
        result=subprocess.run([str(self.binary)],input=json.dumps(request)+'\n',capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        return json.loads(result.stdout)

    def run_reference(self,operations,data=None,collections=None):
        # Windows uses a versioned native file-ID signature, deliberately unlike
        # the archived Unix stat tuple. Borrow only that platform representation
        # while retaining independent reference dependency/invalidation logic.
        if os.name=='nt':
            with patch('thorlang.dependencies.signature',side_effect=self.snapshot_signature):
                return self.reference_operations(operations,data,collections)
        return self.reference_operations(operations,data,collections)

    def snapshot_signature(self,path):
        if os.name=='nt':return self.run_native([{'op':'signature','path':str(path)}])['answers'][0]
        return signature(path)

    def reference_operations(self,operations,data=None,collections=None):
        inputs=Inputs(json.loads(json.dumps(data)) if data is not None else None)
        answers=[]
        for operation in operations:
            op=operation['op'];path=Path(operation.get('path','.'));answer=None
            if op=='file':inputs.file(path,operation.get('raw',None).encode() if operation.get('raw') is not None else None,content=operation.get('content',True))
            elif op=='binding':inputs.binding(path)
            elif op=='tree':inputs.tree(path)
            elif op=='package':inputs.package(path,[Path(name) for name in operation['paths']],tuple(operation.get('suffixes',['.odin','.thor'])))
            elif op=='directory':inputs.data['directories'][str(path)]=self.snapshot_signature(path)
            elif op=='source':
                raw=operation.get('raw')
                source=Source(path,self.frontend,raw=raw.encode() if raw is not None else None,cache=False)
                source.collections={name:Path(path) for name,path in (collections or {}).items()}
                inputs.source(source,source_file=operation.get('source_file',True))
            elif op=='signature':answer=self.snapshot_signature(path)
            elif op=='resolve':answer=str(path.resolve())
            elif op=='digest':answer=file_digest(path)
            elif op=='valid':answer=inputs.valid()
            elif op=='read':answer=path.read_text()
            else:raise ValueError(op)
            answers.append(answer)
        return dict(answers=answers,data=inputs.data,usable=inputs.usable,valid=inputs.valid())

    def compare(self,operations,data=None,collections=None):
        expected=self.run_reference(operations,data,collections)
        actual=self.run_native(operations,data,collections)
        self.assertEqual(actual,expected)
        return actual

    def test_file_signatures_digests_missing_paths_and_raw_bytes(self):
        path=self.root/'héllo.txt';path.write_text('contents 😀')
        self.compare([{'op':'signature','path':str(path)}, {'op':'digest','path':str(path)},
            {'op':'file','path':str(path),'raw':'contents 😀'},
            {'op':'file','path':str(self.root/'missing')}])

    def test_raw_overlay_mismatch_disables_reuse(self):
        path=self.root/'data';path.write_text('old')
        answer=self.compare([{'op':'file','path':str(path),'raw':'new'}])
        self.assertFalse(answer['valid'])

    def test_identical_content_rewrite_keeps_content_snapshot_valid(self):
        path=self.root/'data';path.write_text('same')
        packet=self.compare([{'op':'file','path':str(path)}])
        path.write_text('same')
        answer=self.compare([],packet['data'])
        self.assertTrue(answer['valid'])

    def test_metadata_only_snapshot_observes_changed_signature(self):
        path=self.root/'data';path.write_text('same')
        packet=self.compare([{'op':'file','path':str(path),'content':False}])
        path.write_text('longer')
        answer=self.compare([],packet['data'])
        self.assertFalse(answer['valid'])

    def test_content_change_and_removal_invalidate(self):
        path=self.root/'data';path.write_text('before')
        packet=self.compare([{'op':'file','path':str(path)}])
        path.write_text('after')
        changed=self.compare([],packet['data'])
        self.assertFalse(changed['valid'])
        path.unlink()
        removed=self.compare([],packet['data'])
        self.assertFalse(removed['valid'])

    def test_missing_input_becoming_present_invalidates(self):
        path=self.root/'missing'
        packet=self.compare([{'op':'file','path':str(path)}])
        path.write_text('appeared')
        answer=self.compare([],packet['data'])
        self.assertFalse(answer['valid'])

    def test_repeated_observation_detects_stale_supplied_snapshot(self):
        path=self.root/'data';path.write_text('before')
        packet=self.compare([{'op':'file','path':str(path)}])
        path.write_text('after')
        answer=self.compare([{'op':'file','path':str(path)}],packet['data'])
        self.assertFalse(answer['usable'])

    def test_package_membership_includes_only_requested_regular_suffixes(self):
        for name in ['a.odin','b.thor','ignore.txt']:(self.root/name).write_text(name)
        (self.root/'directory.odin').mkdir()
        paths=[str(self.root/'a.odin'),str(self.root/'b.thor')]
        packet=self.compare([{'op':'package','path':str(self.root),'paths':paths}])
        (self.root/'c.thor').write_text('package extra')
        answer=self.compare([],packet['data'])
        self.assertFalse(answer['valid'])

    def test_symlink_bindings_missing_tails_and_parent_components(self):
        target=self.root/'target';target.mkdir();(target/'child').mkdir()
        link=self.root/'link';directory_alias(link,target/'child')
        broken=self.root/'broken';dangling_directory_alias(broken,self.root/'not-yet')
        paths=[link/'missing/leaf',link/'../sibling',broken/'tail']
        operations=[{'op':op,'path':str(path)} for path in paths for op in ['binding','resolve']]
        self.compare(operations)

    def test_retargeted_symlink_invalidates_binding(self):
        first=self.root/'first';second=self.root/'second';first.mkdir();second.mkdir()
        link=self.root/'link';directory_alias(link,first)
        packet=self.compare([{'op':'binding','path':str(link/'missing')}])
        remove_directory_alias(link);directory_alias(link,second)
        answer=self.compare([],packet['data'])
        self.assertFalse(answer['valid'])

    def test_cyclic_symlink_binding_disables_optional_reuse(self):
        first=self.root/'first';second=self.root/'second'
        file_symlink(first,second);file_symlink(second,first)
        answer=self.run_native([{'op':'binding','path':str(first)}])
        self.assertFalse(answer['usable'])
        self.assertFalse(answer['valid'])

    def test_directory_and_tree_watch_supported_files_and_skip_generated_folders(self):
        for name in ['a.odin','b.py','ignore.txt']:(self.root/name).write_text(name)
        child=self.root/'nested';child.mkdir();(child/'c.odin').write_text('child')
        for name in ['.git','__pycache__','.thor-cache','.thor-build']:
            directory=self.root/name;directory.mkdir();(directory/'ignored.odin').write_text('ignored')
        directory_alias(self.root/'link.odin',child)
        packet=self.compare([{'op':'tree','path':str(self.root)}])
        (child/'c.odin').write_text('changed')
        answer=self.compare([],packet['data'])
        self.assertFalse(answer['valid'])

    def test_tree_rejects_content_and_child_directory_changes_with_equal_metadata(self):
        path=self.root/'module.odin';path.write_text('before')
        packet=self.compare([{'op':'tree','path':str(self.root)}])
        path.write_text('change')
        packet['data']['files'][str(path)]['stat']=self.snapshot_signature(path)
        self.assertFalse(self.run_native([],packet['data'])['valid'])
        self.assertFalse(self.run_reference([],packet['data'])['valid'])
        packet=self.compare([{'op':'tree','path':str(self.root)}])
        child=self.root/'new_package';child.mkdir()
        (child/'new.odin').write_text('package example')
        packet['data']['directories'][str(self.root)]['stat']=self.snapshot_signature(self.root)
        self.assertFalse(self.run_native([],packet['data'])['valid'])
        self.assertFalse(self.run_reference([],packet['data'])['valid'])

    def source(self,expression):
        path=self.root/'main.thor'
        path.write_text('package main\nDATA::'+expression+'\nmain::proc(){}\n')
        return path

    def test_literal_resource_and_missing_exists_inputs(self):
        resource=self.root/'text';resource.write_text('resource')
        path=self.source('#load("text")')
        packet=self.compare([{'op':'source','path':str(path)}])
        resource.write_text('changed resource')
        answer=self.compare([],packet['data'])
        self.assertFalse(answer['valid'])
        path=self.source('#exists("missing")')
        self.compare([{'op':'source','path':str(path)}])

    def test_collection_resource_tracks_actual_contents(self):
        assets=self.root/'assets';assets.mkdir();(assets/'data').write_text('data')
        path=self.source('#load("assets:data")')
        self.compare([{'op':'source','path':str(path)}],collections={'assets':str(assets)})

    def test_load_directory_tracks_immediate_files_only(self):
        assets=self.root/'assets';assets.mkdir();(assets/'one').write_text('one')
        child=assets/'nested';child.mkdir();(child/'ignored').write_text('ignored')
        path=self.source('#load_directory("assets")')
        packet=self.compare([{'op':'source','path':str(path)}])
        (child/'ignored').write_text('changed, still nested')
        nested=self.compare([],packet['data'])
        self.assertTrue(nested['valid'])
        (assets/'two').write_text('new immediate file')
        added=self.compare([],packet['data'])
        self.assertFalse(added['valid'])

    def test_dynamic_resource_path_and_unknown_collection_disable_reuse(self):
        for expression in ['#load(NAME)','#load("unknown:asset")']:
            with self.subTest(expression=expression):
                path=self.source(expression)
                packet=self.compare([{'op':'source','path':str(path)}])
                self.assertFalse(packet['usable'])

    def test_constant_resource_snapshots_watch_all_paths_and_symlink_bindings(self):
        first=self.root/'first';first.write_text('first')
        second=self.root/'second';second.write_text('second')
        link=self.root/'link';file_symlink(link,first)
        path=self.root/'main.thor'
        path.write_text('package main\nNAME::("link" when ODIN_OS==.Darwin else "missing")\n'
                        'ALIAS::NAME\nDATA::#exists(ALIAS)\nmain::proc(){}\n')
        operations=[{'op':'source','path':str(path)}]
        packet=self.run_native(operations)
        self.assertTrue(packet['usable'])
        self.assertTrue(packet['valid'])
        self.assertIn(str(first),packet['data']['files'])
        self.assertIn(str(self.root/'missing'),packet['data']['files'])
        (self.root/'missing').write_text('appeared')
        changed=self.run_native([],packet['data'])
        self.assertFalse(changed['valid'])
        packet=self.run_native(operations)
        link.unlink();file_symlink(link,second)
        changed=self.run_native([],packet['data'])
        self.assertFalse(changed['valid'])
        packet=self.run_native(operations)
        second.write_text('changed content')
        changed=self.run_native([],packet['data'])
        self.assertFalse(changed['valid'])

    def test_source_overlay_and_skipping_source_content(self):
        path=self.source('#exists("missing")')
        raw=path.read_text()+'// unsaved edit\n'
        packet=self.compare([{'op':'source','path':str(path),'raw':raw}])
        self.assertFalse(packet['usable'])
        packet=self.compare([{'op':'source','path':str(path),'raw':raw,'source_file':False}])
        self.assertTrue(packet['usable'])

    def test_malformed_snapshot_cannot_validate(self):
        empty=dict(files={},directories={},members={},bindings={})
        for data in [{},[],False,{**empty,'files':[]},{**empty,'directories':[]},
                     {**empty,'members':None},{**empty,'bindings':[]}]:
            with self.subTest(data=data):
                packet=self.compare([],data)
                self.assertFalse(packet['valid'])

    def test_invalid_content_digest_cannot_use_unchanged_metadata(self):
        path=self.root/'data';path.write_text('contents')
        packet=self.run_native([{'op':'file','path':str(path)}])
        for digest in ['bad','A'*64,42,{},'g'*64]:
            with self.subTest(digest=digest):
                data=json.loads(json.dumps(packet['data']))
                data['files'][str(path)]['sha256']=digest
                changed=self.run_native([],data)
                self.assertFalse(changed['valid'])

    def test_restoring_mtime_does_not_hide_changed_content(self):
        import os
        path=self.root/'data';path.write_text('before')
        packet=self.run_native([{'op':'file','path':str(path)}])
        metadata=path.stat()
        path.write_text('change')
        os.utime(path,ns=(metadata.st_atime_ns,metadata.st_mtime_ns))
        changed=self.run_native([],packet['data'])
        self.assertFalse(changed['valid'])

    def test_content_change_invalidates_even_if_all_metadata_matches(self):
        path=self.root/'data';path.write_text('before')
        packet=self.run_native([{'op':'file','path':str(path)}])
        path.write_text('change')
        packet['data']['files'][str(path)]['stat']=signature(path)
        self.assertFalse(self.run_native([],packet['data'])['valid'])

    def test_directory_resources_track_extensionless_additions_with_equal_metadata(self):
        directory=self.root/'assets';directory.mkdir()
        (directory/'one').write_text('first')
        path=self.source('#load_directory("assets")')
        packet=self.compare([{'op':'source','path':str(path)}])
        (directory/'two').write_text('second')
        packet['data']['directories'][str(directory)]=signature(directory)
        self.assertFalse(self.run_native([],packet['data'])['valid'])
        self.assertFalse(self.run_reference([],packet['data'])['valid'])


if __name__=='__main__':unittest.main()
