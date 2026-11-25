# Stack recovery through an uninlined `#force_inline` wrapper

Native Windows x64 Odin `dev-2026-11-nightly:9a69916`, MSVC ABI, 2026-11-23.
No Thor code or compiler changes are needed for the reproduction.

`main.odin` captures a return address and stack pointer through a wrapper marked
`#force_inline`. A private vectored exception handler resumes at that address with
return value 1. This requires the capture to reside in the still-live caller's
frame. If the wrapper is not inlined, its frame has already returned and its
return-address slot can be overwritten by the later call. Such a wrapper is not
a safe general replacement for a returns-twice primitive.

| Native command | Output | Exit |
| --- | --- | --- |
| `odin run main.odin -file -thread-count:1 -o:none` | `0` | 1 |
| same with `-o:minimal` | `1` | 0 |
| same with `-o:speed` | `1` | 0 |

Replacing the wrapper declaration with `capture :: capture_impl` passes all
three configurations: the assembly captures the actual caller's live frame.
The installed Odin compiler and third-party source trees were not patched.

This reduced a Tina integration-test mismatch. Its
`test_windows_veh_exception_recovery` passes in the original project's default
native build, but fails after lowering at the same optimization level. Native
disassembly shows a direct `_os_trap_save` call in the passing build and an
out-of-line `os_trap_save` wrapper call in the failing build. The wrapper's source
was unchanged. At `-o:none`, both original and lowered programs fail; at
`-o:speed`, both pass. Thus the tested stack-recovery technique depends on actual
inlining, which the observed `#force_inline` configurations do not guarantee.
This remains an upstream compatibility limitation, not a passing default
integration test. The precise inliner threshold that changes the default
project's result has not been isolated.

Raw command results and the four native disassembly logs are retained under
`integration-projects/results/windows-2026-11-23/` and in the remote validation
run's `trap-wrapper-repro` and `tina-controls` directories. The minimal source and
assembly in this directory are independent reproductions.
