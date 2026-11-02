# Assertion rules

Assertions must only validate state. Removing or disabling an assertion must not
change successful program behavior.

- Never put required writes, allocation, resizing, mutation, I/O, iterator/parser
  advancement, ownership transfer, or cleanup inside an assertion condition or
  message. This includes nested calls and assertions in generated source.
- Perform the operation first, store its result, then assert on that result.
  Use an ordinary conditional and an explicit error/panic when failure handling
  must remain active in release builds.
- Read-only predicates and inspection are fine. Review called procedures rather
  than assuming that a Boolean return value means the procedure is read-only.
- Exercise changed native/runtime paths with `-disable-assert` and the unchecked
  release flags as well as checked builds. Test verdicts for that configuration
  must use checks that remain enabled, such as Python comparisons or explicit
  `if ... { panic(...) }` checks.
- Apply the same rule to examples and test setup. Test frameworks that explicitly
  execute an operation to verify its failure are intentional test operations;
  they must not be used to perform application setup or required program work.
- Language regression fixtures may intentionally contain user assertions with
  calls, including tests of assertion elision. Preserve those language semantics;
  this rule governs the toolchain implementation and required test setup.
- Run `python3 -m unittest tests.test_assertion_hygiene` after changing assertions.
  Its conservative call review list is not a general proof of procedure purity.
