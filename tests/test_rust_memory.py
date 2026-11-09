"""Reuse standalone native memory-report fixtures against the Rust compiler."""
import unittest
from unittest.mock import patch
from tests import test_memory_debug as memory
from tests import test_rust_compiler as rust

@unittest.skipUnless(memory.sys.platform == 'darwin' and memory.shutil.which('odin'),
                     'native macOS Odin required')
class RustMemoryTests(unittest.TestCase):
    setUpClass=classmethod(rust.RustCompilerTests.setUpClass.__func__)
    setUp=memory.MemoryDebugTests.setUp
    run_program=memory.MemoryDebugTests.run_program

    def build(self,*args,**kwargs):
        with patch.object(memory,'THOR',rust.RUST):
            return memory.MemoryDebugTests.build(self,*args,**kwargs)

for _name in ('clean_defers_finalizers_and_custom_allocators',
              'each_conditional_main_installs_tracking',
              'leaks_basic_without_debug_and_tracking_off',
              'bad_free_is_immediate_and_mapped',
              'standalone_asan_survives_moving_and_removing_generated_sources',
              'generated_fstring_leak_and_asan_environment_overrides',
              'user_sanitizer_hooks_and_function_opt_out_are_respected',
              'excluded_platform_hook_does_not_disable_automatic_mapping',
              'explicit_symbolizer_with_a_conditional_user_hook',
              'expression_helpers_stack_and_global_reports',
              'trace_and_imported_thor_allocation_locations',
              'normal_build_has_no_memory_support_and_debug_opt_out_removes_it',
              'native_test_tracker_is_not_replaced'):
    setattr(RustMemoryTests,'test_'+_name,getattr(memory.MemoryDebugTests,'test_'+_name))
