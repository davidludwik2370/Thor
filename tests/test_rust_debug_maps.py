"""The real editor map consumer accepts native debug outputs and helper metadata."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from tests import test_rust_compiler as native


class RustDebugMapTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp = native.RustCompilerTests.setUp

    def build(self, source, command='build'):
        self.source.write_text(source)
        binary = self.root/'program'
        flags=['-o:none','-out:'+str(binary)] if command=='build' else []
        result = subprocess.run([str(native.RUST), command, str(self.source), '-debug', *flags,
            '--track-allocations=off', '--no-sanitize', '--output-dir', str(self.root/'generated')],
            cwd=self.root, capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        manifest=self.root/'generated/thor-manifest.json'
        return manifest, json.loads(manifest.read_bytes()), binary

    @unittest.skipUnless(shutil.which('node'), 'Node is required for the real map consumer')
    def inspect(self, manifest, binary, line):
        script='''const {DebugMaps}=require(process.argv[1]);
const maps=new DebugMaps(process.argv[2]);maps.verifyExecutable(process.argv[3]);
const candidates=maps.candidates(process.argv[4],Number(process.argv[5]));
const regions=[];for(const unit of maps.files.values())for(const row of unit.map.regions){
 const text=unit.bytes.subarray(row[0],row[1]).toString();
 const offset=row[0]+Buffer.byteLength(text.slice(0,text.search(/\S/)));
 const before=unit.bytes.subarray(0,offset).toString();
 const line=before.split('\\n').length,column=[...before.split('\\n').at(-1)].length+1;
 const location=maps.location(unit.file,line,column);
 regions.push({kind:row[2],text,location,condition:maps.condition(unit.file,line,'values[0] == 7')});
}console.log(JSON.stringify({candidates,regions}));'''
        result=subprocess.run(['node','-e',script,str(native.ROOT/'editors/vscode/debug-adapter.js'),
            str(manifest),str(binary),str(self.source),str(line)],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        return json.loads(result.stdout)

    def test_debug_program_identity_roots_and_invalidation(self):
        source='package main\nmain::proc(){value:=7;if value!=7{panic("value")}}\n'
        manifest,data,binary=self.build(source)
        self.assertEqual(data['debug_program'],{'path':str(binary),'sha256':hashlib.sha256(binary.read_bytes()).hexdigest()})
        self.assertEqual({Path(path).name for path in data['debug_roots']},{'runtime','base','core','vendor'})
        if shutil.which('node'):
            info=self.inspect(manifest,binary,2)
            self.assertTrue(info['candidates'])
        _,checked,_=self.build(source.replace('value:=7','value:=8').replace('value!=7','value!=8'),'check')
        self.assertNotIn('debug_program',checked)
        self.assertTrue(binary.exists())

    @unittest.skipUnless(shutil.which('node'), 'Node is required for the real map consumer')
    def test_helpers_captures_and_checked_variants_keep_source_identity(self):
        source='''package main
identity::proc(value:int)->int{return value+1}
checked::fn(value:int)->int{return identity(value)}
main::proc(){
    values:=[7,8]
    index:=-1
    selected:=switch index {
        case -1:identity(values[index])
        case:0
    }
    result:=checked(selected)
    if result!=10{panic("result")}
}
'''
        manifest,data,binary=self.build(source)
        info=self.inspect(manifest,binary,7)
        self.assertTrue(info['candidates'])
        self.assertTrue(all(not entry['helper'] for entry in info['candidates']))
        kinds={r['kind'] for r in info['regions']}
        self.assertTrue({'expression-helper','debug-capture','pure-procedure'}<=kinds,info)
        helper=next(r for r in info['regions'] if r['kind']=='expression-helper' and 'capture_' in r['text'])
        self.assertTrue(helper['location']['helper'])
        self.assertTrue(helper['location']['internal'])
        self.assertIn('capture_',helper['condition'])
        self.assertNotIn('values[0]',helper['condition'])
        checked=next(r for r in info['regions'] if r['kind']=='pure-procedure')
        self.assertEqual(checked['location']['procedure'],'identity')
        self.assertIn('checked_',checked['location']['checkedVariant'])

    @unittest.skipUnless(shutil.which('node'), 'Node is required for the real map consumer')
    def test_shadowed_type_query_is_a_real_user_call_in_debug_maps(self):
        source='''package main
import native "base:builtin"
identity::proc(value:int)->int{return value}
type_of::proc(value:int)->int{return value+1}
main::proc(){
    values:=[4,5,6]
    i:=-1
    result:=type_of(identity(values[i]))
    native.assert(result==7)
}
'''
        manifest,data,binary=self.build(source)
        info=self.inspect(manifest,binary,8)
        self.assertTrue(info['candidates'])
        self.assertTrue(any(r['kind']=='source-call-target' and r['text']=='type_of' for r in info['regions']))
