# F-string performance

Thor 0.32.0 formats fields directly into the interpolation's output buffer.
The buffer starts with 512 bytes of stack storage and grows through the current
allocator when necessary. Common short f-strings containing native numbers and
strings now need only the final owned string allocation. This applies to
interpolation generally, including interpolation passed to arbitrary procedures;
there is no special treatment of printing procedures.

Each field is evaluated once in source order, including its dynamic width.
Formatting uses borrowed views or local scratch where possible, then writes
padding and grouping directly into the output. A custom formatter runs once,
even if its output exceeds the initial storage. Large output and high precision
use growing heap storage rather than truncating or repeating formatting work.

The final result is still copied into an exact-sized allocation. An Odin string
does not retain its allocation's capacity, so returning a builder's oversized
allocation would make ordinary `delete(string)` incorrect for allocators that
require the original allocation size. Scratch storage is released before the
result escapes. Allocation failures produce an explicit failure rather than a
silently truncated result. Result allocations retain the original Thor caller
location in allocation-tracker reports.

Some conversions, including big numbers, complex/quaternion formatting and some
native integer precision forms, retain their existing owned conversion scratch.
These can allocate more than once. The optimization does not change numeric
rounding, formatting syntax, allocator selection, or ownership: `f`, `f!`, named
results, and anonymous temporary cleanup keep their existing behavior.

Thor 0.32.1 corrects a release-build regression in 0.32.0: buffer writes and
growth were incorrectly placed inside assertions, so `-disable-assert` could
remove required work and cause malformed output or a crash. These operations
now execute independently of assertions. Allocation failures also retain
explicit checks with assertions disabled. Regression coverage includes
`-disable-assert` alone, optimized builds, and the full unchecked `tbss` flag
combination; output comparisons and allocation checks do not depend on assertions.

## Measurements

The following local macOS arm64 measurements compare Thor 0.31.0 with 0.32.0.
Each row returns an owned string, checks equivalent output against Odin
`fmt.aprintf`, and frees every result. The timed loop performs no terminal I/O.
Both versions use `-o:speed`, 50,000 calls per sample and seven samples; times
are medians rounded to nanoseconds. Odin controls were compiled with each run;
the table shows the control from the 0.32.0 run.

| Formatting case | Previous Thor, ns | Thor 0.32, ns | Odin `aprintf`, ns | Thor allocation/resize events before → after |
| --- | ---: | ---: | ---: | ---: |
| One integer | 319 | 157 | 150 | 5 → 1 |
| Padded integer and float | 554 | 259 | 309 | 9 → 1 |
| Four integer fields | 796 | 310 | 285 | 14.92 → 1 |
| 4,096-byte string field | 3,621 | 388 | 343 | 9 → 2 |

These cases improved by approximately 2–9 times relative to the previous Thor
implementation. The remaining differences from Odin depend on the operation:
the large-string case still pays for scratch growth and the exact-sized final
copy. Timings are local microbenchmarks, not a guarantee for every application.
Allocation counts are measured separately across 100 calls and include tracked
resizes; fractional averages reflect changing output lengths. Every measured
case returns to zero live tracked bytes.

An instrumented copy of the `double_bet` example fell from 304 allocation/resize
events to 54, and from 10,926 to 484 cumulative tracked bytes. Both versions
finished with zero live tracked bytes. The example's source was unchanged.

Reproduce the benchmark from the compiler checkout:

```sh
python3 tools/benchmark_fstrings.py --iterations 50000 --samples 7 --save /tmp/fstrings.json
```

To compare another compiler checkout, set its root explicitly so its own runtime
is used as well:

```sh
THOR_ROOT=/path/to/older/Thor python3 tools/benchmark_fstrings.py \
    --thor /path/to/older/Thor/thor --iterations 50000 --samples 7
```

## Regression coverage

`tests/test_formatting_buffers.py` covers allocation counts, exact-sized frees,
allocators without resize support, stack/heap boundaries, aliased buffer views,
escaped results, large custom formatter output, Unicode padding, high precision,
explicit allocators, allocation failures and comparisons with Odin formatting.
Native runtime tests also check buffer lifetime and appended fields under ASan.

The broader compiler suites cover nested interpolation, conditional lifetimes,
`f!`, dynamic widths, field evaluation order, pure functions, `#run`, big numbers
and diagnostics. Live TLS and VS Code checks exercise field/width hover,
highlighting, edits, rename and mapped errors. CodeLLDB tests verify stepping
into field and width expressions; allocation-tracker tests verify original
source locations. Both evaluation demos are also run in optimized and debug
modes.

The implementation is in `runtime/format/{buffer,format,render}.odin` and the
interpolation lowering in `thorlang/expressions.py`.
