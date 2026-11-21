# Parameter contracts: verification, 2026-11-19

The syntax and contracts are specified in the
[attribute/directive reference](attributes-and-directives.md). The
[executable demo](../examples/parameter_contracts/demo.thor) combines all four
features. Both the Rust compiler and the Python reference implementation support
them; Odin itself is unchanged.

## Coverage

`tests/test_parameter_contracts.py` contains 32 differential compiler tests, with
additional negative cases within those tests. Successful native execution paths
run in checked builds and with `-o:speed -disable-assert -no-bounds-check
-no-type-assert`. Their verdicts use explicit checks rather than assertions.
Coverage includes:

- Automatic metadata defaults, argument text, locations, argument evaluation
  exactly once, omitted ordinary defaults, and ordinary overridable defaults.
- Aliases, explicitly typed callbacks, generic wrappers, callback returns,
  containers, conversions, named arguments, and rejected explicit overrides or
  contract erasure. Selected generic `when` branches retain native checking rules.
- Unsupported metadata ABI/entry-point placements and invalid capture targets.
- Runtime barriers in operands, calls, aliases, function bodies, transitive calls,
  generic `when` branches, collection construction, required constants, and
  explicit `#run`. A runtime marker cannot bypass purity or slice permissions.
- `#ro` readers, copies, transitive mutation, mutable aliases, addresses, writable
  escapes, generic slices, variadics, grouped names, callback assignments and
  containers, and read-only slice returns through erased callbacks.
- Static and runtime `pmap` callbacks, polymorphic callbacks without annotations,
  compile-time mapping, no ordinary inputs, and no returned values.
- Vet suppression during reporting and `--apply`, unused imports/locals,
  semicolons, procedure/local scope, native attributes, malformed annotations,
  and compiler errors that must remain errors.

Five live stdio TLS tests cover hover types and aliases, signature labels and
active parameters, completion filtering, read-only semantic highlighting,
inferred mutation hints, vet suppression, and diagnostics against unsaved text
with original UTF-16 positions. All five also passed against a separately built
TLS executable with assertions, bounds checks, and type-assert checks disabled.
Checked and unchecked frontend builds produced identical parser packets for the
combined demo.

Additional regression runs covered existing slice/copy, vet, `pmap`, pure
mutation, source identity, debug-map, assertion hygiene, and TLS suites. All 97
Rust unit tests passed. The 217-test mixed compiler run had one Odin helper
compiler SIGSEGV in `test_compile_time_readers_accept_readonly_locals`; that
unchanged test subsequently passed four consecutive isolated runs. The existing
59-test mapping/purity batch, 56-test vet batch, and 28-test TLS batch passed.

## Debugging

The real editor source-map consumer accepts the new parameter syntax and runtime
marker, maps breakpoints to original source lines, and the resulting debug
executable runs successfully. A new DAP test also checks metadata parameters,
read-only inputs, and stepping into a runtime-marked call.

Follow-up live validation passes all 52 CodeLLDB tests, including the new metadata
case, with no skipped tests. The test now waits until the first executable body
statement before inspecting spilled parameters and reads Odin string/slice
descriptor fields using CodeLLDB's supported expressions.

The earlier configuration-time timeout did not recur in the final run. A separate,
deterministic adapter startup hang was reproduced against the original adapter:
when CodeLLDB exited before connecting, the adapter cancelled its startup timer
without settling initialization. The same early exit now reports its native exit
code immediately. Startup cancellation and spawn errors also settle outstanding
requests, and disconnect is safe before a native connection exists.

The test transport retains its 30-second request/event deadline, reports the
pending operation and recent backend output on timeout, and bounds cleanup so a
stalled disconnect cannot hide the original failure. Eight transport tests and
all 149 editor tests pass, including startup exit/cancellation/error regressions.
The assertion-hygiene suite passes as well.

## Cost

`#ro` and `vet_ignore` are erased; `#runtime` emits its operand without a wrapper
or dispatch. Caller metadata uses the parameters explicitly declared in the
source signature and native static strings/locations. These features introduce
no new runtime permission table or metadata allocation.

Analysis is gated when the corresponding source features are absent. Reader
proofs are requested for callback conversions that need them, rather than for
unrelated procedure values in allocator support code. Runtime-barrier queries
cache per specialization and respect selected `when` branches.

A small release smoke case combining the parameter features, with no explicit
compile-time execution, measured 91.0 ms of lowering on its first invocation and
10.5 ms on the second; native compilation took 177.2/175.6 ms. Total compiler
reported time was 307.6/221.4 ms. This measures that smoke case, not larger projects.

## Reproduce

Build the compiler once; the prebuilt setting avoids each test class launching
another Cargo build:

```sh
cargo build --release --locked --offline --manifest-path rust/Cargo.toml
THOR_TEST_PREBUILT=1 python3 -m unittest tests.test_parameter_contracts
python3 -m unittest tests.test_parameter_contracts_tls
THOR_TEST_PREBUILT=1 python3 -m unittest tests.test_rust_debug_maps
python3 -m unittest tests.test_debugger tests.test_parameter_contracts_debugger tests.test_debugger_transport
cargo test --locked --offline --manifest-path rust/Cargo.toml --lib
python3 -m unittest tests.test_assertion_hygiene
python3 tools/check_language_reference.py
thor run examples/parameter_contracts/demo.thor
thor run examples/parameter_contracts/demo.thor -o:speed -disable-assert -no-bounds-check -no-type-assert
```
