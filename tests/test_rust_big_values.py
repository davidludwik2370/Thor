"""Exact logical-number contents and static backing before language adoption."""
import json, random, shutil, subprocess, tempfile, unittest
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from thorlang import bigvalues, values
from thorlang.bigconstants import BigConstants

from tests.native_paths import rust_binary
ROOT=Path(__file__).resolve().parents[1]
BINARY=rust_binary('big_protocol', example=True)


def reference(request):
    operation=request['operation']
    try:
        if operation=='literal':result=bigvalues.packet(request['kind'],bigvalues.literal(request['text']))
        elif operation=='number':result=bigvalues.packet(request['value']['kind'],bigvalues.number(request['value']))
        elif operation=='contains':result=bigvalues.contains(request['value'])
        else:
            helpers={}
            source=SimpleNamespace(prefix='data_',root=0)
            big=SimpleNamespace(s=source,e=SimpleNamespace(helpers=helpers),module=lambda:'big',mapped=lambda node,code,kind:code)
            backing=BigConstants(big)
            if operation=='backing':result=dict(expression=backing.view(request['value'],None))
            else:
                value=values.decode(json.dumps(request['packet']).encode())
                if operation=='without_backing':
                    return dict(ok=values.expression(value,'T','meta'))
                values.validate(value)
                expression=values.expression(value,'T','meta',big=lambda v,t:backing.view(v,None))
                materialize=values.materialize(value,'T','meta','pure','value_',big=lambda v,t:backing.view(v,None))
                result=dict(expression=expression,materialize=materialize,owned=values.has_owned(value),slices=values.has_owned(value,slices_only=True),dynamic=values.has_dynamic(value))
            result['declarations']={name:code for (_,name),code in helpers.items()}
        return dict(ok=result)
    except (ValueError,TypeError,KeyError) as error:
        return dict(error=str(error))


class RustBigValueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which('cargo') is None or shutil.which('odin') is None:raise unittest.SkipTest('Native numeric protocol requires Cargo and Odin')
        result=subprocess.run(['cargo','build','--release','--offline','--locked','--manifest-path',str(ROOT/'rust/Cargo.toml'),'--example','big_protocol'],capture_output=True,text=True,timeout=600)
        if result.returncode:raise RuntimeError(result.stdout+result.stderr)

    def compare(self,requests):
        process=subprocess.run([BINARY],input=''.join(json.dumps(request)+'\n' for request in requests),capture_output=True,text=True,check=True,timeout=60)
        actual=[json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual(len(actual),len(requests))
        for request,answer in zip(requests,actual):
            expected=reference(request)
            self.assertEqual(answer,expected,str(request)[:150])
        return actual

    def test_exact_literals_all_bases_scales_and_thousands_of_digits(self):
        rng=random.Random(20261110)
        texts=['0','-0','+0','1234567890123456789012345678901234567890','-0x123456789abcdef','0b1_0001','0o777','0d1234','0z123ab','1e32','-4.2e-12','.003','2.000','2_000.00','1'+'0'*5000,'0'*100+'1.2'+'0'*100]
        texts += [str(rng.randrange(-10**80,10**80))+'e'+str(rng.randrange(-50,51)) for _ in range(250)]
        self.compare([dict(operation='literal',text=text,kind=kind) for text in texts for kind in ['big_integer','big_rational']])

    def test_canonical_integer_and_rational_validation(self):
        requests=[dict(operation='number',value=dict(kind='big_integer',value=value)) for value in ['0','1','-1','000','-0','+1','01','1_1','',None,True,10,'8'*5001]]
        requests += [dict(operation='number',value=dict(kind='big_rational',numerator=n,denominator=d)) for n,d in [('0','1'),('0','2'),('-2','3'),('2','4'),('3','-1'),('1','0'),('12','5'),('3','03'),('+3','5'),('1',3)]]
        self.compare(requests)

    def test_nested_contents_detection(self):
        value=bigvalues.packet('big_integer',123)
        cases=[value,dict(kind='big_rational',numerator='2',denominator='3'),dict(kind='string',bytes=''),dict(kind='record',fields=[['x',value]]),dict(kind='specialized',value=dict(kind='sequence',values=[value])),dict(kind='union',variant=-1),dict(kind='union',variant=0,value=value),dict(kind='bitset',values=[value]),dict(kind='enum_array',fields=[['One',value]])]
        self.compare([dict(operation='contains',value=value) for value in cases])

    def backing_packets(self):
        rng=random.Random(20261110)
        packets=[bigvalues.packet('big_integer',n) for n in [0,1,-1,(1<<63)-1,1<<63,1<<126,1<<252,-(1<<255),(1<<1024)-1]]
        packets += [bigvalues.packet('big_rational',Fraction(n,d)) for n,d in [(0,1),(1,1),(-2,3),(3,3),(1<<200,3),(1,-9)]]
        packets += [bigvalues.packet('big_rational',Fraction(rng.randrange(-10**150,10**150),rng.randrange(1,10**100))) for _ in range(100)]
        return packets

    def test_static_limb_boundaries_signs_reduction_and_backing_identity(self):
        self.compare([dict(operation='backing',value=packet) for packet in self.backing_packets()])

    def test_reconstruction_of_mixed_nested_logical_numbers(self):
        rng=random.Random(20261110)
        def make(depth=0):
            selected=rng.randrange(5 if depth>3 else 10)
            if selected==0:return bigvalues.packet('big_integer',rng.randrange(-10**100,10**100))
            if selected==1:return bigvalues.packet('big_rational',Fraction(rng.randrange(-10**80,10**80),rng.randrange(1,10**60)))
            if selected==2:return dict(kind='integer',value=str(rng.randrange(-1000,1000)))
            if selected==3:return dict(kind='string',bytes=bytes([rng.randrange(256)]).hex())
            if selected==4:return dict(kind='bool',value=bool(rng.randrange(2)))
            if selected==5:return dict(kind='record',fields=[['field_'+str(i),make(depth+1)] for i in range(rng.randrange(4))])
            if selected==6:return dict(kind='union',variant=0,value=make(depth+1))
            if selected==7:return dict(kind='specialized',types=[dict(kind='string',bytes='696e74')],value=make(depth+1))
            if selected==8:return dict(kind='enum_array',fields=[['One',make(depth+1)]])
            storage=rng.choice(['array','slice','dynamic']);items=[make(depth+1) for _ in range(rng.randrange(4))]
            return dict(kind='sequence',values=items,storage=storage,nil=False,**(dict(capacity=len(items)+2) if storage=='dynamic' else {}))
        self.compare([dict(operation='reconstruct',packet=dict(thor_value=1,value=make())) for _ in range(200)])

    def test_numeric_validation_precedes_missing_backing_diagnostic(self):
        cases=[dict(kind='big_integer',value='01'),dict(kind='big_integer',value='+2'),dict(kind='big_rational',numerator='0',denominator='2'),dict(kind='big_rational',numerator='2',denominator='4'),dict(kind='big_integer',value='2'),dict(kind='big_rational',numerator='2',denominator='3')]
        self.compare([dict(operation=operation,packet=dict(thor_value=1,value=value)) for operation in ['reconstruct','without_backing'] for value in cases])

    def roundtrip(self,packet,typ):
        response=self.compare([dict(operation='reconstruct',packet=dict(thor_value=1,value=packet))])[0]['ok']
        code='''package main
import "thor:big"
import "thor:pure"
import "thor:eval"
import meta "base:intrinsics"
import "core:mem"
'''+''.join(response['declarations'].values())+'\nT::'+typ+'\nconstruct::proc()->T{\n'+response['materialize']+'''\n}
main::proc(){
 tracker:mem.Tracking_Allocator
 mem.tracking_allocator_init(&tracker,context.allocator)
 context.allocator=mem.tracking_allocator(&tracker)
 value:=construct()
 eval.emit(value)
 pure.destroy(value)
 if len(tracker.allocation_map)!=0||len(tracker.bad_free_array)!=0{panic("numeric reconstruction ownership")}
 mem.tracking_allocator_destroy(&tracker)
}
'''
        with tempfile.TemporaryDirectory(prefix='thor-rust-big-values-') as directory:
            source=Path(directory)/'main.odin';source.write_text(code)
            for flags in [[],['-o:speed','-disable-assert','-no-bounds-check','-no-type-assert']]:
                with self.subTest(flags=flags):
                    result=subprocess.run(['odin','run',str(source),'-file','-collection:thor='+str(ROOT/'runtime'),*flags],capture_output=True,text=True,timeout=60)
                    self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                    self.assertEqual(result.stderr,'')
                    self.assertEqual(json.loads(result.stdout),dict(thor_value=1,value=packet))

    def test_native_checked_and_unchecked_scalar_backing_reconstruction(self):
        for packet in self.backing_packets()[:15]:
            with self.subTest(packet=packet):
                self.roundtrip(packet,'big.Integer' if packet['kind']=='big_integer' else 'big.Rational')

    def test_native_aggregate_reconstruction_keeps_independent_owners(self):
        integer=bigvalues.packet('big_integer',1<<250)
        rational=bigvalues.packet('big_rational',Fraction(-7,13))
        packet=dict(kind='record',fields=[['large',integer],['ratio',rational],['items',dict(kind='sequence',values=[integer,integer],storage='slice',nil=False)],['choice',dict(kind='union',variant=1,value=rational)]])
        self.roundtrip(packet,'struct{large:big.Integer,ratio:big.Rational,items:[]big.Integer,choice:union{int,big.Rational}}')


if __name__=='__main__':unittest.main()
