#+build windows
package main

import win "core:sys/windows"
import "core:fmt"
import "core:os"

when ODIN_ARCH != .amd64 { #panic("This reproduction requires Windows x64") }
Environment :: struct {instruction, stack: u64}
environment: Environment
foreign import capture_asm "capture.asm"
foreign capture_asm {capture_impl :: proc "c"(env:^Environment)->i32 ---}

// This wrapper is only valid if it is actually inlined: the recovery address
// must be in main's live frame, rather than the wrapper's returned frame.
capture :: #force_inline proc "contextless"(env:^Environment)->i32 {
    return capture_impl(env)
}

recover :: proc "system"(info:^win.EXCEPTION_POINTERS)->win.LONG {
    info.ContextRecord.Rip=environment.instruction
    info.ContextRecord.Rsp=environment.stack
    info.ContextRecord.Rax=1
    return win.EXCEPTION_CONTINUE_EXECUTION
}
main :: proc() {
    handler:=win.AddVectoredExceptionHandler(1,recover)
    if handler==nil {os.exit(2)}
    defer win.RemoveVectoredExceptionHandler(handler)
    result:=capture(&environment)
    if result==0 {win.RaiseException(win.EXCEPTION_ACCESS_VIOLATION,0,0,nil)}
    fmt.println(result)
    if result!=1 {os.exit(1)}
}
