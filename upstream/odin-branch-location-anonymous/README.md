# Backend assertion for `#branch_location` in an anonymous procedure

The installed Odin compiler `dev-2026-10:8169255b9` accepts this native source
during checking but asserts during LLVM code generation. The compiler is running
on macOS arm64. The minimal source is [main.odin](main.odin).

From this folder, compile it with:

```sh
odin build main.odin -file -thread-count:1 -out:/tmp/odin-branch-location-repro
```

The failure is:

```text
src/llvm_backend_expr.cpp(4643): Assertion Failure: `p->uses_branch_location`
This is a compiler error. Please report this.
```

The compiler exits on SIGTRAP, reported as return code `-5` by Python's
`subprocess` on this system. One compiler worker reproduces the failure, so this
does not require parallel compilation. Checked builds and unchecked release
flags reproduce the same assertion.

The expected result is successful compilation. When the anonymous procedure
returns, its deferred statement should print that procedure's implicit exit
location. The named local-procedure control compiles and runs successfully:

```odin
package main
import "core:fmt"
main :: proc() {
    p :: proc() { defer fmt.println(#branch_location) }
    p()
}
```

The issue is reproducible directly with Odin. Thor's emitted anonymous
procedures encounter the same backend assertion. Thor support and location
mapping are documented in the [compatibility corrections](../../docs/odin-superset-audit-2026-11-13/fixes.md).
