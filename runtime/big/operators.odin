package thor_big

import "base:runtime"
import backend "core:math/big"

@(private="package")
canonicalize_integer :: proc "contextless"(value:^Integer) {
    for value._value.used>0 && value._value.digit[value._value.used-1]==0 {value._value.used-=1}
    if value._value.used==0 {value._value.sign=.Zero_or_Positive}
}

// The native bridge never changes operands. Every numeric result owns storage.
// All try_ paths destroy partial results and leave a zero result on failure.
@(private="package")
raw_integer_binary :: proc(a, b: Integer, $op: string, allocator := context.allocator) -> (result: Integer, err: Error) {
    context.allocator = allocator
    x, y := a._value, b._value
    // Native big APIs initialize empty *input* headers. These local zero
    // headers can therefore acquire scratch allocations that are not owners
    // in the caller. Release those without touching borrowed nonempty inputs.
    defer if raw_data(a._value.digit)==nil {release_raw(&x)}
    defer if raw_data(b._value.digit)==nil {release_raw(&y)}
    when op == "+" { err = backend_error(backend.int_add(&result._value,&x,&y,allocator)) }
    else when op == "-" { err = backend_error(backend.int_sub(&result._value,&x,&y,allocator)) }
    else when op == "*" { err = backend_error(backend.int_mul(&result._value,&x,&y,allocator)) }
    else when op == "/" || op == "%" || op == "%%" {
        if is_zero(b) { return {}, .Division_by_Zero }
        when op == "/" { err = backend_error(backend.int_div(&result._value,&x,&y,allocator)) }
        else {
            err = backend_error(backend.int_divmod(nil,&result._value,&x,&y,allocator))
            if err != .None { return }
            when op == "%%" {
                if !is_zero(result) && result._value.sign != y.sign {
                    err = backend_error(backend.int_add(&result._value,&result._value,&y,allocator))
                }
            }
        }
    } else when op == "&" { err = backend_error(backend.int_bit_and(&result._value,&x,&y,allocator)) }
    else when op == "|" { err = backend_error(backend.int_bit_or(&result._value,&x,&y,allocator)) }
    else when op == "~" { err = backend_error(backend.int_bit_xor(&result._value,&x,&y,allocator)) }
    else when op == "&~" {
        complement: Integer
        defer destroy(&complement)
        err = backend_error(backend.int_bit_complement(&complement._value,&y,allocator))
        if err == .None { err = backend_error(backend.int_bit_and(&result._value,&x,&complement._value,allocator)) }
    } else when op == "<<" || op == ">>" {
        if !is_zero(b) && y.sign == .Negative { return {}, .Invalid_Shift }
        count, native_err := backend.int_get(&y,int)
        if native_err != .None {
            when op == ">>" {
                if !is_zero(a) && x.sign == .Negative { return try_from_integer(-1,allocator) }
                return
            } else { return {}, .Integer_Overflow }
        }
        when op == "<<" { err = backend_error(backend.int_shl(&result._value,&x,count,allocator)) }
        else {
            // Arithmetic right shift, including negative values. Avoid the
            // installed backend's signed-shift routine (its final subtraction
            // uses the original source rather than the shifted intermediate).
            remainder: Integer
            defer destroy(&remainder)
            negative := !is_zero(a) && x.sign == .Negative
            if count >= a._value.used*size_of(backend.DIGIT)*8 {
                if negative {return try_from_integer(-1,allocator)}
                return
            }
            x.sign = .Zero_or_Positive
            err = backend_error(backend.int_shrmod(&result._value,&remainder._value,&x,count,allocator))
            canonicalize_integer(&result)
            canonicalize_integer(&remainder)
            if err == .None && negative {
                if !is_zero(remainder) { err = backend_error(backend.int_add_digit(&result._value,&result._value,1,allocator)) }
                if !is_zero(result) { result._value.sign = .Negative }
            }
        }
    } else when op == "**" { return try_integer_power(a,b,allocator) }
    else { #assert(false,"Unsupported Integer operator") }
    return
}

@(private="package")
raw_integer_power :: proc(base, exponent: Integer, allocator := context.allocator) -> (result: Integer, err: Error) {
    if !is_zero(exponent) && exponent._value.sign == .Negative { return {}, .Invalid_Exponent }
    factor, remaining: Integer
    defer destroy(&factor)
    defer destroy(&remaining)
    result, err = try_from_integer(1,allocator)
    if err != .None { return }
    if is_zero(exponent) { return }
    factor, err = try_clone(base,allocator)
    if err != .None { return }
    remaining, err = try_clone(exponent,allocator)
    if err != .None { return }
    for !is_zero(remaining) {
        if remaining._value.digit[0] & 1 != 0 {
            next: Integer
            next, err = try_integer_binary(result,factor,"*",allocator)
            if err != .None { return }
            destroy(&result)
            result = next
        }
        err = backend_error(backend.int_shr(&remaining._value,&remaining._value,1,allocator))
        if err != .None { return }
        if !is_zero(remaining) {
            next: Integer
            next, err = try_integer_binary(factor,factor,"*",allocator)
            if err != .None { return }
            destroy(&factor)
            factor = next
        }
    }
    return
}

try_integer_binary :: proc(a,b:Integer,$op:string,allocator:=context.allocator)->(result:Integer,err:Error) {
    result,err=raw_integer_binary(a,b,op,allocator)
    if err!=.None {destroy(&result)} else {canonicalize_integer(&result)}
    return
}
try_integer_power :: proc(a,b:Integer,allocator:=context.allocator)->(result:Integer,err:Error) {
    result,err=raw_integer_power(a,b,allocator)
    if err!=.None {destroy(&result)} else {canonicalize_integer(&result)}
    return
}

// Comparison does not require initializing or allocating zero operands.
compare_integer :: proc "contextless"(a,b:Integer)->int {
    negative_a := !is_zero(a) && a._value.sign == .Negative
    negative_b := !is_zero(b) && b._value.sign == .Negative
    if negative_a != negative_b { return -1 if negative_a else 1 }
    result := 0
    if a._value.used != b._value.used { result = -1 if a._value.used < b._value.used else 1 }
    else {
        for i := a._value.used-1; i>=0; i-=1 {
            if a._value.digit[i] != b._value.digit[i] {
                result = -1 if a._value.digit[i] < b._value.digit[i] else 1
                break
            }
        }
    }
    return -result if negative_a else result
}

try_rational_binary :: proc(a,b:Rational,$op:string,allocator:=context.allocator)->(result:Rational,err:Error) {
    context.allocator=allocator
    when op=="**" {
        if !is_zero(b) && !is_one(b._denominator) { return {},.Invalid_Exponent }
        exponent:=b._numerator
        invert:=!is_zero(exponent) && exponent._value.sign==.Negative
        exponent._value.sign=.Zero_or_Positive
        if invert && is_zero(a) { return {},.Division_by_Zero }
        n,d,one:Integer
        defer destroy(&n);defer destroy(&d);defer destroy(&one)
        denominator:=a._denominator
        if is_zero(a) { one,err=try_from_integer(1,allocator);if err!=.None{return};denominator=one }
        n,err=try_integer_power(a._numerator,exponent,allocator);if err!=.None{return}
        d,err=try_integer_power(denominator,exponent,allocator);if err!=.None{return}
        if invert {n,d=d,n}
        result,err=normalize_owned(n,d,allocator)
        n,d={},{}
        return
    } else when op=="%" || op=="%%" {
        quotient:Rational
        whole:Integer
        product,remainder:Rational
        defer destroy(&quotient);defer destroy(&whole);defer destroy(&product);defer destroy(&remainder)
        quotient,err=try_rational_binary(a,b,"/",allocator);if err!=.None{return}
        if !is_zero(quotient) {
            whole,err=try_integer_binary(quotient._numerator,quotient._denominator,"/",allocator)
            if err!=.None{return}
        }
        factor:Rational
        defer destroy(&factor)
        factor,err=try_rational_from_integer(whole,allocator);if err!=.None{return}
        product,err=try_rational_binary(factor,b,"*",allocator);if err!=.None{return}
        remainder,err=try_rational_binary(a,product,"-",allocator);if err!=.None{return}
        when op=="%%" {
            if !is_zero(remainder) && remainder._numerator._value.sign!=b._numerator._value.sign {
                return try_rational_binary(remainder,b,"+",allocator)
            }
        }
        result,remainder=remainder,{}
        return
    } else {
        #assert(op=="+" || op=="-" || op=="*" || op=="/","Rational does not support bitwise operators or shifts")
        when op=="/" {if is_zero(b){return {},.Division_by_Zero}}
        an,ad,bn,bd:=a._numerator,a._denominator,b._numerator,b._denominator
        one,n,d,left,right:Integer
        defer destroy(&one);defer destroy(&n);defer destroy(&d);defer destroy(&left);defer destroy(&right)
        if is_zero(ad) || is_zero(bd) {
            one,err=try_from_integer(1,allocator);if err!=.None{return}
            if is_zero(ad){ad=one};if is_zero(bd){bd=one}
        }
        when op=="+" || op=="-" {
            left,err=try_integer_binary(an,bd,"*",allocator);if err!=.None{return}
            right,err=try_integer_binary(bn,ad,"*",allocator);if err!=.None{return}
            n,err=try_integer_binary(left,right,op,allocator);if err!=.None{return}
            d,err=try_integer_binary(ad,bd,"*",allocator);if err!=.None{return}
        } else when op=="*" {
            n,err=try_integer_binary(an,bn,"*",allocator);if err!=.None{return}
            d,err=try_integer_binary(ad,bd,"*",allocator);if err!=.None{return}
        } else when op=="/" {
            n,err=try_integer_binary(an,bd,"*",allocator);if err!=.None{return}
            d,err=try_integer_binary(ad,bn,"*",allocator);if err!=.None{return}
        }
        result,err=normalize_owned(n,d,allocator)
        n,d={},{}
        return
    }
}

try_binary :: proc(a,b:$T,$op:string,allocator:=context.allocator)->(T,Error) where T==Integer || T==Rational {
    when T==Integer {return try_integer_binary(a,b,op,allocator)}
    else {return try_rational_binary(a,b,op,allocator)}
}
binary :: proc(a,b:$T,$op:string,allocator:=context.allocator,loc:=#caller_location)->T where T==Integer || T==Rational {
    result,err:=try_binary(a,b,op,allocator)
    check(err,loc)
    return result
}
try_unary :: proc(value:$T,$op:string,allocator:=context.allocator)->(result:T,err:Error) where T==Integer || T==Rational {
    when op=="~" {
        #assert(T==Integer,"Rational does not support bitwise complement")
        x:=value._value
        defer if raw_data(value._value.digit)==nil {release_raw(&x)}
        err=backend_error(backend.int_bit_complement(&result._value,&x,allocator))
        if err!=.None{destroy(&result)}
        else {canonicalize_integer(&result)}
    } else {
        #assert(op=="+" || op=="-","Unsupported numeric unary operator")
        result,err=try_clone(value,allocator)
        if err!=.None{return}
        when op=="-" {
            when T==Integer {
                if !is_zero(result){result._value.sign=.Zero_or_Positive if result._value.sign==.Negative else .Negative}
            } else {
                if !is_zero(result){result._numerator._value.sign=.Zero_or_Positive if result._numerator._value.sign==.Negative else .Negative}
            }
        }
    }
    return
}
unary :: proc(value:$T,$op:string,allocator:=context.allocator,loc:=#caller_location)->T where T==Integer || T==Rational {
    result,err:=try_unary(value,op,allocator);check(err,loc);return result
}
try_compare :: proc(a,b:$T,allocator:=context.allocator)->(result:int,err:Error) where T==Integer || T==Rational {
    when T==Integer {return compare_integer(a,b),.None}
    else {
        if equal(a,b){return}
        // Denominators are positive, and logical zero needs no cross product.
        if is_zero(a){return -compare_integer(b._numerator,Integer{}),.None}
        if is_zero(b){return compare_integer(a._numerator,Integer{}),.None}
        left,right:Integer
        defer destroy(&left);defer destroy(&right)
        left,err=try_integer_binary(a._numerator,b._denominator,"*",allocator);if err!=.None{return}
        right,err=try_integer_binary(b._numerator,a._denominator,"*",allocator);if err!=.None{return}
        return compare_integer(left,right),.None
    }
}
compare :: proc(a,b:$T,$op:string,allocator:=context.allocator,loc:=#caller_location)->bool where T==Integer || T==Rational {
    order,err:=try_compare(a,b,allocator);check(err,loc)
    when op=="==" {return order==0}
    else when op=="!=" {return order!=0}
    else when op=="<" {return order<0}
    else when op=="<=" {return order<=0}
    else when op==">" {return order>0}
    else when op==">=" {return order>=0}
    else {#assert(false,"Unsupported comparison operator")}
}
