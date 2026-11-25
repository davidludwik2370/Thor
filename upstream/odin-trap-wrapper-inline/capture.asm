; Minimal Windows x64 return-address/stack capture. RCX is Environment*.
bits 64
global capture_impl
section .text
capture_impl:
    mov rax, [rsp]
    mov [rcx], rax
    lea rax, [rsp+8]
    mov [rcx+8], rax
    xor eax, eax
    ret
