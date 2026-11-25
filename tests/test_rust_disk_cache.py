"""Default cache sharing, bounds and cache-independent compiler behavior."""
import json
import os
from pathlib import Path
import platform
import subprocess
import unittest
from tests import test_rust_compiler as native
from tests.cache_paths import entries

class RustDiskCacheTests(unittest.TestCase):
    setUpClass=classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp=native.RustCompilerTests.setUp

    def invoke(self,source,*,flags=(),env=None,command='check',profile=None):
        environment={**os.environ,**(env or {})}
        if profile:environment['THOR_PROFILE']=str(profile)
        result=subprocess.run([str(native.RUST),command,str(source),'--comptime=required',
            '--output-dir',str(self.root/'generated'/source.parent.name),'-thread-count:2',*flags],
            cwd=self.root,env=environment,text=True,capture_output=True,timeout=90)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        return result

    def test_default_cache_is_shared_without_creating_per_project_caches(self):
        environment={**os.environ,'HOME':str(self.root/'home'),'XDG_CACHE_HOME':str(self.root/'xdg'),
                     'LOCALAPPDATA':str(self.root/'local')}
        environment.pop('THOR_CACHE_DIR',None)
        expected=(self.root/'home/Library/Caches/Thor' if platform.system()=='Darwin' else
                  self.root/'local/Thor/cache' if os.name=='nt' else self.root/'xdg/thor')
        for name in ['alpha','beta']:
            source=self.root/name/'main.thor';source.parent.mkdir()
            source.write_text('package main\nimport "thor:basic"\nmain::proc(){basic.print(42)}\n')
            profile=self.root/(name+'.json')
            # Use the complete environment so inherited cache overrides cannot
            # defeat this test of platform defaults.
            result=subprocess.run([str(native.RUST),'check',str(source),'--comptime=required',
                '--output-dir',str(self.root/'generated'/name),'-thread-count:2'],cwd=self.root,
                env={**environment,'THOR_PROFILE':str(profile)},capture_output=True,text=True,timeout=90)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertFalse((source.parent/'.thor-cache').exists())
        self.assertTrue(entries(expected,'syntax-*'))
        warm=json.loads((self.root/'beta.json').read_text())['counters']
        self.assertGreater(warm.get('parser.syntax_cache_hits',0),0)
        self.assertLessEqual(warm.get('parser.processes',0),2,warm)
        self.assertFalse(entries(expected,'parser-v1'))

    def test_eviction_rebuilds_identical_checked_and_unchecked_programs(self):
        cache=self.root/'cache';environment={'THOR_CACHE_DIR':str(cache)}
        self.source.write_text('''package main
import "core:fmt"
VALUE :: #run answer()
answer :: fn()->int{return 42}
main :: proc(){values~:=[answer() for n in 0..<3];if len(values)!=3||values[1]!=VALUE{panic("bad result")};fmt.println(VALUE)}
''')
        for flags in [[],native.UNCHECKED]:
            first=self.invoke(self.source,command='run',env=environment,flags=flags)
            for path in entries(cache,'syntax-*')+entries(cache,'eval-v1'):path.unlink()
            second=self.invoke(self.source,command='run',env=environment,flags=flags)
            self.assertEqual(first.stdout,second.stdout)
            self.assertEqual(second.stdout.strip(),'42')

    def test_file_build_project_names_remain_part_of_cache_identity(self):
        # The same dependency is parsed under two entry-project names. Its
        # build-project-name tag must retain distinct exclusion results.
        shared=self.root/'shared';shared.mkdir()
        (shared/'shared.thor').write_text('#+build-project-name alpha\npackage shared\nVALUE :: 42\n')
        cache=self.root/'cache';environment={**os.environ,'THOR_CACHE_DIR':str(cache)}
        for name in ['alpha','beta','alpha']:
            source=self.root/name/'main.thor';source.parent.mkdir(exist_ok=True)
            source.write_text('package main\nimport "../shared"\nmain::proc(){_=shared.VALUE}\n')
            result=subprocess.run([str(native.RUST),'check',str(source),'--output-dir',str(self.root/'out'/name)],
                env=environment,cwd=self.root,capture_output=True,text=True,timeout=60)
            self.assertEqual(result.returncode==0,name=='alpha',result.stdout+result.stderr)

if __name__=='__main__':unittest.main()
