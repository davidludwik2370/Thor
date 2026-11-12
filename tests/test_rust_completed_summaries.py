"""Completed graph reuse preserves effects, aliases and diagnostics on retries."""
import json
import os
import unittest
from tests import test_rust_compiler as native
from tests import test_rust_dependency_lookahead as dependencies


class RustCompletedSummaryTests(unittest.TestCase):
    setUpClass = classmethod(native.RustCompilerTests.setUpClass.__func__)
    setUp = native.RustCompilerTests.setUp
    invoke = native.RustCompilerTests.invoke
    program = native.RustCompilerTests.program
    sdk_overlay = dependencies.RustDependencyLookaheadTests.sdk_overlay
    chain = dependencies.RustDependencyLookaheadTests.chain

    def prepare(self, prefix='', body=None):
        overlay=self.chain(body=body)
        self.source.write_text(self.source.read_text().replace('main :: proc()',prefix+'\nmain :: proc()'))
        return overlay

    def profile(self,overlay,*,flags=(),error=None):
        path=self.root/'profile.json'
        result=self.invoke('rust',command='build' if error else 'run',
            flags=['--overlay',str(overlay),*flags],env={**os.environ,'THOR_PROFILE':str(path)})
        if error:
            self.assertNotEqual(result.returncode,0)
            self.assertIn(error,result.stderr)
            self.assertIn(str(self.source),result.stderr)
        else:self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        return json.loads(path.read_text())['counters']

    def test_completed_summaries_survive_namespace_extension(self):
        prefix='\n'.join(f'reader_{n}::proc(values:[]int)->int{{return values[0]+{n}}}' for n in range(30))
        overlay=self.prepare(prefix)
        for flags in [['-debug'],native.UNCHECKED]:
            counts=self.profile(overlay,flags=flags)
            self.assertGreaterEqual(counts.get('permissions.summary_hits',0),30,counts)

    def test_closed_call_graph_keeps_return_aliases_and_mutating_callees(self):
        prefix='''
leaf::proc(values:[]int)->[]int{return values}
outer::proc(values:[]int)->[]int{return leaf(values)}
write::proc(values:[]int){values[0]=9}
reader::proc(values:[]int)->int{return outer(values)[0]}
'''
        overlay=self.prepare(prefix,'''items:=[1]int{7};view:>items[:]
if reader(view)!=7{panic("cached read")}
write(outer(items[:]))
if items[0]!=9{panic("cached alias")}
if math.thor_probe_0(view)!=9{panic("dependency")}
''')
        for flags in [['-debug'],native.UNCHECKED]:
            counts=self.profile(overlay,flags=flags)
            self.assertGreater(counts.get('permissions.summary_hits',0),0,counts)
        self.source.write_text(self.source.read_text().replace('write(outer(items[:]))','write(outer(view))'))
        self.profile(overlay,error='read-only slice')

    def test_local_readonly_issue_is_replayed_after_discovery_retry(self):
        prefix='bad::proc(){values:>[]int{1};values[0]=2}\n'
        overlay=self.prepare(prefix)
        for flags in [['-debug'],native.UNCHECKED]:
            counts=self.profile(overlay,flags=flags,error='read-only slice')
            self.assertGreater(counts.get('permissions.summary_hits',0),0,counts)

    def test_named_results_cycles_and_shadowed_locals_keep_their_storage(self):
        prefix='''
Node::struct{values:[]int,next:^Node}
build::proc(values:[]int)->(node:Node){node.values=values;return}
read::proc(values:[]int)->int{
    result:=values[0]
    {result:=result+1;_=result}
    return result
}
'''
        overlay=self.prepare(prefix,'''items:=[1]int{7};node:=build(items[:]);node.next=&node
if node.next.values[0]!=7||read(items[:])!=7{panic("cached graph")}
node.next.values[0]=8
if items[0]!=8||math.thor_probe_0(items[:])!=8{panic("cycle alias")}
''')
        for flags in [['-debug'],native.UNCHECKED]:self.profile(overlay,flags=flags)

    def test_generic_array_and_slice_instantiations_remain_distinct(self):
        prefix='''
first::proc(values:$T)->int{return values[0]}
assign::proc(values:$T){local:=values;local[0]=9}
'''
        overlay=self.prepare(prefix,'''items:=[1]int{7};view:>items[:]
if first(items)!=7||first(view)!=7{panic("specialization")}
assign(items)
if items[0]!=7||math.thor_probe_0(view)!=7{panic("array copy")}
''')
        for flags in [['-debug'],native.UNCHECKED]:self.profile(overlay,flags=flags)
        self.source.write_text(self.source.read_text().replace('assign(items)','assign(view)'))
        self.profile(overlay,error='read-only slice')

    def test_global_reference_state_is_not_frozen_by_summary_reuse(self):
        prefix='''
global:[]int
store::proc(values:[]int){global=values}
write_global::proc(){global[0]=9}
'''
        overlay=self.prepare(prefix,'''items:=[1]int{7};store(items[:]);write_global()
if items[0]!=9||math.thor_probe_0(items[:])!=9{panic("global storage")}
''')
        for flags in [['-debug'],native.UNCHECKED]:self.profile(overlay,flags=flags)
        self.source.write_text(self.source.read_text().replace('store(items[:])','view:>items[:];store(view)'))
        self.profile(overlay,error='read-only slice')

    def test_recursive_procedures_keep_the_normal_fixed_point_driver(self):
        overlay=self.prepare('''
walk::proc(values:[]int,n:int)->int{
    if n==0{return values[0]}
    return walk(values,n-1)
}
''','''items:=[1]int{7};view:>items[:]
if walk(view,3)!=7||math.thor_probe_0(view)!=7{panic("recursive read")}
''')
        for flags in [['-debug'],native.UNCHECKED]:self.profile(overlay,flags=flags)


if __name__=='__main__':unittest.main()
