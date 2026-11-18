# Parallel mapping

`pmap` is available from `thor:basic` and `thor:parallel`:

```odin
import "thor:basic"

Job :: struct { left, right: int }
calculate :: proc(a, b: $T) -> (sum, product: T) { return a+b, a*b }

jobs := []Job{{2,3}, {4,5}}
rows := basic.pmap(jobs, calculate)
defer delete(rows)
// rows[0].sum == 5; rows[1].product == 20
```

Jobs must be a slice of structs. Fields supply positional callback arguments in
declaration order, as in `calculate(**jobs[i])`. Struct field names need not match
parameter names. Polymorphic callbacks are inferred from those field types;
neither a procedure-type annotation nor an explicit specialization is required.
Procedure aliases, overloads, procedure variables, default arguments, and `fn`
callbacks are supported. An empty job struct invokes a zero-argument callback.

Results preserve job order, even though callback execution order is unspecified:

* One result produces a slice of that type, including when that type is a struct.
* Multiple results produce a slice of structs. Named results retain their names;
  unnamed results use `v0`, `v1`, etc. Equal result types still produce a struct.
* No results produce no return value: call `pmap(jobs, action)` as a statement.

`result_type=Result` overrides the output element type. For multiple results it
must be a struct with matching field types/count/order; its names override the
callback's result names. For a single result it must match that result's type.
It is an error to supply `result_type` to a callback with no results. If declared
and generated result names collide, provide distinct names or an explicit type.

```odin
Result :: struct { added, multiplied: int }
rows := basic.pmap(jobs, calculate, result_type=Result, workers=4)
defer delete(rows)
```

## Scheduling and ownership

`workers=0` uses the processor count. Positive values are capped at the job
count; negative values panic. The calling thread participates, so `workers=1`
is serial. Each worker processes a contiguous chunk. There is no thread per job,
per-item scheduler allocation, or runtime result reflection. Nested mapping is
serial within its current worker. On targets without native thread support,
mapping is serial. All started workers are joined before returning. Failure to
allocate scheduling storage or start a worker falls back to serial execution;
failure to allocate the output slice panics at the original caller location.

`allocator=context.allocator` owns the output slice and scheduling storage. Delete
the outer result with that allocator. Inputs are borrowed and results are
shallow; `pmap` does not clone or delete allocations inside jobs or results.
Read-only slice permissions still apply to callback arguments.
The outer job slice can be read-only. Slices carried inside jobs follow Thor's
existing conservative rule for indirect callbacks: they must provide writable
access, even when the callback only reads. Copying those nested slices into
new mutable storage is an option when the original view is read-only.

Callbacks run with `worker_allocator`, which defaults to the runtime heap
allocator. This is deliberately independent of the caller's default allocator:
a scoped arena or debug tracking allocator may not support concurrent calls.
Every worker inherits the rest of the caller's context, but new threads retain
their own temporary allocator. The caller's allocator is restored on return.

When callbacks return owned allocations made with their default allocator,
delete those allocations with **the same worker allocator**. For example:

```odin
import "base:runtime"
heap := runtime.heap_allocator()
rows := basic.pmap(jobs, allocate_result, worker_allocator=heap)
for row in rows { delete(row, heap) }
delete(rows)
```

Supplying `worker_allocator` promises that it supports concurrent use and
outlives any returned allocations. Temporary-allocator memory must not escape
a callback. Shared state, referenced resources, and callback synchronization are
the programmer's responsibility. Small callbacks may cost less than starting
threads; use a serial loop/`fmap` or `workers=1` for small workloads.

## Compile-time execution and pure callers

The same API works with `#run`, including inferred polymorphic callbacks and
named multiple results:

```odin
rows := #run basic.pmap([]Job{{2,3}, {4,5}}, calculate, workers=2)
defer delete(rows)
// rows[0].sum == 5; rows[1].product == 20
```

`pmap` remains a `proc`: an ordinary runtime call may run an impure callback.
When called by `fn` or `#run`, the selected callback must instead meet the normal
pure-code rules, recursively. Runtime procedure variables, ambient context,
global mutable state, I/O, and explicit `allocator`/`worker_allocator` arguments
are not eligible. Callback defaults are checked only when the jobs omit them.
As with other pure calls, a callback may modify referenced storage owned by the
surrounding computation; it may not modify that computation's borrowed inputs.
Callers remain responsible for avoiding races between jobs.

An explicit `#run` also requires compile-time-known jobs and options. It uses
the same bounded parallel scheduler inside the compile-time helper. Results
are materialized and cached through the ordinary `#run` mechanism; the returned
runtime slice is allocated and must be deleted normally. Empty input, nested
mapping, result-type overrides, and allocated/numeric results follow the same
rules as other eligible compile-time calls. Execution timeout and callback
errors refer to the original Thor request.

Pure callers use independent worker scratch allocators. Worker allocations stay
alive until the caller has promoted its result and finished using its local
data, then are released with the caller's scratch storage. This also supports
callbacks returning allocated values or writing allocated values through local
output parameters. Synchronized scratch bookkeeping also supports explicitly
freeing or resizing buffers across the caller/worker boundary. Ordinary runtime
calls retain the worker-allocator ownership
contract described above.

## Result type queries

`result_type_of` is a compiler intrinsic in the ordinary `thor:intrinsics`
namespace; it adds no reserved words:

```odin
import ti "thor:intrinsics"
Result :: ti.result_type_of(calculate(**Job{}))
```

It inspects a resolved procedure call without executing it or its arguments.
One result keeps its exact type; multiple results become a struct using the
same naming rule as `pmap`. A no-result call has no result type and is an error.
Queries work through aliases and with polymorphic/overloaded calls. Native
Odin checks the actual call's argument types, defaults, and constraints.

Library procedures can use a query as a type parameter's default:

```odin
first :: proc(jobs: []$Job, callback: $F,
    $Result: typeid = ti.result_type_of(callback(**Job{})),
) -> Result {
    return compress_values(callback(**jobs[0]))
}
```

Thor supplies the default at the caller, where the callback's result names and
specialization are known. Runtime arguments are evaluated once in written order.
The worker implementation is ordinary library code. Pure calls use a reviewed
scheduler boundary tied to the official `thor:parallel.pmap` declaration, through
aliases and re-exports; an unrelated procedure named `pmap` receives no special
treatment. No Odin compiler changes are needed.
