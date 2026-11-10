"""Exact constant operations compared with the frozen source evaluator."""
import json
import random
import subprocess
import unittest
from fractions import Fraction
from types import SimpleNamespace
from thorlang import bigvalues
from thorlang.bigconstants import BigConstants, NotConstant
from tests import test_rust_big_values as numeric


class Node:
    def __init__(self,source,kind,operator='',**roles):
        self.source,self.kind,self.data,self.text,self.roles=source,kind,{'op':operator},'',roles

    def role(self,name):return self.roles.get(name)
    def group(self,name):return []


def reference(request):
    # Feed portable packets to the actual reference AST evaluator. Each operand
    # is a remembered constant, so native scalar coercion is tested separately.
    source=SimpleNamespace()
    host=BigConstants(SimpleNamespace(s=source,e=None))
    source.big=SimpleNamespace(constants=host,property=lambda node:None)
    source.resolver=SimpleNamespace(type_of=lambda node:SimpleNamespace(kind=host.payloads[node]['kind']),
                                    membership_untyped=lambda node:False)
    a=Node(source,'Fixture');host.payloads[a]=request['a']
    if request['operation']=='constant_unary':node=Node(source,'Unary',request['operator'],expr=a)
    else:
        b=Node(source,'Fixture');host.payloads[b]=request['b']
        node=Node(source,'Binary',request['operator'],left=a,right=b)
    try:return dict(ok=host.evaluate(node))
    except (ValueError,ZeroDivisionError,OverflowError) as error:return dict(error=str(error))
    except NotConstant:return dict(error='Unsupported constant numeric operation.')


class RustBigConstantTests(unittest.TestCase):
    setUpClass=classmethod(numeric.RustBigValueTests.setUpClass.__func__)

    def compare(self,requests):
        process=subprocess.run([numeric.BINARY],input=''.join(json.dumps(request)+'\n' for request in requests),
                               capture_output=True,text=True,check=True,timeout=60)
        answers=[json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual(len(answers),len(requests))
        for request,answer in zip(requests,answers):
            expected=reference(request)
            self.assertEqual(answer,expected,str(request)[:200])

    def generated(self,kind):
        rng=random.Random(20261110);requests=[]
        for _ in range(100):
            a=Fraction(rng.randrange(-2**200,2**200),rng.randrange(1,2**50) if kind=='big_rational' else 1)
            b=Fraction(rng.randrange(-2**100,2**100),rng.randrange(1,2**30) if kind=='big_rational' else 1)
            pack=lambda number:bigvalues.packet(kind,number)
            for operator in ['+','-','*','/','%','%%','==','!=','<','<=','>','>=']:
                requests.append(dict(operation='constant_binary',operator=operator,a=pack(a),b=pack(b)))
            if kind=='big_integer':
                for operator in ['&','|','~','&~']:
                    requests.append(dict(operation='constant_binary',operator=operator,a=pack(a),b=pack(b)))
                for operator in ['<<','>>']:
                    requests.append(dict(operation='constant_binary',operator=operator,a=pack(a),b=pack(rng.randrange(0,100))))
            for operator in ['+','-']+(['~'] if kind=='big_integer' else []):
                requests.append(dict(operation='constant_unary',operator=operator,a=pack(a)))
            for count in [-3,-1,0,1,3,7]:
                requests.append(dict(operation='constant_binary',operator='**',a=pack(a),b=pack(count)))
        self.compare(requests)

    def test_integer_operations_match_the_reference(self):self.generated('big_integer')
    def test_rational_operations_match_the_reference(self):self.generated('big_rational')

    def test_zero_and_large_shift_and_power_boundaries(self):
        requests=[]
        for kind in ['big_integer','big_rational']:
            for value in [-1,0,1]:
                for operator in ['/','%','%%','<<','>>','**']:
                    if kind=='big_rational' and operator in ['<<','>>']:continue
                    requests.append(dict(operation='constant_binary',operator=operator,a=bigvalues.packet(kind,value),b=bigvalues.packet(kind,0)))
        cases=[(a,10**100,op) for a in [-1,0,1] for op in ['>>','**']]
        cases.extend([(0,10**100,'<<'),(2,8*16*1024*1024+1,'<<'),(2,8*16*1024*1024+1,'**')])
        for a,b,operator in cases:
            requests.append(dict(operation='constant_binary',operator=operator,a=bigvalues.packet('big_integer',a),b=bigvalues.packet('big_integer',b)))
        self.compare(requests)

    def test_signs_type_mismatches_and_fractional_exponents(self):
        requests=[]
        for kind in ['big_integer','big_rational']:
            for a in [-17,-1,0,1,17]:
                for b in [-5,-1,0,1,5]:
                    for operator in ['/','%','%%']:
                        requests.append(dict(operation='constant_binary',operator=operator,a=bigvalues.packet(kind,a),b=bigvalues.packet(kind,b)))
        requests.extend([
            dict(operation='constant_binary',operator='+',a=bigvalues.packet('big_integer',1),b=bigvalues.packet('big_rational',1)),
            dict(operation='constant_binary',operator='**',a=bigvalues.packet('big_rational',2),b=bigvalues.packet('big_rational',Fraction(1,2))),
            dict(operation='constant_binary',operator='<<',a=bigvalues.packet('big_integer',2),b=bigvalues.packet('big_integer',-1)),
        ])
        self.compare(requests)


if __name__=='__main__':unittest.main()
