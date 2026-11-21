"""Native flags and file tags compared directly with installed Odin verdicts."""
from tests.test_rust_compiler import compiler_engines
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import unittest

from tests import test_rust_compiler as native
from thorlang import cli


NATIVE_FLAGS = {flag['name'] for flag in cli.CATALOG['native_flags'] if flag['name'].startswith('vet')}
NATIVE_TAGS = {name.removeprefix('vet-') for name in NATIVE_FLAGS-{'vet','vet-packages'}} | {'deprecated','explicit-allocators'}
CLEAN = 'package main\nmain :: proc() {}\n'
FEATURES = '''package main
import "core:fmt"
import _ "thor:basic" / zip
increment :: fn(x: int) -> int { return x+1 }
ANSWER :: #run increment(2)
main :: proc() {
	numbers := [2]int{1, 2}
	words := [2]string{"one", "two"}
	values := zip(number=numbers[:], word=words[:])
	for &row, i in values {
		if row.number != i+1 || row.word != words[i] { panic("zip") }
		row.number += 1
	}
	text := f"{switch {case numbers[0]>0: increment(numbers[0]); case: 0}}"
	defer delete(text)
	odd :> []int{x**2 for x in numbers if x>0}
	defer delete(odd)
	if odd[0]!=4 || odd[-1]!=9 { panic("comprehension") }
	for i: f64 in 0..<2 { if i<0 { panic("typed loop") } }
	big_value: Integer = 123456789012345678901234567890
	defer delete(big_value)
	big_value += 2
	if big_value != Integer(123456789012345678901234567892) { panic("big value") }
	if ANSWER != 3 { panic("compile-time helper") }
	fmt.println(text)
}
'''
VIOLATIONS = {
    'vet': ('package main\nimport "core:fmt"\nmain::proc(){x:=1;fmt.println(cast(int)x)}\n', 'cast'),
    'vet-cast': ('package main\nimport "core:fmt"\nmain::proc(){x:=1;fmt.println(cast(int)x)}\n', 'cast'),
    'vet-shadowing': ('package main\nimport "core:fmt"\nmain::proc(){x:=1;{x:=2;fmt.println(x)};fmt.println(x)}\n', 'shadows'),
    'vet-when-shadowing': ('package main\nwhen true {len::3}\nmain::proc(){}\n', 'shadows'),
    'vet-semicolon': ('package main\nmain::proc(){\n\tx:=1;\n\t_=x\n}\n', 'semicolon'),
    'vet-unused': ('package main\nmain::proc(){x:=1}\n', 'not used'),
    'vet-unused-variables': ('package main\nmain::proc(){x:=1}\n', 'not used'),
    'vet-unused-procedures': ('package main\ndead::proc(){}\nmain::proc(){}\n', 'not used'),
    'vet-unused-imports': ('package main\nimport "core:fmt"\nmain::proc(){}\n', 'not used'),
    'vet-style': ('package main\nRow::struct{\n\tx:int\n}\nmain::proc(){}\n', 'comma'),
    'vet-tabs': ('package main\nmain::proc(){\n    _=0\n}\n', 'tabs'),
    'strict-style': ('package main\nRow::struct{\n\tx:int\n}\nmain::proc(){}\n', 'comma'),
    'vet-nil-deref': ('package main\nimport "core:fmt"\nmain::proc(){p:^int;fmt.println(p^)}\n', 'nil'),
    'vet-uninitialized': ('package main\nimport "core:fmt"\nmain::proc(){x:int=---;fmt.println(x)}\n', 'before'),
    'vet-atomic-access': ('package main\nimport "base:intrinsics"\nimport "core:fmt"\nx:int\nmain::proc(){_=intrinsics.atomic_add(&x,1);fmt.println(x)}\n', 'atomically'),
    'vet-using-param': ('package main\nRow::struct{x:int}\nread::proc(using row:Row)->int{return x}\nmain::proc(){_=read(Row{1})}\n', 'using'),
    'vet-using-stmt': ('package main\nRow::struct{x:int}\nmain::proc(){row:=Row{1};using row;_=x}\n', 'using'),
}


class NativeVetTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp = native.RustCompilerTests.setUp

    def call(self, engine, *flags, command='check', entry=None, status=0):
        compiler = native.PYTHON if engine == 'python' else native.RUST
        result = subprocess.run([str(compiler), command, str(entry or self.app),
                                 '--output-dir', str(self.root/(engine+'-output')), '-thread-count:1', *flags],
                                cwd=self.root, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, status, result.stdout+result.stderr)
        return result

    def oracle(self, raw, flags=(), command='check'):
        source=self.root/'original.odin'
        source.write_text(raw)
        result=subprocess.run([os.environ.get('THOR_ODIN','odin'),command,str(source),'-file',
                               '-thread-count:1','-json-errors',*flags],
                              cwd=self.root,capture_output=True,text=True,timeout=60)
        self.assertIn(result.returncode,(0,1),result.stdout+result.stderr)
        return result

    def compare_native(self,raw,flags=(),command='check'):
        oracle=self.oracle(raw,flags,command)
        try:expected=json.loads(oracle.stderr or '{"errors":[]}')['errors']
        except ValueError:expected=[{'type':'error','pos':None,'msgs':[oracle.stderr.strip()]}]
        self.source.write_text(raw)
        for engine in compiler_engines():
            with self.subTest(engine=engine):
                result=self.call(engine,*flags,'-json-errors',command=command,status=oracle.returncode)
                actual=json.loads(result.stderr or '{"errors":[]}')['errors']
                # Ordinary Thor unused-local warnings remain warnings. Compare
                # the native policy errors, including complete source ranges.
                actual=[d for d in actual if d['type']=='error']
                errors=[d for d in expected if d['type']=='error']
                expected_messages=[[m.replace(str(self.root/'original.odin'),str(self.source)) for m in d['msgs']] for d in errors]
                self.assertEqual([d['msgs'] for d in actual],expected_messages,actual)
                for found,native_error in zip(actual,errors):
                    if not native_error.get('pos'):
                        self.assertIsNone(found.get('pos'))
                        continue
                    self.assertEqual(found['pos']['file'],str(self.source))
                    for key in ('offset','line','column'):
                        self.assertEqual(found['pos'][key],native_error['pos'][key],(found,native_error))
        return oracle

    def test_catalog_covers_every_installed_native_vet_flag(self):
        result=subprocess.run([os.environ.get('THOR_ODIN','odin'),'build','-help'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        installed=set(re.findall(r'^\s+-(vet(?:-[a-z-]+)?)(?=[:\s]|$)',result.stdout,re.M))
        self.assertTrue(installed)
        self.assertLessEqual(installed,NATIVE_FLAGS)
        self.assertEqual(set(VIOLATIONS),NATIVE_FLAGS-{'vet-packages'} | {'strict-style'})

    def test_all_native_flags_accept_clean_thor(self):
        for name in sorted(NATIVE_FLAGS | {'strict-style'}):
            with self.subTest(flag=name):
                flags=['-vet-packages:main'] if name=='vet-packages' else ['-'+name,'-vet-packages:main']
                self.compare_native(CLEAN,flags)

    def test_all_native_flag_errors_match_odin_source_locations(self):
        for name,(raw,_) in VIOLATIONS.items():
            with self.subTest(flag=name):
                result=self.compare_native('// 😀 positions\n'+raw,['-'+name,'-vet-packages:main'])
                self.assertEqual(result.returncode,1)

    def test_installed_file_policy_inventory_is_complete(self):
        root=Path(subprocess.check_output([os.environ.get('THOR_ODIN','odin'),'root'],text=True).strip())
        settings=root/'src/build_settings.cpp'
        if not settings.is_file():self.skipTest('SDK does not include compiler source')
        raw=settings.read_text().split('u64 get_vet_flag_from_name',1)[1].split('return VetFlag_NONE;',1)[0]
        self.assertEqual(set(re.findall(r'name == "([a-z-]+)"',raw)),NATIVE_TAGS)

    def test_incompatible_native_analysis_flags_keep_option_errors(self):
        for flags in [('-vet-atomic-access','-no-atomic-analysis'),
                      ('-vet-nil-deref','-no-escape-analysis'),
                      ('-vet-uninitialized','-no-escape-analysis')]:
            with self.subTest(flags=flags):self.compare_native(CLEAN,flags)

    def test_native_only_policy_defaults_match_odin(self):
        self.source.unlink(missing_ok=True)
        source=self.app/'main.odin'
        raw='package main\ndead::proc(){}\nmain::proc(){}\n'
        source.write_text(raw)
        for flags in [('-vet-unused-procedures',),('-vet-unused-procedures','-vet-packages:main')]:
            expected=self.oracle(raw,flags)
            for engine in compiler_engines():
                result=self.call(engine,*flags,'-json-errors',status=expected.returncode)
                self.assertIn('unused-procedures' if len(flags)==1 else 'not used',result.stderr)

    def test_native_policies_across_commands_and_artifact_modes(self):
        self.source.write_text('#+vet cast unused\n'+CLEAN)
        for engine in compiler_engines():
            for command in ('check','build','run','vet','doc'):
                with self.subTest(engine=engine,command=command):
                    self.call(engine,'-vet-cast',command=command)
            for flags in [native.UNCHECKED, ['-debug'],['-build-mode:obj','-use-single-module']]:
                with self.subTest(engine=engine,flags=flags):
                    output=self.root/(engine+'artifact')
                    self.call(engine,'-vet','-vet-packages:main','-out:'+str(output),*flags,command='build')
                    self.assertTrue(output.is_file())
            self.source.write_text('#+test\n#+vet unused cast\npackage main\nimport "core:testing"\n@(test)\nworks::proc(t:^testing.T){testing.expect(t,2+2==4)}\n')
            self.call(engine,'-vet','-vet-packages:main',*native.UNCHECKED,command='test')
            self.source.write_text('#+vet cast unused\n'+CLEAN)

    def test_every_native_tag_and_negation_is_accepted(self):
        for name in sorted(NATIVE_TAGS):
            for prefix in ('','!'):
                with self.subTest(tag=prefix+name):
                    self.compare_native('#+vet '+prefix+name+'\n'+CLEAN)
        self.compare_native('#+vet\n'+CLEAN)
        self.compare_native('#+vet '+' '.join(sorted(NATIVE_TAGS))+'\n'+CLEAN)
        self.compare_native('\ufeff#+vet unused cast\r\n'+CLEAN.replace('\n','\r\n'))

    def test_native_file_tag_errors_and_package_filter_overrides(self):
        for flag,(raw,_) in VIOLATIONS.items():
            if flag=='strict-style':continue
            tag='#+vet'+(' '+flag[4:] if flag!='vet' else '')
            with self.subTest(tag=tag):
                result=self.compare_native(tag+'\n'+raw,['-vet-packages:other'])
                self.assertEqual(result.returncode,1)
        self.compare_native('#+vet deprecated\npackage main\n@(deprecated="old")\nold::proc(){}\nmain::proc(){old()}\n')
        self.compare_native('#+vet explicit-allocators\npackage main\nmain::proc(){x:=make([]int,1);delete(x)}\n')

    def test_native_using_feature_optins_and_optouts_keep_precedence(self):
        raw='package main\nRow::struct{x:int}\nmain::proc(){row:=Row{1};using row;_=x}\n'
        for feature in ('using-stmt','!using-stmt'):
            with self.subTest(feature=feature):
                self.compare_native('#+vet using-stmt\n#+feature '+feature+'\n'+raw)
        params='package main\nRow::struct{x:int}\nread::proc(using row:Row)->int{return x}\nmain::proc(){_=read(Row{1})}\n'
        self.compare_native('#+vet using-param\n#+feature using-stmt\n'+params)

    def test_repeated_tags_override_in_order_and_keep_native_defaults(self):
        raw='package main\nmain::proc(){if true {x:=1}}\n'
        for tags in ('#+vet\n#+vet !unused\n','#+vet unused-variables\n#+vet !unused-variables\n',
                     '#+vet !unused-variables\n#+vet unused-variables\n',
                     '#+vet !unused-variables\n'):
            with self.subTest(tags=tags):self.compare_native(tags+raw,['-vet-unused-variables'])
        # Odin requires an explicit package scope for unused-procedures CLI.
        self.compare_native('package main\ndead::proc(){}\nmain::proc(){}\n',['-vet-unused-procedures'])

    def test_native_tag_syntax_and_unknown_names_are_reported_by_odin(self):
        for tag in ('#+vet no-such-policy','#+vet !','#+vet unused, cast','#+vet extra'):
            with self.subTest(tag=tag):self.compare_native(tag+'\n'+CLEAN)
        self.compare_native(CLEAN,['-vet-future-unaudited'])

    def test_mixed_native_sources_dependencies_exclusion_and_overlays(self):
        self.source.write_text(CLEAN)
        excluded=self.app/('ignored_linux.thor' if os.name=='nt' else 'ignored_windows.thor')
        excluded.write_text('#+vet unused\nnot valid source\n')
        (self.app/'other.odin').write_text('package main\nVALUE::1\n')
        for engine in compiler_engines():self.call(engine,'-vet')
        library=self.app/'library';library.mkdir()
        (library/'value.thor').write_text('#+vet unused\npackage library\nVALUE::1\n')
        self.source.write_text('package main\nimport "library"\nmain::proc(){_=library.VALUE}\n')
        for engine in compiler_engines():self.call(engine,'-vet','-vet-packages:main')
        replacement=self.root/'replacement.thor';replacement.write_text('#+vet unused\npackage main\nmain::proc(){x:=1}\n')
        overlay=self.root/'overlay.json';overlay.write_text(json.dumps({'Replace':{str(self.source):str(replacement)}}))
        for engine in compiler_engines():
            errors=json.loads(self.call(engine,'--overlay',overlay,'-json-errors',status=1).stderr)['errors']
            self.assertTrue(any("'x' declared but not used" in ' '.join(e['msgs']) for e in errors),errors)
            self.assertTrue(all(e['pos']['file']==str(self.source) for e in errors),errors)

    def test_native_default_scope_includes_external_collection_packages(self):
        library=self.root/'external';library.mkdir()
        source=library/'value.thor'
        source.write_text('package library\nvalue::proc()->int{\n x:=2;\n return x\n}\n')
        self.source.write_text('package main\nimport external "external:."\nmain::proc(){_=external.value()}\n')
        collection='-collection:external='+str(library)
        native_library=source.with_suffix('.odin');native_library.write_text(source.read_text())
        try:
            expected=self.oracle(self.source.read_text(),['-vet-semicolon',collection])
            self.assertEqual(expected.returncode,1,expected.stderr)
            scoped=self.oracle(self.source.read_text(),['-vet-semicolon','-vet-packages:main',collection])
            self.assertEqual(scoped.returncode,0,scoped.stderr)
        finally:native_library.unlink()
        for engine in compiler_engines():
            result=self.call(engine,'-vet-semicolon',collection,'-json-errors',status=1)
            errors=json.loads(result.stderr)['errors']
            self.assertTrue(any(e.get('pos',{}).get('file')==str(source) and 'semicolon' in ' '.join(e['msgs']).lower() for e in errors),errors)
            self.call(engine,'-vet-semicolon','-vet-packages:main',collection)

    def test_comments_unicode_crlf_and_file_tag_positions(self):
        for name in ('unused','explicit-allocators'):
            raw='// 😀 Documentation: #+vet unused\r\n/*\r\n#+vet shadowing\r\n*/\r\n#+vet '+name+'\r\n'
            self.compare_native(raw+'package main\r\nmain::proc(){x:=make([]int,1);delete(x)}\r\n')

    def test_simple_thor_extensions_use_native_source_view(self):
        raw='#+vet unused-variables cast\npackage main\nimport "core:fmt"\nwork::fn(x:int)->int{return x+1}\nmain::proc(){\n values :> []int{1,2}\n fmt.println(work(values[0]))\n}\n'
        self.source.write_text(raw)
        for engine in compiler_engines():
            for config in ([],native.UNCHECKED):
                result=self.call(engine,*config,command='run')
                self.assertEqual(result.stdout,'2\n')
                self.assertNotIn('__thor_',result.stderr)

    def test_supported_checks_work_with_generated_helpers_checked_and_unchecked(self):
        self.source.write_text(FEATURES)
        for engine in compiler_engines():
            for config in ([],native.UNCHECKED):
                result=self.call(engine,'-vet-semicolon','-vet-when-shadowing',*config,command='run')
                self.assertEqual(result.stdout,'3\n')
                self.assertEqual(result.stderr,'')

    def test_thor_expressions_preserve_native_policy_errors(self):
        for name,body in {
            'vet-cast':'x:=1;text:=f"{cast(int)x}";defer delete(text);fmt.println(text)',
            'vet-nil-deref':'p:^int;text:=f"{p^}";defer delete(text);fmt.println(text)',
            'vet-shadowing':'x:=1;values:=[x for x in 0..<3];defer delete(values);fmt.println(x,values)',
        }.items():
            self.source.write_text('package main\nimport "core:fmt"\nmain::proc(){'+body+'}\n')
            for engine in compiler_engines():
                error=self.call(engine,'-'+name,'-json-errors',status=1)
                self.assertIn({'vet-cast':'Unneeded cast','vet-nil-deref':'always nil','vet-shadowing':'shadows'}[name],error.stderr)

    def test_no_alloc_composes_with_native_policies(self):
        self.source.write_text('#+vet no_alloc unused cast\npackage main\nmain::proc(){x:=1;_=x}\n')
        for engine in compiler_engines():self.call(engine)
        self.source.write_text('#+vet no_alloc, unused, cast\npackage main\nmain::proc(){x:=1}\n')
        for engine in compiler_engines():
            self.assertIn("'x' declared but not used",self.call(engine,status=1).stderr)
        self.source.write_text('#+vet no_alloc unused\npackage main\nmain::proc(){x:=make([]int,1);delete(x)}\n')
        for engine in compiler_engines():self.assertIn('no_alloc:',self.call(engine,status=1).stderr)

    def test_unrelated_native_flags_do_not_activate_vet_projection(self):
        self.source.write_text('package main\nmain::proc(){values:=[1,2];if values[0]!=1{panic("bad")}}\n')
        for engine in compiler_engines():
            for flag in ('-disallow-do','-disable-non-constant-globals'):
                with self.subTest(engine=engine,flag=flag):self.call(engine,flag)

    def test_ordinary_builds_and_custom_contracts_keep_fast_path(self):
        for header in ('','#+vet no_alloc\n'):
            self.source.write_text(header+CLEAN)
            profile=self.root/'profile.json'
            result=subprocess.run([str(native.RUST),'check',str(self.app),'--output-dir',str(self.root/'profile-output')],
                                  env={**os.environ,'THOR_PROFILE':str(profile)},capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertNotIn('backend.native_source_checks',json.loads(profile.read_text()).get('stages',{}))

    def test_full_help_lists_native_flags_without_obsolete_restrictions(self):
        for engine in compiler_engines():
            compiler=native.PYTHON if engine=='python' else native.RUST
            result=subprocess.run([str(compiler),'help','build','-full'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertNotIn('Odin-only',result.stdout)
            for name in NATIVE_FLAGS:self.assertIn('-'+name,result.stdout)


if __name__=='__main__':unittest.main()
