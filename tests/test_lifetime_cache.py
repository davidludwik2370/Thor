"""Warm memory checks retain evidence and invalidate every observable input."""
import json
import os
import shutil
import subprocess
import time
import unittest
from tests import test_rust_compiler as native
from tests.cache_paths import entries
from tests.native_paths import directory_alias, remove_directory_alias


class LifetimeCacheTests(unittest.TestCase):
    setUpClass=classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp=native.RustCompilerTests.setUp
    invoke=native.RustCompilerTests.invoke

    def vet(self, *flags, command='vet'):
        profile=self.root/'profile.json'
        result=self.invoke('rust',command=command,directory=True,
            flags=['--vet=lifetime,leak','--comptime=required','-json-errors','-thread-count:1',*flags],
            env={**os.environ,'THOR_PROFILE':str(profile)})
        packet=json.loads(result.stderr)
        self.assertEqual(result.returncode,int(packet['error_count']>0),result.stdout+result.stderr)
        return packet,json.loads(profile.read_text())['counters']

    def entries(self):
        return entries(self.root/'cache','lifetime-v1')

    def test_warm_results_and_reporting_modes_retain_all_evidence(self):
        self.source.write_text('package main\nwork::proc(callback:proc(v:[]int)){v~:=make([]int,2);callback(v)}\nmain::proc(){v:=make([]int,2);delete(v);_=v[0]}\n')
        cold,counts=self.vet()
        self.assertEqual(counts.get('vet.memory_cache_writes'),1,counts)
        for mode in ['summary','show','error']:
            warm,counts=self.vet('--unverified='+mode)
            self.assertEqual(counts.get('vet.memory_cache_hits'),1,counts)
            self.assertEqual(counts.get('vet.lifetime_steps',0),0,counts)
            expected=[{k:v for k,v in d.items()if k!='type'}for d in cold['errors']]
            self.assertEqual(expected,[{k:v for k,v in d.items()if k!='type'}for d in warm['errors']])
        uncached,counts=self.vet('--no-cache')
        self.assertEqual(cold,uncached)
        self.assertNotIn('vet.memory_cache_hits',counts)

    def test_same_length_source_edit_invalidates_even_if_mtime_is_restored(self):
        self.source.write_text('package main\nmain::proc(){v:=make([]int,2);delete(v);_=v[0]}\n')
        before,_=self.vet()
        self.assertTrue(before['errors'])
        stamp=self.source.stat()
        self.source.write_text(self.source.read_text().replace('delete(v);_=v[0]','_=v[0];delete(v)'))
        os.utime(self.source,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
        after,counts=self.vet()
        self.assertEqual(after['errors'],[])
        self.assertNotIn('vet.memory_cache_hits',counts)

    def test_imported_native_body_edits_invalidate_cached_effects(self):
        library=self.app/'library';library.mkdir()
        helper=library/'library.odin'
        helper.write_text('package library\nuse::proc(p:^int){_=p^}\n')
        self.source.write_text('package main\nimport "library"\nmain::proc(){p~:=new(int);library.use(p);_=p^}\n')
        safe,_=self.vet();self.assertEqual(safe['errors'],[])
        self.vet()
        helper.write_text('package library\nuse::proc(p:^int){free(p)}\n')
        bad,counts=self.vet()
        self.assertTrue(any(d['code']=='thor/lifetime'for d in bad['errors']),bad)
        self.assertNotIn('vet.memory_cache_hits',counts)

    def test_flags_checks_and_disabled_assertion_paths_have_distinct_keys(self):
        self.source.write_text('package main\nread::proc(v:[]int){assert(v[0]==1)}\nmain::proc(){v:=make([]int,2);delete(v);read(v)}\n')
        checked,_=self.vet();self.assertTrue(checked['errors'])
        unchecked,counts=self.vet('-disable-assert')
        self.assertEqual(unchecked['errors'],[])
        self.assertNotIn('vet.memory_cache_hits',counts)
        _,counts=self.vet('-disable-assert');self.assertEqual(counts.get('vet.memory_cache_hits'),1)
        lifetime,_=self.vet('--vet=lifetime')
        self.assertTrue(lifetime['errors'])
        checked_again,counts=self.vet();self.assertEqual(checked_again,checked)
        self.assertEqual(counts.get('vet.memory_cache_hits'),1)

    def test_added_source_and_explicit_acknowledgement_do_not_hide_real_issues(self):
        self.source.write_text('package main\nmain::proc(){}\n')
        self.vet();self.vet()
        (self.app/'extra.thor').write_text('package main\n@(vet_ignore={.lifetime_unknown,.leak_unknown}) broken::proc(){p:=new(int);free(p);_=p^}\n')
        packet,counts=self.vet()
        self.assertTrue(any(d['certainty']=='definite'for d in packet['errors']),packet)
        self.assertNotIn('vet.memory_cache_hits',counts)

    def test_resource_contents_are_revalidated(self):
        resource=self.app/'value.txt';resource.write_text('safe')
        self.source.write_text('package main\nTEXT::#load("value.txt",string)\nmain::proc(){v:=make([]int,2);defer delete(v);when TEXT=="safe"{_=v[0]}else{delete(v)}}\n')
        clean,_=self.vet();self.assertEqual(clean['error_count'],0)
        _,counts=self.vet();self.assertEqual(counts.get('vet.memory_cache_hits'),1,counts)
        resource.write_text('fail')
        packet,counts=self.vet()
        self.assertEqual(packet['error_count'],0,packet)
        self.assertNotIn('vet.memory_cache_hits',counts)

    def test_result_discovery_skips_inactive_calls_but_rechecks_enabled_branches(self):
        self.source.write_text('package main\nimport "core:math"\nmain::proc(){when #config(THOR_METADATA_ENABLED,false){_=math.sqrt(f64(9))}}\n')
        packet,counts=self.vet('--no-cache')
        self.assertEqual(packet['errors'],[])
        self.assertGreater(counts.get('lifetime.result_fact_inactive',0),0,counts)
        _,selected=self.vet('--no-cache','-define:THOR_METADATA_ENABLED=true')
        self.assertGreater(selected.get('lifetime.result_fact_queries',0),counts.get('lifetime.result_fact_queries',0),selected)

    def test_import_symlink_retarget_does_not_keep_the_old_contract(self):
        for name,body in [('reader','_=p^'),('writer','free(p)')]:
            target=self.root/name;target.mkdir()
            (target/'lib.odin').write_text('package lib\nuse::proc(p:^int){'+body+'}\n')
        link=self.app/'library';directory_alias(link,self.root/'reader')
        self.source.write_text('package main\nimport lib "library"\nmain::proc(){p~:=new(int);lib.use(p);_=p^}\n')
        self.vet();self.vet()
        remove_directory_alias(link);directory_alias(link,self.root/'writer')
        packet,counts=self.vet()
        self.assertTrue(any(d['code']=='thor/lifetime'for d in packet['errors']),packet)
        self.assertNotIn('vet.memory_cache_hits',counts)

    def test_corrupt_missing_and_cleared_cache_recompute_without_losing_findings(self):
        self.source.write_text('package main\nmain::proc(){v:=make([]int,2);delete(v);_=v[0]}\n')
        original,_=self.vet();entries=self.entries();self.assertEqual(len(entries),1)
        entries[0].write_text('{broken')
        repaired,counts=self.vet();self.assertEqual(original,repaired)
        self.assertNotIn('vet.memory_cache_hits',counts)
        entries[0].unlink()
        missing,_=self.vet();self.assertEqual(original,missing)
        cleared,counts=self.vet('--clear-cache');self.assertEqual(original,cleared)
        self.assertNotIn('vet.memory_cache_hits',counts)

    def test_unsaved_overlays_never_reuse_a_disk_source_contract(self):
        self.source.write_text('package main\nmain::proc(){v:=make([]int,2);_=v[0];delete(v)}\n')
        clean,_=self.vet();self.assertEqual(clean['errors'],[])
        replacement=self.root/'edited.thor'
        replacement.write_text(self.source.read_text().replace('_=v[0];delete(v)','delete(v);_=v[0]'))
        overlay=self.root/'overlay.json'
        overlay.write_text(json.dumps({'Replace':{str(self.source):str(replacement)}}))
        packet,counts=self.vet('--overlay',str(overlay))
        self.assertTrue(any(d.get('code')=='thor/lifetime'for d in packet['errors']),packet)
        self.assertNotIn('vet.memory_cache_hits',counts)
        self.assertEqual(counts.get('vet.memory_cache_bypasses'),1,counts)
        restored,counts=self.vet();self.assertEqual(restored,clean)
        self.assertEqual(counts.get('vet.memory_cache_hits'),1)

    def test_cached_strict_build_still_fails_on_definite_memory_errors(self):
        self.source.write_text('package main\nmain::proc(){v:=make([]int,2);delete(v);_=v[0]}\n')
        for flags in [[],native.UNCHECKED]:
            cold,_=self.vet(*flags,command='build')
            warm,counts=self.vet(*flags,command='build')
            self.assertEqual(cold,warm)
            self.assertEqual(warm['error_count'],1)
            self.assertEqual(counts.get('vet.memory_cache_hits'),1,counts)

    def test_unwritable_memory_cache_does_not_change_the_findings(self):
        self.source.write_text('package main\nmain::proc(){v:=make([]int,2);delete(v);_=v[0]}\n')
        expected,_=self.vet()
        blocked=self.root/'blocked-cache';blocked.write_text('blocked')
        actual,counts=self.vet('--cache-dir',str(blocked))
        self.assertEqual(actual,expected)
        self.assertNotIn('vet.memory_cache_hits',counts)
        self.assertEqual(blocked.read_text(),'blocked')

    @unittest.skipUnless(os.name=='posix','replacing a running executable requires Unix')
    def test_running_compiler_uses_its_embedded_revision_after_executable_replacement(self):
        self.source.write_text('package main\nmain::proc(){p~:=new(int);_=p^}\n')
        binary=self.root/'compiler';shutil.copy2(native.RUST,binary)
        actual=shutil.which(os.environ.get('THOR_ODIN','odin'))
        self.assertIsNotNone(actual)
        wrapper=self.root/'odin-wrapper'
        wrapper.write_text('#!/usr/bin/env python3\nimport os,sys,time\nfrom pathlib import Path\n'+
            'root=Path(__file__).parent\nif sys.argv[1]=="check":\n'+
            ' (root/"entered").touch()\n while not (root/"release").exists():time.sleep(0.01)\n'+
            'os.execv('+repr(actual)+',['+repr(actual)+',*sys.argv[1:]])\n')
        wrapper.chmod(0o755)
        args=[str(binary),'vet',str(self.app),'--odin',str(wrapper),'--comptime=required',
              '--vet=lifetime,leak','--cache-dir',str(self.root/'cache'),'--output-dir',str(self.root/'generated'),'-json-errors']
        profile=self.root/'replace-profile.json'
        process=subprocess.Popen(args,cwd=self.root,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
            env={**os.environ,'THOR_PROFILE':str(profile)})
        try:
            deadline=time.monotonic()+30
            while not (self.root/'entered').exists() and process.poll()is None and time.monotonic()<deadline:time.sleep(0.01)
            self.assertTrue((self.root/'entered').exists())
            replacement=self.root/'replacement';replacement.write_bytes(b'not the running compiler')
            os.replace(replacement,binary)
        finally:
            (self.root/'release').touch()
            out,err=process.communicate(timeout=45)
        self.assertEqual(process.returncode,0,out+err)
        shutil.copy2(native.RUST,replacement);os.replace(replacement,binary)
        warm=subprocess.run(args,cwd=self.root,capture_output=True,text=True,timeout=45,
            env={**os.environ,'THOR_PROFILE':str(profile)})
        self.assertEqual(warm.returncode,0,warm.stdout+warm.stderr)
        self.assertEqual(json.loads(profile.read_text())['counters'].get('vet.memory_cache_hits'),1)


if __name__=='__main__':unittest.main()
