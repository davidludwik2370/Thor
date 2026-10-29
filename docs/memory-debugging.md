# Memory checks in debug builds

On native macOS executable builds, `-debug` enables Thor's basic allocation
tracker and Odin's AddressSanitizer (`-sanitize:address`). ASan builds default to
`-use-single-module`, which improves global-variable instrumentation. An explicit
Odin module or sanitizer choice takes precedence.

Normal builds retain their existing allocator, compiler flags and output. The
automatic memory checks are also disabled for other hosts and cross targets;
`-debug` still produces ordinary debug information and Thor source maps for
breakpoints and stepping on those platforms.

```sh
thor run . -debug
thor run . -debug --track-allocations=off   # ASan remains on
thor run . -debug --no-sanitize            # Basic tracker remains on
thor run . -debug --track-allocations=trace
thor run . --track-allocations=basic       # Tracking without debug/ASan
thor run . -debug --track-allocations=off --no-sanitize
```

`--track-allocations` accepts `auto`, `off`, `basic`, and `trace`. Trace mode
requires `-debug`. Explicit tracking currently requires a native macOS executable.
An explicit `-sanitize:...` is forwarded even when `--no-sanitize` is present;
that switch disables only automatic AddressSanitizer selection.

`thor check` and `thor transpile` retain their normal behavior. Library and
no-entry-point builds do not automatically install a tracker. `thor test -debug`
uses ASan on macOS but keeps Odin's existing per-test tracking allocator. To make
Odin tests fail on leaked memory, add `-define:ODIN_TEST_FAIL_ON_BAD_MEMORY=true`.

## Allocation reports

Thor installs the tracker as `context.allocator` at the beginning of `main`.
Ordinary Odin-convention calls inherit that context. Code can still install a
custom allocator or use explicit allocator arguments. `context.temp_allocator`
retains its normal behavior, and independently initialized thread contexts do
not automatically inherit the tracker.

Keep allocator ownership consistent: memory allocated through the tracker must
be freed through that allocator. When changing the context, use a nested scope
or retain the original allocator for subsequent frees. Maps and dynamic arrays
normally carry their own allocator; strings and raw slices require care.

The tracker has stable process-lifetime storage. Its shutdown callback reports
after main's deferred cleanup and Odin's normal `@(fini)` procedures. Clean runs
are silent. Outstanding allocations produce a summary and allocation locations
on stderr; leaks alone preserve the program's exit status. Invalid/double frees
panic immediately at their mapped location.

The tracker covers allocations routed through it, starting in main. Startup
allocations, explicitly selected allocators and arena suballocations have their
usual ownership rules. Join worker threads before shutdown. C `atexit` callbacks
registered before main can run after the tracker report; retained allocations for
those callbacks should use an explicit allocator. Panic, abort and forced debugger
termination can bypass the normal leak report. The tracker does not free leaked
user allocations for you.

## Original Thor locations, including direct execution

Memory-enabled executables contain a compact, versioned source-location table.
Copied source retains line/column precision; generated helpers map to their Thor
expression. The table includes imported Thor sources and shared-library mappings,
and stores compiled locations rather than rereading possibly edited source files.

The tracker uses that table directly. ASan uses the shared executable
`llvm-symbolizer-thor` beside the Thor launcher. It delegates address resolution
to LLVM's symbolizer and maps code frames and global declaration locations before
ASan prints its report. Compiler-generated expression helper names are replaced
with the owning Thor procedure where available. Ordinary Odin and system frames
keep their actual locations.

This also works when a debug executable is launched directly, without `thor run`,
VS Code, or environment setup. On macOS, keep its normal `.dSYM` bundle with it.
The executable and bundle can be moved together; generated Odin and separate
Thor map files are not needed for these runtime reports.

The shared Thor installation, an LLVM symbolizer and the ASan runtime must remain
available. `THOR_LLVM_SYMBOLIZER` selects a particular LLVM symbolizer; otherwise
Thor discovers the installed tool and records its path in the executable. Normal
ASan configuration remains available through `ASAN_OPTIONS` and
`ASAN_SYMBOLIZER_PATH`. A program providing its own `__asan_default_options` keeps
control of that hook. If the helper is unavailable, ASan still detects errors,
but its output can lose source locations. Clean runs do not start the helper.

Hook detection excludes platform/test files not compiled for the current build.
Within an included file it is conservative: even a hook inside an inactive
`when` branch suppresses Thor's default hook. This affects automatic symbolizer
selection, not instrumentation, allocation tracking or debugger source maps.
For such a program, select the helper explicitly when running it:

```sh
ASAN_SYMBOLIZER_PATH=/path/to/Thor/llvm-symbolizer-thor ./program
```

The embedded map still supplies original Thor locations. Automatic selection of
conditionally declared user hooks remains a refinement for a later pass.

Source mapping cannot recreate caller frames omitted by the native unwinder.
Some Odin allocation/free stacks omit callers even with ASan's slow unwinding
enabled. Basic tracker locations and the fault's available frames remain useful;
the tracing allocator adds allocation call stacks.

ASan detects many invalid memory accesses, but does not understand every custom
allocator's internal object boundaries. It is a debugging aid rather than a full
memory-safety guarantee. Its instrumentation and tracking add runtime overhead.
These defaults are intentionally limited to debug builds, and are not injected
into Thor's compile-time evaluation/type-probe executables. Compile-time cache
keys and ordinary builds do not depend on the memory-check options.

See [AddressSanitizer's documentation](https://clang.llvm.org/docs/AddressSanitizer.html)
and [Odin's tracking allocator example](https://odin-lang.org/docs/overview/#tracking-allocator)
for the underlying facilities.
