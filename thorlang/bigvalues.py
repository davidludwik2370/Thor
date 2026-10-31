"""Portable, canonical arbitrary-precision contents (never native addresses)."""
from decimal import Decimal
from fractions import Fraction
import re


def decimal_integer(text):
    negative=text.startswith('-')
    digits=text[1:] if negative else text
    result=0
    for offset in range(0,len(digits),1000):
        chunk=digits[offset:offset+1000]
        result=result*10**len(chunk)+int(chunk)
    return -result if negative else result


def decimal_text(number):
    negative=number<0
    number=abs(number)
    chunks=[]
    while number:
        number,remainder=divmod(number,10**1000)
        chunks.append(str(remainder) if not number else str(remainder).zfill(1000))
    return ('-' if negative else '')+(''.join(reversed(chunks)) if chunks else '0')


def literal(text):
    text=text.replace('_','')
    sign=-1 if text.startswith('-') else 1
    digits=text.lstrip('+-')
    if len(digits)>2 and digits[:2].lower() in {'0b','0o','0d','0z','0x'}:
        base={'b':2,'o':8,'d':10,'z':12,'x':16}[digits[1].lower()]
        value=0
        for c in digits[2:].lower():value=value*base+int(c,36)
        return sign*value
    return Fraction(Decimal(text))


def packet(kind,number):
    if kind=='big_integer':
        if isinstance(number,Fraction) and number.denominator!=1:raise ValueError('An Integer constant must represent a whole number exactly.')
        return dict(kind=kind,value=decimal_text(int(number)))
    number=Fraction(number)
    return dict(kind='big_rational',numerator=decimal_text(number.numerator),denominator=decimal_text(number.denominator))


def number(value):
    def integer(text,positive=False):
        pattern=r'[1-9][0-9]*' if positive else r'(?:0|-?[1-9][0-9]*)'
        if not isinstance(text,str) or not re.fullmatch(pattern,text):raise ValueError('Invalid canonical big-number digits.')
        return decimal_integer(text)
    if value['kind']=='big_integer':return integer(value.get('value'))
    n=integer(value.get('numerator'));d=integer(value.get('denominator'),True)
    result=Fraction(n,d)
    if result.numerator!=n or result.denominator!=d:raise ValueError('A Rational result must be reduced with a positive denominator.')
    return result


def contains(value):
    kind=value.get('kind')
    if kind in {'big_integer','big_rational'}:return True
    if kind=='specialized':return contains(value['value'])
    if kind in {'sequence','bitset'}:return any(contains(v) for v in value['values'])
    if kind in {'record','enum_array'}:return any(contains(v) for _,v in value['fields'])
    if kind=='union' and value['variant']>=0:return contains(value['value'])
    return False
