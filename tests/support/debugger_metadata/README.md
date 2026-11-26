# Caller metadata debugger control

These sources match the live regression
`tests.test_parameter_contracts_debugger.ParameterContractDebuggerTests.test_runtime_step_into_metadata_and_ro_input_keeps_source_and_values`.
The plain Odin procedure uses native inferred caller-expression/location defaults;
the Thor procedure adds read-only and compiler-filled parameter contracts. Both
keep the first body statement on line 4 and the call on line 10. Required program
work is outside assertions, and an explicit conditional verifies the result.

Build each separate package with its compiler and `-debug -o:none -thread-count:1`.
Break on line 10, step into the procedure, and inspect the first body statement
on line 4. The expected watches are `expression.len = 9`,
`expression.data = "value + 1"`, `location.line = 10`, and `values.data[0] = 7`.
Continue to line 11 to inspect `output = 10`.

On Windows x64 Odin 9a69916 / CodeLLDB 1.12.3, all four argument watches are
unavailable at the first body statement in direct plain Odin, plain Odin through
an identity source-map bridge, and Thor. Every build runs successfully and the
final output watch is 10 in all three sessions. The strict live test remains
active. This is an additional native debugger limitation, separate from the
eight original unresolved cases in `debugger_pairs/`.
