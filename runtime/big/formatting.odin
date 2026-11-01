// Exact decimal conversion for Thor interpolation. No floating-point round trip.
package thor_big

import backend "core:math/big"
import "core:fmt"
import "core:strings"

// Caller owns the returned magnitude. Sign/prefix/padding are handled by format.
format_radix :: proc(value:Integer, radix:int, upper:bool, allocator:=context.allocator)->string {
    if is_zero(value) {return strings.clone("0",allocator)}
    assert(radix>=2 && radix<=16)
    source:=value._value; source.sign=.Zero_or_Positive
    assert(source.used<=(max(int)-1)/(size_of(backend.DIGIT)*8),"format size overflow")
    size:=source.used*size_of(backend.DIGIT)*8+1
    buffer:=make([]u8,size,allocator);defer delete(buffer,allocator)
    written,err:=backend.int_itoa_raw(&source,i8(radix),buffer,size)
    check(backend_error(err),#location())
    start:=0
    for start<written-1 && buffer[start]=='0' {start+=1}
    for &c in buffer[start:written] {
        if upper {if c>='a'&&c<='f'{c-=32}}
        else {if c>='A'&&c<='F'{c+=32}}
    }
    return strings.clone(string(buffer[start:written]),allocator)
}

is_negative :: proc "contextless"(value:$T)->bool where T==Integer || T==Rational {
    when T==Integer {return !is_zero(value) && value._value.sign==.Negative}
    else {return !is_zero(value) && value._numerator._value.sign==.Negative}
}

@(private="package")
format_ten :: proc(exponent:int)->Integer {
    result,err:=power_of_ten(exponent,context.allocator)
    check(err,#location())
    return result
}

// floor(log10(n/d)), computed with integer comparisons, including values close
// to powers of ten. Inputs are positive; zero is handled by the caller.
@(private="package")
format_exponent :: proc(n,d:Integer)->int {
    ns,ds:=to_string(n),to_string(d);defer delete(ns);defer delete(ds)
    k:=len(ns)-len(ds)
    power:=format_ten(abs(k));defer destroy(&power)
    if k>=0 {
        scaled:=binary(d,power,"*");defer destroy(&scaled)
        if compare_integer(n,scaled)<0 {k-=1}
    } else {
        scaled:=binary(n,power,"*");defer destroy(&scaled)
        if compare_integer(scaled,d)<0 {k-=1}
    }
    return k
}

// round(n/d * 10^places), nearest with ties to even. Every scratch owner is
// released, including the initialized-zero headers used by the Odin backend.
@(private="package")
format_round :: proc(n,d:Integer,places:int)->Integer {
    power:=format_ten(abs(places));defer destroy(&power)
    x,y:=clone(n),clone(d);defer destroy(&x);defer destroy(&y)
    if places>=0 {next:=binary(x,power,"*");destroy(&x);x=next}
    else {next:=binary(y,power,"*");destroy(&y);y=next}
    q,r:Integer;defer destroy(&r)
    err:=backend.int_divmod(&q._value,&r._value,&x._value,&y._value,context.allocator)
    check(backend_error(err),#location())
    canonicalize_integer(&q);canonicalize_integer(&r)
    two:=from_integer(2);defer destroy(&two)
    twice:=binary(r,two,"*");defer destroy(&twice)
    cmp:=compare_integer(twice,y)
    if cmp>0 || cmp==0 && !is_zero(q) && q._value.digit[0]&1!=0 {
        err=backend.int_add_digit(&q._value,&q._value,1,context.allocator)
        check(backend_error(err),#location())
    }
    return q
}

@(private="package")
format_fixed_digits :: proc(b:^strings.Builder,digits:string,places:int) {
    if places<=0 {
        strings.write_string(b,digits)
        for _ in 0..<-places {strings.write_byte(b,'0')}
    } else if len(digits)>places {
        strings.write_string(b,digits[:len(digits)-places])
        strings.write_byte(b,'.');strings.write_string(b,digits[len(digits)-places:])
    } else {
        strings.write_string(b,"0.")
        for _ in 0..<places-len(digits) {strings.write_byte(b,'0')}
        strings.write_string(b,digits)
    }
}

// f/F, e/E, g/G and percentage share one exact rounding path. Rational g/G
// requires precision at the public generic entry, not an arbitrary digit cap.
format_decimal :: proc(value:$T,verb:rune,precision:int=-1,allocator:=context.allocator)->string where T==Integer || T==Rational {
    context.allocator=allocator
    n,d:Integer
    when T==Integer {n=clone(value);d=from_integer(1)}
    else {n=numerator(value);d=denominator(value)}
    defer destroy(&n);defer destroy(&d)
    negative:=is_negative(n);n._value.sign=.Zero_or_Positive
    if verb=='%' {hundred:=from_integer(100);defer destroy(&hundred);next:=binary(n,hundred,"*");destroy(&n);n=next}
    p:=precision
    if p<0 {p=6 if verb=='e'||verb=='E' else 3}
    assert(p<max(int)-400,"format precision overflow")
    k:=0
    if !is_zero(n) {k=format_exponent(n,d)}
    scientific:=verb=='e'||verb=='E'
    general:=verb=='g'||verb=='G'
    places:=p
    if scientific {places=p-k}
    if general {
        // Bare Integer g is exact, using Odin's shortest-style threshold.
        if precision<0 {
            digits:=to_string(n);defer delete(digits)
            p=len(digits)
        } else {p=max(1,p)}
        places=p-1-k
    }
    q:=format_round(n,d,places);defer destroy(&q)
    digit_owner:=to_string(q);defer delete(digit_owner)
    digits:=digit_owner
    if scientific || general {
        significant:=p+1 if scientific else p
        if len(digits)>significant {k+=1;places-=1;digits=digits[:len(digits)-1]}
        if general {scientific=k< -4 || k>=(6 if precision<0 else p)}
    }
    b:strings.Builder;strings.builder_init(&b);defer strings.builder_destroy(&b)
    if negative {strings.write_byte(&b,'-')}
    if scientific {
        strings.write_byte(&b,digits[0])
        rest:=digits[1:]
        if !general && len(rest)<p {
            strings.write_byte(&b,'.');strings.write_string(&b,rest)
            for _ in len(rest)..<p {strings.write_byte(&b,'0')}
        }
        if general {for len(rest)>0 && rest[len(rest)-1]=='0' {rest=rest[:len(rest)-1]}}
        if len(rest)>0 && (general || len(rest)>=p) {strings.write_byte(&b,'.');strings.write_string(&b,rest)}
        strings.write_byte(&b,'E' if verb=='E'||verb=='G' else 'e')
        fmt.sbprintf(&b,"%s%02d","-" if k<0 else "+",abs(k))
    } else {
        format_fixed_digits(&b,digits,places)
        if general && places>0 {
            for len(b.buf)>0 && b.buf[len(b.buf)-1]=='0' {pop(&b.buf)}
            if len(b.buf)>0 && b.buf[len(b.buf)-1]=='.' {pop(&b.buf)}
        }
    }
    if verb=='%' {strings.write_byte(&b,'%')}
    return strings.clone(strings.to_string(b))
}
