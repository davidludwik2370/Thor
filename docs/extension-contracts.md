# Thor extension contracts

These rules describe what a Thor program, its caller, or its build can observe. The examples are inputs to the [conformance suite](../tests/test_extension_contracts.py). Each example is a complete program. The rules apply to the maintained compiler on supported native targets. The [language specification](language-spec.md) indexes the other extensions.

## Odin compatibility and migration

Odin is the baseline. A native `.odin` dependency uses the selected Odin compiler's language. When reading the same text as `.thor`, additional syntax can give an Odin identifier another meaning. The audited spellings are `fn`, `set`, `or_panic`, `Integer`, and `Rational`. The last two are predefined types.

The compatibility requirement is that every valid Odin program can be expressed after `thor migrate` and, with equivalent inputs, SDK, target, flags and dependencies, keeps its observable behavior. This includes which conditional branches compile, printed values, and names observed through reflection or external interfaces. Names local to the implementation can change. Unchecked operations outside Odin's defined behavior have no portable result to compare. Where a native build or debug default matters, use the command emitted by `migrate --command`.

### Migrating an identifier collision

<!-- contract: migration -->
```odin
package main
import "core:fmt"

main :: proc() {
    fn_odin := 4
    fn := 2
    or_panic := fn + fn_odin
    fmt.println(fn, fn_odin, or_panic)
}
```

Both binaries must print `2 4 6`. The conversion must choose a fresh name for `fn`, preserve all references, and leave the source tree intact. Building both projects during migration verifies acceptance and linking. Running them against the same expected observations tests behavioral parity. Migration preserves reflected struct, enum and bit-field member names, including `or_panic`. [Migration guide](odin-migration.md) · [Native parity cases](../tests/test_migrate.py)

The translated command includes `--odin-compatible`. In that mode, a signed sequence index or slice endpoint uses Odin bounds semantics. For a length-two array, a runtime index of `-1` fails a checked build at execution, even with assertions disabled. A plain Thor invocation counts from the end. Both forms must agree with native Odin on in-range indices. No test executes an out-of-range native access with bounds checks disabled.

### Observable name of a migrated type

<!-- contract: reflected-type -->
```odin
package main
import "core:fmt"

Integer :: distinct i16
main :: proc() { fmt.println(typeid_of(Integer)) }
```

Odin prints `Integer`. The converted program currently prints `Integer_odin`. This is a known gap in the compatibility requirement. The test reports this specific discrepancy separately, passes when both outputs match, and fails on any other output or build failure. The name of a generic type parameter can also appear when Odin formats an instantiated record. Preserving an externally visible name must remain consistent even when a different declaration or specialization is active in another build. Source paths and programs that read their own source present a related design question about which source identity to expose.

## Ownership of fn results

A `fn` may read borrowed input. At each reachable return, every result component that needs backing storage must have a proven owner created by the function or by a verified operation it calls. This holds through aliases, concrete generic substitutions, callback calls, multiple results, and implicit or named returns. Returning the length or an inline scalar read from a borrow is valid. Returning even a zero-length view into borrowed backing still needs an explicit clone. Nil and empty values without backing need no owner.

### Forwarding an owned allocation

<!-- contract: owned -->
```thor
package main
import "core:fmt"
import "core:slice"

duplicate :: fn(xs: []$T) -> []T { slice.clone(xs) }
tail :: fn(xs: []int) -> []int { duplicate(xs[1:]) }

main :: proc() {
    original := [3, 5, 7]
    result := tail(original[:])
    defer delete(result)
    result[0] = 11
    fmt.println(original[1], result[0])
}
```

This prints `5 11`. Returning a unique whole owned buffer transfers it to the caller without a second payload allocation. User defers run before that storage leaves the function. Scratch storage that is still local is released on exit. A shallow clone suffices here because each element is an integer. For rows containing slices, each nested slice also needs independent backing. `pure.clone` deep-copies supported values and `pure.destroy` releases an owned tree. If a result contains the same owned allocation twice, the returned members must be independently releasable. Dynamic-array results preserve length, capacity and a usable caller allocator. Fixed SOAs and SOA slices follow the same rules for their logical fields. [Counting-allocator, SOA and alias tests](../tests/test_fn_result_ownership.py)

### Rejecting a borrowed result

<!-- contract: borrowed -->
```thor
package main

tail :: fn(xs: []int) -> []int {
    alias := xs
    alias[1:]
}
main :: proc() {}
```

This declaration must fail at compile time, including in unchecked builds. A wrapper, `#run`, or an intermediate record cannot confer ownership. A nonempty static literal and a view into a local array also require an explicit copy when returned as an owned slice. `proc` continues to support APIs that deliberately return borrowed views. [Purity and ownership rules](language-guide.md#pure-functions-and-compile-time-execution)

## Required evaluation and cache consistency

An explicit `#run` must evaluate the requested eligible call at compile time in all four modes. Unknown runtime inputs, a disallowed effect, or failure during required execution must produce a compilation error. `--comptime=required` still permits explicit requests. `override` may ignore a `#runtime` barrier but must still enforce purity and ownership. These rules apply to host evaluation. [Evaluation modes and limits](comptime-modes.md)

### Reconstructing a cached result

<!-- contract: cache -->
```thor
package main
import "core:fmt"

OFFSET :: #config(OFFSET, 3)
make_values :: fn(n: int) -> []int {
    xs := make([]int, 2)
    xs[0], xs[1] = n, n + OFFSET
    return xs
}
saved :: proc() -> []int { return #run make_values(7) }

main :: proc() {
    a := saved()
    defer delete(a)
    b := saved()
    defer delete(b)
    a[0] = 99
    fmt.println(a[0], b[0], b[1])
}
```

With the default configuration, this prints `99 7 10`. With `-define:OFFSET=11`, it prints `99 7 18`. The result of each execution of `saved` belongs to its caller. The compiler may reuse serialized contents, but runtime allocations must be independent on every execution. In a constant context a supported string result instead becomes ordinary static Odin data. Slices and dynamic arrays require runtime materialization.

For a given valid build, disabling the cache, starting with an empty cache, and reusing a populated cache must agree on observable results. Reuse is valid only if the relevant dependencies, configuration, target and evaluation policy still describe the same computation. A result computed with Thor indexing cannot satisfy a request that would fail under Odin bounds. An unrelated edit may cause a miss. A prior success cannot validate a newly invalid return or a failed evaluation. [Cache invalidation](../tests/test_rust_cached_execution.py) · [Execution failures and validation](../tests/test_t23.py)

## vet --apply across configurations

An automatic edit must preserve compilation and behavior in each supported configuration in which the original program works. Compiler acceptance with the current flags is necessary and can be insufficient. Consider other `#config` values, target-selected source files, generic specializations and aliases. This obligation also applies to removing imports and to a batch of interacting edits. If vet cannot establish safety, keep the source. An unknown SDK initializer is enough reason to retain a potentially side-effecting import. Immutable advice that is valid in this build but unsafe to apply across configurations can carry a manual-review diagnostic.

### Applying a correction under one configuration

<!-- contract: vet -->
```thor
package main
import "core:fmt"
import "core:math"

CHANGE :: #config(CHANGE, false)
main :: proc() {
    base := 4
    value := base + 1
    when CHANGE { value += math.abs(-2) }
    fmt.println(base, value)
}
```

Applying immutable and unused-import suggestions with the default flags must convert `base` to `:>`, preserve `value :=`, and retain the `math` import used only in the other configuration. The program must still print `4 5` by default and `4 7` with `-define:CHANGE=true`. Applying the same immutable corrections again must leave this file byte-for-byte unchanged. When an edit is only safe together with another, the verified batch may apply it and an individual editor fix must be withheld. [Immutable edit rules and JSON diagnostics](immutable-vet.md) · [Configuration and alias tests](../tests/test_vet_immutable_oracle.py) · [Native policy and import tests](../tests/test_native_policy_projection.py)

## Running the contracts

From the repository root, run the examples and their allocation-count and reflected-member checks.

```sh
python3 tools/test_compiler.py --match 'tests.test_extension_contracts|test_owned_helpers_forward_one_payload_allocation|test_reflected_or_panic_members_and_colliding_type_references'
```

The runner extracts the marked examples above verbatim. It checks real compiler exit status and output, changes one input at a time, and compares the edited program against the original under both configurations. The allocator test counts allocations of the exact payload size at runtime, with assertions enabled, disabled, and in an unchecked optimized build. The expected outputs are independent of generated Odin and cached packets.
