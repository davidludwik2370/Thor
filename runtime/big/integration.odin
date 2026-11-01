package thor_big

// Native fallback preserves fields with the same spelling on ordinary records.
compiler_numerator :: proc(value:$T,$Result:typeid)->Result {
    when T==Rational {return numerator(value)}
    else when T==^Rational {return numerator(value^)}
    else {return compiler_clone(value.numerator)}
}
compiler_denominator :: proc(value:$T,$Result:typeid)->Result {
    when T==Rational {return denominator(value)}
    else when T==^Rational {return denominator(value^)}
    else {return compiler_clone(value.denominator)}
}
compiler_equal :: proc "contextless"(a,b:$T)->bool {
    when T==Integer || T==Rational {return equal(a,b)}
    else {return a==b}
}

// The compiler clones case operands before passing them here. Consume those
// owners after comparison; the switch selector is a borrowed stable snapshot.
compiler_switch_case :: proc(value,match:$T)->bool where T==Integer || T==Rational {
    defer compiler_destroy_value(match)
    return compare(value,match,"==")
}
compiler_switch_range :: proc(value,low,high:$T,$inclusive:bool)->bool where T==Integer || T==Rational {
    defer compiler_destroy_value(low)
    defer compiler_destroy_value(high)
    when inclusive {return compare(value,low,">=") && compare(value,high,"<=")}
    else {return compare(value,low,">=") && compare(value,high,"<")}
}

// Inputs are borrowed. Each returned number is an independent owner.
compiler_abs :: proc(value:$T)->T where T==Integer || T==Rational {
    when T==Integer {return unary(value,"-") if value._value.sign==.Negative else clone(value)}
    else {return unary(value,"-") if value._numerator._value.sign==.Negative else clone(value)}
}
compiler_extreme :: proc(a,b:$T,$name:string)->T where T==Integer || T==Rational {
    return clone(a) if compare(a,b,"<" if name=="min" else ">") else clone(b)
}
compiler_clamp :: proc(value,low,high:$T)->T where T==Integer || T==Rational {
    return clone(low) if compare(value,low,"<") else clone(high) if compare(value,high,">") else clone(value)
}

sum :: proc(values:[]$T)->T where T==Integer || T==Rational {
    total:T
    for value in values {
        next:=binary(total,value,"+")
        destroy(&total)
        total=next
    }
    return total
}
ascending :: proc(first:$T,rest:..T)->bool where T==Integer || T==Rational {
    previous:=first
    for value in rest {if compare(previous,value,">"){return false};previous=value}
    return true
}
descending :: proc(first:$T,rest:..T)->bool where T==Integer || T==Rational {
    previous:=first
    for value in rest {if compare(previous,value,"<"){return false};previous=value}
    return true
}
