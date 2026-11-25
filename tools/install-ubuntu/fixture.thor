package main
import "core:os"
import "thor:basic" / print

Person :: struct { name: string, age: int }
twice :: fn(n: int) -> int { return n * 2 }
ANSWER :: #run twice(21)

main :: proc() {
    person :> Person{"Thor", ANSWER}
    print(f"{person.name}: {person.age}")
    err := os.write_entire_file("ran.txt", []u8{'o', 'k'})
    if err != nil { panic("could not write installer test marker") }
}
