"""Independent native publishing: policy parity, real relocation and diagnostics."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from tests import test_rust_compiler as native
from tests import test_publish as reference
from tests import rust_publish_cases as cases
from tests.native_paths import rust_binary

ROOT=native.ROOT
PROTOCOL=rust_binary('publish_protocol',example=True)

class RustPublishProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if native.using_prebuilt_compiler(PROTOCOL):return
        result=subprocess.run(['cargo','build','--release','--manifest-path',str(ROOT/'rust/Cargo.toml'),
            '--locked','--offline','--example','publish_protocol','-j','2'],capture_output=True,text=True,timeout=600)
        if result.returncode:raise RuntimeError(result.stdout+result.stderr)

    def test_icon_pixels_crop_warnings_and_platform_containers(self):
        count=cases.verify_icons(PROTOCOL)
        self.assertEqual(count,12)

    def test_configuration_resources_names_and_native_flags(self):
        count=cases.verify_policy(PROTOCOL)
        self.assertEqual(count,36)

    def test_pe_dll_and_controlled_linux_dependency_closure(self):
        count=cases.verify_dependencies(PROTOCOL)
        self.assertEqual(count,18)

    def test_policy_and_icon_conversion_do_not_require_python_or_odin(self):
        from PIL import Image
        with tempfile.TemporaryDirectory(prefix='thor-publish-no-tools-') as temporary:
            root=Path(temporary)
            (root/'main.thor').write_text('package main\nmain::proc(){}\n')
            request=root/'request.json'
            request.write_text(json.dumps(dict(entry=str(root),flags=[],system='linux',arch='amd64',options={},native=False)))
            env={**os.environ,'PATH':str(root/'no-tools')}
            result=subprocess.run([str(PROTOCOL),'publisher',str(request)],env=env,cwd=root,capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('ok',json.loads(result.stdout))
            icon=root/'icon.png';Image.new('RGBA',(12,8),(0,0,0,0)).save(icon)
            result=subprocess.run([str(PROTOCOL),str(icon),str(root/'normalized.png'),'linux'],env=env,capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            with Image.open(root/'normalized.png') as converted:
                self.assertEqual(converted.size,(8,8))

    @unittest.skipUnless(sys.platform=='darwin','real native macOS code signing')
    def test_all_package_formats_and_managed_replacements(self):
        count=cases.verify_packages(PROTOCOL)
        self.assertEqual(count,16)

@unittest.skipUnless(sys.platform=='darwin','native macOS packaging')
class RustMacPublishTests(reference.PublishFixture):
    setUpClass=classmethod(native.RustCompilerTests.setUpClass.__func__)

    def setUp(self):
        super().setUp()
        original=subprocess.run
        def run(arguments,*args,**kwargs):
            if isinstance(arguments,(list,tuple)) and str(arguments[0])==str(ROOT/'thor'):
                arguments=[str(native.RUST),*arguments[1:]]
            return original(arguments,*args,**kwargs)
        self.patch=patch.object(reference.subprocess,'run',side_effect=run)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    invoke=reference.MacPublishTests.invoke

    def test_json_packaging_error_and_crop_warning_share_one_packet(self):
        from PIL import Image
        icon=self.image()
        Image.new('RGB',(12,6),(20,80,200)).save(icon)
        self.file('thor-publish.json',b'conflict')
        result=subprocess.run([str(native.RUST),'publish',str(self.project),'-json-errors',
            '--app-resource','thor-publish.json'],cwd=self.output,capture_output=True,text=True,timeout=90)
        self.assertEqual(result.returncode,1,result.stdout+result.stderr)
        packet=json.loads(result.stderr)
        self.assertEqual(packet['error_count'],1)
        self.assertEqual([error['type'] for error in packet['errors']],['warning','error'])
        self.assertIn('conflicts with Thor',str(packet))
        self.assertFalse(list(self.output.glob('.thor-publish-*')))

    def test_toolchain_validation_precedes_bad_config_and_metadata_is_retained(self):
        self.config('[publish\ninvalid')
        result=subprocess.run([str(native.RUST),'publish',str(self.project),'-json-errors',
            '--odin',str(self.directory/'missing-odin')],cwd=self.output,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,1)
        packet=json.loads(result.stderr)
        self.assertIn('Odin compiler not found',str(packet))
        (self.project/'thor.toml').unlink()
        result=subprocess.run([str(native.RUST),'publish',str(self.project),'-json-errors','--editor-info'],
            cwd=self.output,capture_output=True,text=True,timeout=90)
        self.assertEqual(result.returncode,0,result.stderr)
        packet=json.loads(result.stderr)
        self.assertEqual(packet['error_count'],0)
        self.assertTrue(packet['slice_access'])

    def test_options_before_source_equals_forms_and_unchecked_release(self):
        source='''package main
import "thor:app"
import "core:os"
import "core:fmt"
main::proc(){p:=app.resource_path("assets/data.txt");defer delete(p)
data,err:=os.read_entire_file(p,context.allocator);if err!=nil{panic("read")};defer delete(data)
fmt.println(string(data))}
'''
        (self.project/'main.thor').write_text(source)
        self.file('assets/data.txt',b'content')
        for flags in [[],['-disable-assert','-no-bounds-check','-no-type-assert']]:
            result=subprocess.run([str(native.RUST),'publish','--app-name=Chosen',
                '--app-version','1.2',str(self.project),'-o:speed',*flags],cwd=self.output,
                capture_output=True,text=True,timeout=90)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            binary=Path(result.stdout.strip())/'Contents/MacOS/output'
            run=subprocess.run([str(binary)],cwd='/tmp',capture_output=True,text=True,timeout=30)
            self.assertEqual((run.returncode,run.stdout),(0,'content\n'),run.stderr)

# Reuse the established behavior tests while routing compiler invocations to Rust.
for name in [
 'test_visible_icon_crop_warning_is_structured_and_obeys_warning_flags',
 'test_palette_icon_with_only_transparent_padding_publishes_without_warning',
 'test_real_gui_app_with_icon_moves_away_from_sources_and_preserves_arguments',
 'test_development_resource_lookup_is_independent_of_cwd',
 'test_console_mode_is_explicit_and_shell_quotes_executable',
 'test_native_library_dependency_closure_is_copied_and_relocated',
 'test_versioned_framework_is_copied_with_internal_links_and_resigned',
 'test_declared_dynamic_plugin_loads_from_bundled_rpath',
 'test_finder_gui_launch_runs_without_a_terminal_wrapper',
 'test_republishing_updates_only_managed_app',
 'test_publish_compiler_errors_keep_original_source_locations_and_previous_app',
 'test_debug_allocation_tracking_accepts_owned_resource_paths',
 'test_bad_publish_config_does_not_affect_ordinary_builds',
 'test_resource_helper_checks_on_linux_target',
 'test_publish_flags_are_not_accepted_by_build',
]:
    setattr(RustMacPublishTests,name,getattr(reference.MacPublishTests,name))
