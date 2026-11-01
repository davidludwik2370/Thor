// Run: thor run examples/big_operators -debug
// Runtime operators. The type/ownership introduction is in examples/big_numbers.
package main
import "core:fmt"
import "thor:big"

show::proc(label:string,value:$T){
    text:=big.to_string(value)
    defer delete(text)
    fmt.println(label,text)
}

add::proc(a,b:$T)->T{return a+b}

main::proc(){
    // A requested Integer context keeps the entire expression exact, instead
    // of overflowing a native intermediate. Inferred native arithmetic is unchanged.
    exact:Integer=123456789012345678901234567890*123456789012345678901234567890
    show("Exact product:",exact)
    delete(exact)

    // Operations create independent results; named operands are not changed.
    a:=Integer(10)
    b:=Integer(20)
    a,b=b,a+1 // Snapshot both old values before replacing either owner.
    assert(a==20&&b==11)
    a+=5
    a*=2
    show("Updated a:",a)
    show("Independent b:",b)
    delete(a)
    delete(b)

    // Integer division truncates toward zero. % keeps the dividend's sign;
    // %% is Odin's ((a % b) + b) % b, so a nonzero result follows the divisor.
    show("-7 / 3:",Integer(-7)/3)
    show("-7 % 3:",Integer(-7)%3)
    show("-7 %% 3:",Integer(-7)%%3)
    show("7 %% -3:",Integer(7)%%-3)

    // Bit operations use infinite two's complement. Right shift extends the
    // sign, so -7 >> 1 is -4. Shift counts must be nonnegative Integers/literals.
    show("Complement:",~Integer(5))
    show("Arithmetic shift:",Integer(-7)>>1)
    show("Xor:",Integer(5)~3)
    show("Power:",Integer(2)**200)

    // Rational contexts coerce untyped numeric operands exactly. A decimal is
    // its source fraction, never a rounded native floating-point value.
    fraction:Rational=1/3+0.25
    show("1/3 + 1/4:",fraction) // 7/12.
    show("Exact remainder:",Rational(7,3)%Rational(1,2))
    show("Negative power:",Rational(2,3)**-3) // 27/8.
    assert(fraction>Rational(1,2))
    delete(fraction)

    // Generics dispatch at specialization; ordinary native calls stay ordinary.
    result:=add(Integer(2),Integer(3))
    show("Generic Integer:",result)
    delete(result)
    assert(add(2,3)==5)

    // Typed native operands must be converted explicitly before big arithmetic.
    native:i64=3
    show("Explicit conversion:",Integer(2)+Integer(native))
    // _=Integer(2)+native    // Compile error: matching operand types required.
    // _=Integer(2)**-3      // Use Rational for negative exponents.
    // _=Rational(1,2)&1     // Rational has no bitwise operations.
    // Exact :: constants are demonstrated in examples/big_comptime.

    fmt.println("All big-number runtime operator examples passed.")
}
