"""Runtime operators, independent Python/Fraction oracles, and lifetimes."""
from fractions import Fraction
import json
import random
import unittest
from tests import test_big_language as language


class BigOperatorTests(unittest.TestCase):
    invoke=language.BigLanguageTests.invoke
    program=language.BigLanguageTests.program
    run_big=language.BigLanguageTests.run_big

    def test_integer_arithmetic_bitwise_division_and_remainder_oracles(self):
        rng=random.Random(0x54484f52)
        checks=[]
        for index in range(30):
            bits=4096 if index==0 else 200
            a=rng.randrange(-2**bits,2**bits)
            b=rng.choice([-1,1])*rng.randrange(1,2**(bits-30))
            q=abs(a)//abs(b)*(-1 if (a<0)!=(b<0) else 1)
            results={'+':a+b,'-':a-b,'*':a*b,'/':q,'%':a-q*b,'%%':a%b,
                     '&':a&b,'|':a|b,'~':a^b,'&~':a&~b}
            checks.append('{a:=Integer('+str(a)+');b:=Integer('+str(b)+')')
            for op,value in results.items():checks.append('expect(a '+op+' b,'+json.dumps(str(value))+')')
            checks.append('assert((a<b)=='+str(a<b).lower()+');assert((a>=b)=='+str(a>=b).lower()+')')
            checks.append('expect(-a,'+json.dumps(str(-a))+');expect(~a,'+json.dumps(str(~a))+')')
            checks.append('delete(a);delete(b)}')
        self.run_big('\n'.join(checks))

    def test_shifts_powers_zero_and_negative_operands(self):
        checks=[]
        for a in [-2**130-1,-129,-7,-1,0,1,7,2**160+3]:
            for count in [0,1,2,63,64,130,300]:
                checks.append('expect(Integer('+str(a)+') >> '+str(count)+','+json.dumps(str(a>>count))+')')
                checks.append('expect(Integer('+str(a)+') << '+str(count)+','+json.dumps(str(a<<count))+')')
        for a in [-5,-1,0,1,2,17]:
            for power in [0,1,2,5,20]:
                checks.append('expect(Integer('+str(a)+') ** '+str(power)+','+json.dumps(str(a**power))+')')
        checks.append('expect(Integer(-7)>>Integer(999999999999999999999999999999),"-1")')
        self.run_big('\n'.join(checks))

    def test_rational_exact_arithmetic_comparisons_and_remainders(self):
        rng=random.Random(9913)
        checks=[]
        for _ in range(20):
            a=Fraction(rng.randrange(-10**20,10**20),rng.randrange(1,1000))
            b=Fraction(rng.choice([-1,1])*rng.randrange(1,10**15),rng.randrange(1,1000))
            q=int(a/b)
            rem=a-q*b
            mod=rem if rem==0 or (rem<0)==(b<0) else rem+b
            results={'+':a+b,'-':a-b,'*':a*b,'/':a/b,'%':rem,'%%':mod}
            checks.append('{a:=Rational('+str(a.numerator)+','+str(a.denominator)+');b:=Rational('+str(b.numerator)+','+str(b.denominator)+')')
            for op,result in results.items():
                checks.append('expect(a '+op+' b,'+json.dumps(str(result.numerator)+'/'+str(result.denominator))+')')
            checks.append('assert((a<b)=='+str(a<b).lower()+');assert((a>=b)=='+str(a>=b).lower()+')')
            checks.append('delete(a);delete(b)}')
        self.run_big('\n'.join(checks))

    def test_contextual_literals_updates_swaps_and_aliased_side_effects(self):
        self.run_big('''
a:Integer=123456789012345678901234567890+5*2
expect(a,"123456789012345678901234567900")
b:=Integer(2)
a,b=b,a+1
expect(a,"2");expect(b,"123456789012345678901234567901")
a+=3;a*=2;a/=2;a-=1;expect(a,"4")
a%=3;expect(a,"1");a<<=4;a>>=2;expect(a,"4")
a|=2;a&=3;a~=1;expect(a,"3")
values:=make(map[string]Integer);values["x"]=a
values["x"]+=2;expect(values["x"],"5")
delete(values["x"]);delete(values)
delete(a);delete(b)
x:=Integer(7)
y:=x+replace(&x)
expect(x,"100");expect(y,"9")
delete(x);delete(y)
''','replace::proc(value:^Integer)->Integer{value^=Integer(100);return Integer(2)}\n')

    def test_generic_operators_preserve_native_and_set_instantiations(self):
        self.run_big('''
a:=Integer(2);b:=Integer(3)
c:=add(a,b);expect(c,"5");assert(less(a,b))
delete(a);delete(b);delete(c)
assert(add(2,3)==5&&less(2,3))
one:=set[int]{1};two:=set[int]{2};three:=combine(one,two)
assert(len(three)==2);delete(one);delete(two);delete(three)
r:=Rational(2,3)**-3;expect(r,"27/8");delete(r)
s: Rational=0.1+0.2;expect(s,"3/10");delete(s)
''','add::proc(a,b:$T)->T{return a+b}\nless::proc(a,b:$T)->bool{return a<b}\ncombine::proc(a,b:$T)->T{return a|b}\n')

    def test_untyped_constants_and_constructor_expression_contexts_are_exact(self):
        self.run_big('''
a:=Integer(2)*COUNT
b:Rational=DECIMAL+0.2
c:=Integer(2**200)
r:=Rational(1+2,2*3)
expect(a,"6");expect(b,"3/10");expect(c,"1606938044258990275541962092341162602522202993782792835301376");expect(r,"1/2")
delete(a);delete(b);delete(c);delete(r)
''','COUNT::3\nDECIMAL::0.1\n')

    def test_invalid_operators_and_typed_native_mixing_are_errors(self):
        for expression,message in [
            ('Integer(2)+i64(3)','Cannot'),('i64(3)+Integer(2)','Cannot'),
            ('Integer(2)+Rational(3)','Cannot'),('Integer(2)==f64(2)','Cannot'),
            ('Rational(1,2)&Rational(3,4)','bitwise'),('~Rational(1,2)','bitwise'),
            ('Integer(2)**-3','nonnegative exponent'),('Integer(2)<<-1','nonnegative'),
            ('Integer(2)/0','divisor must not be zero'),
        ]:
            with self.subTest(expression=expression):
                self.invoke(self.program('_='+expression),command='check',fail=message)

    def test_dynamic_domain_errors_panic_at_runtime(self):
        for body,message in [
            ('a:=Integer(2);b:=Integer(-1);_=a**b','nonnegative'),
            ('a:=Integer(2);b:=Integer(-1);_=a<<b','nonnegative'),
            ('a:=Rational(2);b:=Rational(1,2);_=a**b','whole exponents'),
            ('a:=Integer(2);b:Integer;_=a/b','must not be zero'),
        ]:
            self.invoke(self.program(body),fail=message)
