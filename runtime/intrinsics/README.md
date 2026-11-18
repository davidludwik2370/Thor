# Thor type queries

```odin
import ti "thor:intrinsics"
Result :: ti.result_type_of(calculate(**Job{}))
```

`result_type_of` inspects a call without executing the procedure or its arguments.
It returns the single result's exact type, or a struct containing all results.
Named results keep their names; unnamed results use `v0`, `v1`, and so on. A
no-result call is an error. Native Odin still validates argument compatibility,
overload selection, defaults, and generic constraints.

Queries work through constant aliases and can be used in pure functions and
compile-time execution. Querying an impure procedure does not make the query
impure. Type dependencies are retained independently from the procedure's body.

For dependent type-parameter defaults and a library use of the query, see
[parallel mapping](../parallel/README.md). No additional reserved word is added.
