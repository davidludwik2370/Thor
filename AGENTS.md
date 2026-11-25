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

# Generated storage

- Use the shared bounded compiler cache. Tests that need isolated caches must
  put them in automatically cleaned temporary directories, rather than retaining
  a cache for every case, source revision or benchmark run.
- Prefer `python3 tools/build_compiler.py` for compiler development builds and
  `python3 tools/test_compiler.py` for suites. These apply development retention.
  Preserve logs/results, not obsolete object histories or copied installations.
- Integration replay workspaces can be restored with
  `python3 tools/integration_storage.py restore` and archived again after use with
  `python3 tools/integration_storage.py archive`. The archive verifies sources
  before removing working copies. Preserve the canonical upstream checkouts,
  reports and any unique source changes.
- Prefer the existing Linux VM for ongoing Linux validation. A disposable Docker
  validation environment must be removed after its source differences and test
  evidence have been preserved. Do not prune unrelated Docker or VM data.
