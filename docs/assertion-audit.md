# Assertion audit — 2026-11-02

Assertions only validate state. Disabling them must preserve successful program
behavior. Required work runs before the assertion; mandatory error handling uses
an ordinary conditional. The repository rule is in [AGENTS.md](../AGENTS.md).

## Scope and findings

Reviewed the tracked compiler, native frontend, runtime/basic library, TLS fork,
editor clients, build/test tools, generated-source templates, demos and examples.
Generated SDK copies and caches are derived artifacts; their authoritative
sources are in the audited tree. The installed upstream Odin toolchain was not
modified.

The native AST inventory found 81 assertion calls in implementation sources:
five in the frontend, 65 in the runtime (including compile-time assertions),
and 11 in TLS. Their conditions/messages only inspect values or call reviewed
read-only operations. These include length/type/size queries, Unicode scalar
validation, lexical path inspection and borrowing a builder's contents with
`strings.to_string`. No additional required side effects were found after the
Thor 0.32.1 formatter fix.

Python's two implementation assertions are source-map consistency checks. They
do not advance the mapping state; stack mutation and emission happen outside
the assertions. Generated `#assert` checks inspect type/size/constant constraints.
Current VS Code client code contains no application assertion operations; the
retained upstream client checks a parsed numeric header and release metadata,
with its assertion helper logging only on failure. TLS's special builtin
signature file was reviewed as metadata rather than compiled implementation.

Two editor test setup snippets wrote confirmation files inside assertions.
Both now perform the write first and assert on its returned error. The demos
also now evaluate counter-updating membership/range/power expressions and random
sampling before checking their results, so these examples model the same rule.

Language acceptance fixtures can deliberately put user calls in assertions to
test Thor's assertion semantics. The compiler continues to accept and lower
those user programs; this audit does not introduce a new language restriction.

## Continuing checks

Run:

```sh
python3 -m unittest tests.test_assertion_hygiene
```

The six tests cover:

- Native implementation conditions and messages, using the real frontend AST.
  Calls must appear on a reviewed list of read-only operations. File build tags
  are neutralized during this check so other platforms are examined too.
- Python implementation assertions, including hidden calls and assignment,
  await/yield expressions.
- Regression examples containing nested/multiline mutations, assertion message
  calls, contextless assertions and allocating interpolation. Comments and
  quoted text are excluded by parsing.
- Actual editor setup fixtures compiled with `-disable-assert`: their files must
  still be created, checked by Python outside the compiled program.
- Equivalent frontend AST and syntax errors from a native frontend built with
  assertions/bounds/type checks disabled, including the two large demos.
- A complete compiler invocation under normal Python and Python `-O`, performing
  compile-time execution, lowering, formatting and unchecked native execution.

The call review list is conservative maintenance protection, not a general
procedure-purity proof. Adding a new predicate requires reviewing its body and
any callees. It cannot prove the behavior of arbitrary user procedures or
upstream dependencies.

Validation also includes all 1,133 native TLS tests under
`-disable-assert -no-bounds-check -no-type-assert`, four native tokenizer tests
with assertions disabled, and isolated VS Code standalone-run and CodeLLDB
integration suites. The evaluation, walkthrough and basic-library demos are
checked in ordinary optimized and fully unchecked release builds.
