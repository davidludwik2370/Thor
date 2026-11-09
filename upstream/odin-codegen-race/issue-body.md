## Context

Tested at commit `8169255b97b85d6a08691d99764f51fb01f92b5a`.

```text
Odin:    dev-2026-10:8169255b9
OS:      macOS Sequoia 15.6.0 (build 24G84, kernel 24.6.0)
CPU:     Apple M1 Pro
RAM:     32768 MiB
Backend: LLVM 22.1.7
```

The race reproduces when compiling plain Odin; no external transpiler is needed.
ThreadSanitizer here instruments the C++ compiler executable, not the Odin
program being compiled.

## Expected behavior

Parallel code-generation workers should safely publish and read cached entity
names, including both the `String` header and its underlying bytes.

## Current behavior

C++ ThreadSanitizer reports concurrent reads/writes of
`Entity::TypeName.ir_mangled_name` in `lb_get_entity_name`. Workers generating
different LLVM modules share the same entity. The cache is a plain two-word
`String`, initialized/read without synchronization.

This was investigated after an intermittent compiler SIGSEGV in `alloc_cstring`
called by `lb_type_internal`. The crash record shows a null source string pointer
with length 26, and a non-null destination allocation. That is consistent with
unsafe cached-name publication. A batch of 2,402 stock-compiler compilations
completed successfully, but a later full regression run repeated the same
SIGSEGV stack/offsets with a null source pointer and length 30 (crash record
`odin-2026-10-29-065118.ips`). That failing test passed on isolated retry. The
independently reproducible finding is the data race below.

A November 9 regression run encountered the same initial stack offsets with a
null source pointer and length 29. Its isolated retry and clean full rerun
passed. The extracted record is `evidence/recurrence-2026-11-09.txt`; it does not
independently identify the particular concurrent write.

## Steps to reproduce

1. In a separate Odin checkout at the commit above, build the compiler with C++
   ThreadSanitizer. On the tested macOS/Homebrew setup:

   ```sh
   LLVM_CONFIG=/opt/homebrew/opt/llvm/bin/llvm-config \
   CXXFLAGS='-O1 -g -fsanitize=thread -fno-omit-frame-pointer' \
   LDFLAGS='-fsanitize=thread' ./build_odin.sh debug
   ```

   The build script runs a demo after building the C++ executable; sanitizer
   reports from that demo can make the script return failure even when the
   compiler executable was built successfully.

2. Save the following as `main.odin` in a separate directory:

   ```odin
   package main

   import "core:fmt"

   main :: proc() {
       fmt.println("hello")
   }
   ```

3. Compile it with the instrumented compiler's normal parallel-module backend.
   Set `ODIN_ROOT` to the matching Odin SDK checkout if the executable is stored
   elsewhere. Use a fresh output directory:

   ```sh
   codegen_case=$(mktemp -d)
   TSAN_OPTIONS='halt_on_error=0:history_size=7' \
     /path/to/instrumented/odin build /path/to/main.odin -file \
     -build-mode:llvm-ir -out:"$codegen_case" -json-errors
   ```

   Continuing after earlier TSan reports is necessary to reach code generation.
   TSan may terminate the compiler after reporting warnings; that termination is
   distinct from the original stock-compiler SIGSEGV.

## Failure logs

Selected access pair (temporary checkout paths omitted):

```text
WARNING: ThreadSanitizer: data race
  Read of size 8 by thread T2:
    #0 lb_get_entity_name(lbModule*, Entity*) src/llvm_backend_general.cpp:2572:64
    #1 lb_type_internal(lbModule*, Type*) src/llvm_backend_general.cpp:3136:62
    #2 lb_type(lbModule*, Type*) src/llvm_backend_general.cpp:3517:14
    #3 lb_type_internal_for_procedures_raw(lbModule*, Type*)
    #4 lb_get_procedure_raw_type(lbModule*, Type*) src/llvm_backend_type.cpp:108:9
    #5 lb_create_procedure(lbModule*, Entity*, bool) src/llvm_backend_proc.cpp:158:26
    #6 lb_generate_procedures_and_types_per_module(void*) src/llvm_backend.cpp:2616:44

  Previous write of size 8 at the same address by thread T1:
    #0 lb_get_entity_name(lbModule*, Entity*) src/llvm_backend_general.cpp
    #1 lb_type_internal(lbModule*, Type*) src/llvm_backend_general.cpp:3136:62
    #2 lb_type(lbModule*, Type*) src/llvm_backend_general.cpp:3517:14
    ...
    #9 lb_generate_procedures_and_types_per_module(void*) src/llvm_backend.cpp:2611:9
```

The original crash stack was:

```text
alloc_cstring(gbAllocator, String)                 +896
lb_type_internal(lbModule*, Type*)                +680
lb_type(lbModule*, Type*)                         +568
lb_generate_procedures_and_types_per_module(void*) +72
internal_thread_proc(void*)
```

## Controls and experimental fix

- The minimal hello example produced 10 reports whose first access stack starts
  in `lb_get_entity_name` with the original instrumented compiler.
- `-thread-count:1` removed the observed name-cache reports. An earlier control
  produced no TSan reports; a fresh packaging check reported one unrelated
  parser/global-file-table access pair, so this is not a claim that the entire
  compiler becomes race-free with that flag.
- `-use-single-module` removed the observed type-name cache reports; other TSan
  warnings remained.
- A local experimental patch publishes a pointer to an immutable `String` using
  acquire/release atomics and compare/exchange, following the compiler's existing
  local-procedure-name publication pattern. The same minimal example then
  produced no first-access `lb_get_entity_name` reports. A separate print fixture
  also changed from 10 reports in each of three runs to zero in each run.
- A release compiler with that patch passed 200 stress compilations and the
  functional examples tested. The patch has not been validated against the full
  Odin test suite or other platforms, and is not installed as the local compiler.

Similar mutable caches for procedure/variable link names deserve review too. A
separate variable/link-name report remains with the narrow type-name patch in
the larger print fixture. This is not a claim that all other sanitizer reports
have been investigated or that the patch fixes every compiler race.

The attached bundle includes the plain Odin fixture, runner, selected sanitizer
traces, environment report, recorded experiment results and experimental patch.
