# Odin code-generation crash investigation

Investigated October 29, 2026, using the installed Odin compiler
`dev-2026-10:8169255b9` (`8169255b97b85d6a08691d99764f51fb01f92b5a`) on
macOS 15.6 / arm64. The installed Odin checkout/binary and Thor's defaults are
unchanged. All compiler experiments used a separate temporary copy.

## Finding

There is a reproducible **data race in Odin's cached LLVM type names**. It is
present when compiling an ordinary Odin program containing only
`fmt.println("hello")`; neither Thor nor its generated code is required.

The race matches the earlier SIGSEGV closely. The ordinary compiler did not
repeat that exact crash during 2,402 subsequent compilations. The underlying
race was reproduced with a C++ ThreadSanitizer build of Odin. A later full
regression run repeated the same crash stack and offsets, with a null source
pointer and length 30; its isolated retry passed. The later record is summarized
in `evidence/recurrence-2026-10-29.txt`.

## Original crash evidence

The macOS record is `odin-2026-10-29-031708.ips`, not a crash in the compiled
program. Eleven threads were active in the compiler. The faulting worker's stack
was:

```text
alloc_cstring(gbAllocator, String)                 +896
lb_type_internal(lbModule*, Type*)                +680
lb_type(lbModule*, Type*)                         +568
lb_generate_procedures_and_types_per_module(void*) +72
internal_thread_proc(void*)
```

The saved registers and disassembly show that `alloc_cstring` received a string
with a **null source pointer and length 26**. Its destination allocation was
non-null. The faulting instruction was a load through that null source while
copying the name. This is inconsistent cached-name data, rather than a null
destination returned by an allocation failure.

## Why the cache is unsafe

In the inspected source, `src/llvm_backend_general.cpp:2565–2596`,
`lb_get_entity_name` checks `e->TypeName.ir_mangled_name.len` and returns the
cached `String` if nonzero. Otherwise it computes the name and writes the cache.

`String` has two separate machine-word fields: a pointer and a length. The cache
is shared by code-generation workers for different LLVM modules. Those workers
read/write both words without a lock or atomic publication. They also need an
ordering guarantee that the name's bytes are initialized before another worker
reads them. The default compiler's assembly uses ordinary loads and stores.

That C++ data race permits a reader to observe a nonzero length without a valid
published pointer/content. It explains both the null-pointer/26-byte state in the
crash and its intermittency. The immediate compiler call site constructs named
LLVM struct/union types from this cached name.

## Reproduction and controls

[The fixture and runner](README.md) preserve a plain
Odin example, commands, a selected sanitizer trace, compact evidence and an
experimental patch. ThreadSanitizer must instrument the **C++ compiler binary**;
passing Odin's `-sanitize:thread` to the example alone does not test this race.

| Experiment | Result |
| --- | --- |
| Stock release compiler, 2,402 compilations split between plain Odin and frozen generated Thor | No repeated native crash; confirms it is difficult to catch by repetition alone. |
| Original compiler rebuilt with C++ TSan, three repeated plain print cases | 10 reports with a first-access stack in `lb_get_entity_name` in each run; recorded read/write stacks identify the type-name cache. |
| Original TSan compiler, one-line hello fixture | 10 equivalent cache reports. |
| Prototype atomic type-name cache, three repeated plain print cases | No first-access cache reports in any run. |
| Prototype TSan compiler, one-line hello fixture | No first-access cache reports. |
| Original TSan compiler with `-use-single-module` | No type-name cache reports in this control. Other sanitizer reports remain. |
| Original TSan compiler with `-thread-count:1` | No type-name cache reports. The earlier control had no warnings; fresh bundle validation reported one unrelated parser/global-file-table race. |
| Ordinary release compiler built with the prototype patch | 200 stress compilations pass. Plain hello, frozen generated `basic.print`, and both the basic and full Thor walkthrough demos compile/run successfully. |

The sanitizer also reported other locations. They are not established causes of
this crash, and this investigation does not validate them all. In the original
two-print-call case a separate report about a cached variable/link name remains
after the type-name patch. The prototype deliberately addresses the observed
type-name race; it is not a claim that Odin is globally race-free.

Fresh validation after packaging the standalone reproducer is preserved in
`evidence/bundle-validation.json`: three baseline hello runs reported 12, 12 and
10 first-access name-cache warnings, versus zero in all three prototype runs.
Both workaround controls had zero name-cache warnings; other warnings remained.

## Fix and immediate options

The experimental patch publishes a pointer to an immutable `String` atomically,
using acquire/release ordering and compare/exchange for competing initializers.
It follows the existing atomic-pointer pattern used for Odin's local procedure
names. The winner's pointer and contents become visible together; readers never
load a partially published two-word value. Parallel compilation remains enabled.

The patch changes three compiler source files and is included for review. It
has **not** replaced the installed compiler. It is narrowly validated against the
reproducer and functional examples, not the entire Odin compiler test suite or
other native platforms. A broader upstream fix should review the analogous
procedure/variable link-name caches too.

With the existing compiler, these explicit Thor build flags avoid the observed
parallel-module name-cache path:

```sh
thor build . -use-single-module
# Or, to give up compiler-worker parallelism:
thor build . -thread-count:1
```

`-use-single-module` changes compilation organization and may change performance;
the best choice depends on the project. Neither workaround has been made a Thor
default for normal builds. Native macOS debug builds using automatic ASan already
default to a single module, unless the user explicitly selects separate modules.
Thor's existing fatal-backend diagnostic will identify another SIGSEGV rather
than silently returning an unusual exit code.

No issue or patch was submitted to an external service. The local artifacts are
suitable starting material for an upstream report/review.
