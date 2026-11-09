"""Native contents reconstruction matches Python and survives unchecked Odin builds."""
import json,random,shutil,subprocess,tempfile,unittest
from thorlang import values
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BINARY=ROOT/'rust/target/release/examples/value_protocol'

def integer(n):return dict(kind='integer',value=str(n))
def sequence(items,storage='array',nil=False,**kwargs):return dict(kind='sequence',values=items,storage=storage,nil=nil,**kwargs)

class RustValueProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("cargo") is None:raise unittest.SkipTest("Rust value tests require Cargo")
        result=subprocess.run(['cargo','build','--release','--offline','--locked','--manifest-path',str(ROOT/'rust/Cargo.toml'),'--example','value_protocol'],text=True,capture_output=True,timeout=120)
        if result.returncode:raise RuntimeError(result.stdout+result.stderr)
    def compare(self,packets):
        requests=''.join(json.dumps(dict(packet=packet))+'\n' for packet in packets)
        process=subprocess.run([BINARY],input=requests,text=True,capture_output=True,check=True)
        actual=[json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual(len(actual),len(packets))
        for packet,result in zip(packets,actual):
            with self.subTest(packet=packet):
                try:
                    value=values.decode(json.dumps(packet).encode());expression=values.expression(value,'T','meta');materialize=values.materialize(value,'T','meta','pure','value_')
                    expected=dict(ok=dict(expression=expression,materialize=materialize,owned=values.has_owned(value),slices=values.has_owned(value,slices_only=True),dynamic=values.has_dynamic(value)))
                except values.ValueErrorProtocol as error:expected=dict(error=str(error))
                self.assertEqual(result,expected)
    def test_scalars(self):
        data=[integer(n) for n in [0,-1,1,-(1<<127),(1<<128)-1]]+[dict(kind='integer',value='-000')]
        data += [dict(kind='bool',value=b) for b in [False,True]]
        data += [dict(kind='string',bytes=s) for s in ['', '00ff22415c','f09f9880', '00 ff\t22']]
        data += [dict(kind=k,bytes=b,byte_order=o) for k in ['float','bits'] for b in ['0000','0000803f','0000000000000080'] for o in ['big','little']]
        data += [dict(kind='bits',bytes='ff'*16),dict(kind='float',bytes='000000000000f87f',byte_order='little')]
        self.compare([dict(thor_value=1,value=v) for v in data])
    def test_aggregates_and_ownership(self):
        string=dict(kind='string',bytes='6869');floating=dict(kind='float',bytes='000000000000f03f',byte_order='little')
        data=[sequence([integer(1),integer(2)]),sequence([],storage='slice',nil=True),sequence([],storage='slice'),sequence([string],storage='slice'),sequence([],storage='dynamic',capacity=7),sequence([],storage='dynamic',nil=True,capacity=0),sequence([dict(kind='record',fields=[])],storage='dynamic',nil=True,zero_sized=True,capacity=4)]
        data +=[dict(kind='bitset',values=[integer(2),integer(5)]),dict(kind='record',fields=[['text',string],['values',data[3]]]),dict(kind='record',fields=[['é',integer(3)],['e\u0301',integer(4)]]),dict(kind='enum_array',fields=[['One',string]]),dict(kind='union',variant=-1),dict(kind='union',variant=1,value=data[3]),dict(kind='complex',values=[floating,floating]),dict(kind='quaternion',values=[floating]*4),dict(kind='specialized',types=[dict(kind='string',bytes='696e74')],value=integer(3))]
        self.compare([dict(thor_value=1,value=v) for v in data])
    def test_invalid(self):
        bad=[None,{},dict(kind='nope'),dict(kind='integer',value=str(1<<128)),dict(kind='integer',value=str(-(1<<127)-1)),dict(kind='integer',value=True),dict(kind='integer',value='+1'),dict(kind='bool',value=1),dict(kind='float',bytes='00'),dict(kind='float',bytes='00'*16),dict(kind='float',bytes='gg'),dict(kind='float',bytes='0 0'),dict(kind='bits',bytes='00',byte_order='sideways'),dict(kind='sequence',values=[],nil=False,storage='map'),dict(kind='sequence',values=[],nil=0),sequence([],storage='dynamic',capacity=-1),sequence([integer(1)],storage='dynamic',capacity=0),sequence([],storage='dynamic',nil=True,capacity=1),sequence([integer(1)],storage='slice',nil=True),dict(kind='record',fields=[['a',integer(1)],['a',integer(2)]]),dict(kind='record',fields=[['x; panic("bad")',integer(1)]]),dict(kind='union',variant=-2),dict(kind='complex',values=[]),dict(kind='specialized',types=[integer(1)],value=integer(0))]
        self.compare([dict(thor_value=1,value=v) for v in bad]+[{},dict(thor_value=2,value=integer(3))])
    def test_generated_portable_packets(self):
        randomizer=random.Random(20261109)
        def make(depth=0):
            selected=randomizer.randrange(5 if depth>=4 else 10)
            if selected==0:return integer(randomizer.randrange(-(1<<127),1<<128))
            if selected==1:return dict(kind='bool',value=bool(randomizer.randrange(2)))
            if selected==2:return dict(kind='string',bytes=bytes(randomizer.randrange(256) for _ in range(randomizer.randrange(8))).hex())
            if selected==3:return dict(kind='float',bytes=bytes(randomizer.randrange(256) for _ in range(randomizer.choice([2,4,8]))).hex(),byte_order=randomizer.choice(['little','big']))
            if selected==4:return dict(kind='bitset',values=[integer(n) for n in sorted(randomizer.sample(range(10),randomizer.randrange(5)))])
            if selected==5:
                items=[make(depth+1) for _ in range(randomizer.randrange(4))];storage=randomizer.choice(['array','slice','dynamic']);nil=not items and bool(randomizer.randrange(2))
                return sequence(items,storage,nil,**(dict(capacity=len(items) if nil else len(items)+randomizer.randrange(5)) if storage=='dynamic' else {}))
            if selected==6:return dict(kind='record',fields=[['field_'+str(n),make(depth+1)] for n in range(randomizer.randrange(4))])
            if selected==7:return dict(kind='union',variant=0,value=make(depth+1))
            if selected==8:return dict(kind='enum_array',fields=[['Item'+str(n),make(depth+1)] for n in range(randomizer.randrange(4))])
            return dict(kind='specialized',types=[dict(kind='string',bytes='696e74')],value=make(depth+1))
        self.compare([dict(thor_value=1,value=make()) for _ in range(200)])
    def test_specialization_metadata_keeps_payload_ownership(self):
        payload=sequence([dict(kind='string',bytes='78')],storage='dynamic',capacity=4)
        wrapped=dict(kind='specialized',types=[dict(kind='string',bytes='696e74')],value=payload)
        self.compare([dict(thor_value=1,value=wrapped)])
        owned=values.has_owned(wrapped)
        dynamic=values.has_dynamic(wrapped)
        code=values.materialize(wrapped,'T','meta','pure','value_')
        self.assertTrue(owned and dynamic)
        self.assertIn('allocate_dynamic',code)
        self.assertIn('pure.clone',code)

    def test_depth(self):
        value=integer(1)
        for _ in range(33):value=sequence([value])
        self.compare([dict(thor_value=1,value=value)])
    def roundtrip(self,specialized):
        types='''
        Choice::union{int,string}
        Kind::enum{a,b,c}
        T::struct{text:string,values:[][]int,growing:[dynamic]string,empty:[]int,zeroes:[]struct{},number:f64,choice:Choice,bits:bit_set[Kind],indexed:[Kind]int}
        '''
        with tempfile.TemporaryDirectory(prefix='thor-value-roundtrip-') as work:
            p=Path(work);source=p/'input.odin'
            source.write_text('''package main
        import eval "thor:eval"
        '''+types+'''
        main::proc(){
            value:T
            value.text="hi\\x00\\xff"
            row:=[]int{3,4}
            value.values=[][]int{row}
            value.growing=make([dynamic]string,1,4)
            defer delete(value.growing)
            value.growing[0]="hello"
            value.zeroes=make([]struct{},3)
            defer delete(value.zeroes)
            value.number=transmute(f64)u64(0x8000000000000000)
            value.choice=string("union")
            value.bits={.a,.c}
            value.indexed={.a=3,.b=4,.c=5}
            eval.emit(value)
        }
        ''')
            if specialized:
                source.write_text(source.read_text().replace('eval.emit(value)','eval.emit_specialized(value,[]string{"T"})'))
            flags=['-file','-collection:thor='+str(ROOT/'runtime')]
            run=subprocess.run(['odin','run',str(source),*flags],text=True,capture_output=True,timeout=60)
            if run.returncode:raise RuntimeError(run.stdout+run.stderr)
            packet=json.loads(run.stdout)
            response=subprocess.run([BINARY],input=json.dumps(dict(packet=packet))+'\n',text=True,capture_output=True,check=True)
            data=json.loads(response.stdout)
            if 'error' in data:raise RuntimeError(data)
            generated=data['ok']['materialize']
            target=p/'check.odin'
            target.write_text('''package main
        import meta "base:intrinsics"
        import pure "thor:pure"
        import "core:mem"
        '''+types+'''
        make_result::proc()->T{
        '''+generated+'''
        }
        main::proc(){
            tracker:mem.Tracking_Allocator
            mem.tracking_allocator_init(&tracker,context.allocator)
            defer mem.tracking_allocator_destroy(&tracker)
            context.allocator=mem.tracking_allocator(&tracker)
            value:=make_result()
            if value.text!="hi\\x00\\xff" || len(value.values)!=1 || len(value.values[0])!=2 || value.values[0][0]!=3 || value.values[0][1]!=4{panic("record/slice contents")}
            if len(value.growing)!=1 || cap(value.growing)!=4 || value.growing[0]!="hello"{panic("dynamic capacity")}
            if len(value.empty)!=0 || raw_data(value.empty)!=nil || len(value.zeroes)!=3{panic("nil/zero-sized shape")}
            if transmute(u64)value.number!=0x8000000000000000 || value.choice.(string)!="union"{panic("numeric/union bits")}
            expected_bits:=bit_set[Kind]{.a,.c}
            expected_indexed:=[Kind]int{.a=3,.b=4,.c=5}
            if value.bits!=expected_bits || value.indexed!=expected_indexed{panic("enum containers")}
            value.values[0][0]=9
            pure.destroy(value)
            if len(tracker.allocation_map)!=0 || len(tracker.bad_free_array)!=0{panic("reconstruction leaks")}
        }
        ''')
            for mode in ([],['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert']):
                result=subprocess.run(['odin','run',str(target),*flags,*mode],text=True,capture_output=True,timeout=60)
                if result.returncode:raise RuntimeError(result.stdout+result.stderr+'\n'+target.read_text())
    def test_checked_and_unchecked_native_roundtrip_has_no_leaks(self):
        for specialized in (False,True):
            with self.subTest(specialized=specialized):self.roundtrip(specialized)

if __name__=='__main__':unittest.main()
