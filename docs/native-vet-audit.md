# Native vet compatibility audit

Updated 2026-11-20 against Odin `dev-2026-10:8169255b9`.

All 17 native `-vet*` options, `-strict-style`, and all 17 accepted native file
policy names are available on Odin-compatible source renamed to `.thor`.
Both Rust and Python retain native policy errors, warnings, source locations,
negations, repeated-tag ordering and package-filter behavior. There is no
Odin-only rejection list. The native compiler diagnoses unknown options and
invalid combinations.

| Policy | Checking path |
| --- | --- |
| `semicolon`, `when-shadowing` | Ordinary lowering; also supported with all Thor extensions. |
| Bare `#+vet`, `-vet`, unused, shadowing, using, style, tabs, cast, nil-deref, uninitialized, atomic-access | A source-preserving native check, followed by ordinary Thor validation and compilation. |
| `deprecated`, `explicit-allocators` | Native file-tag policies in the checking view; these have no standalone CLI flags in the pinned SDK. |
| `-vet-packages`, `-strict-style-packages` | Native package selectors; file tags retain their override behavior. |
| Thor `no_alloc` | Thor's transitive allocation proof; consumed before native validation. |

The checking view supports Odin syntax, `fn`, immutable declarations, parameter
contracts, f-strings and comprehensions. For ordinary Odin-compatible files it
preserves the source text. For Thor expressions it exposes user reads, writes,
loops, calls and lexical bindings in the enclosing procedure. Compiler-owned type
witnesses retain signatures without checking a second captured execution of user
code. Original formatting is checked by opt-in frontend style checks; native
semantic policies still run on the checking view.

The checking program is compiled but never executed. Ordinary lowering still
enforces Thor's contracts and produces the runtime executable. This adds no
runtime instrumentation and changes neither runtime lowering nor Odin itself.
The Python implementation uses the Rust checking engine for Thor-only expressions;
its ordinary lowering and native-only checking remain independent. A built Rust
compiler is therefore required for that opt-in Python path.

## Policies for Thor expressions

* F-string fields and dynamic formatting widths are reads at the caller, even
  when their values are used only for formatting.
* Comprehension bindings have loop scope. Native shadowing and unused checks
  apply to their source names. Inputs, filters, keys, values and spreads are
  checked, including typed bindings, maps and sets.
* Short-circuit guards, conditionals, optional fallbacks, deferred statements,
  iteration conditions/post-statements and switch arms retain their control flow.
  Compile-time inactive `when` branches remain inactive. Native nil-dereference
  vet still reports known-nil reads in runtime `if false` branches, matching Odin.
* Unevaluated queries such as `size_of` do not construct collections or read their
  elements. Aliases of those builtins retain that behavior.
* Compiler-generated allocation calls are exempt from the native
  `explicit-allocators` argument rule. Source-written calls such as `make` and
  `delete` must still supply allocator arguments when that policy requires them.
  This policy checks explicit arguments; `no_alloc` is the transitive allocation
  prohibition.
* `@(unused)` keeps its unused-warning suppression, including with native unused
  policies. References to a marked declaration still fail Thor validation.
* Thor's implicit `using` enablement is retained for other policies. Native
  `using-stmt` / `using-param` policies can still reject those constructs;
  explicit source `#+feature using-stmt` keeps Odin's opt-in precedence.
* Contextual literal types come from the same rules as ordinary lowering,
  including declared arrays, return values, struct fields and generic parameters
  with private element types from imported packages.
* Generated helpers and support imports do not become unused/shadowing findings.
  Native package selectors and per-file opt-outs retain their source meaning.

Bare `#+vet` enables the native bundle, not every individual check and not
`no_alloc`. Native lists use spaces: `#+vet unused cast !shadowing`. Repeated
headers compose in source order. `extra` appears in Odin's diagnostic suggestions
but is rejected by its actual parser in this revision. Thor accepts comma lists
containing its custom `no_alloc` option; the native remainder is space separated.

Native checking views retain Odin's default package scopes. For example,
`-vet-unused-procedures` without a required scope remains an option error.
Ordinary `.odin` projects no longer get an implicit `-vet-packages` injected.
The two general policies also retain native default scopes through the ordinary
lowering, including external collection and SDK packages. Thor does not inject
an implicit package filter.

`thor vet` / `--vet` remain separate Thor convention checks, with verified
`--apply` corrections. Convention findings are errors for `thor build --vet`
and warnings otherwise; native diagnostics keep their native severity.

## Regression coverage

`tests/test_native_vet.py` compares installed Odin directly with both Thor
implementations for every native CLI policy and every file policy and opt-out.
It covers real failures and clean sources, exact primary locations, bare and
repeated tags, package-filter overrides, scoped unused-procedure requirements,
unknown names, whitespace/comma syntax, Unicode/CRLF, mixed/native/imported and
excluded files, overlays, `fn` and immutable syntax, custom no_alloc composition,
and full Thor features with the two general policies.
`tests/test_native_vet_features.py` exercises native policies on f-strings,
comprehensions, compile-time values, source styles and their control-flow
interactions through both compiler entry points. Runtime fixtures use
explicit checks in checked and unchecked release configurations.

`tests/test_vet_tags_tls.py` covers completions, descriptions, filtering, native
negations, valid completed tags, genuine policy errors, typos, unfinished edits,
and unsaved contract activation/recovery, including native diagnostics inside
f-strings and comprehensions. CLI, migration, native-only and Thor
vet suites cover interactions with command modes and automatic fixes.

Tests reporting expected native errors use `-thread-count:1` because the pinned
Odin revision has independently reproduced native checker/parser races. Thor's
default worker policy is unchanged. Those upstream failures are not retried or
counted as passing checks. This audit's first broader regression round also hit
one SIGSEGV while reporting an unused local type in an ordinary build, without
native vet flags or a native source-check pass. That parallel run is recorded as
a toolchain failure; expected-diagnostic fixtures use one native worker for the
separate serial verification.

### Native scoped-vet parser crash

Migration validation isolated an additional native-only reproducer on the same
installed Odin revision. Save the following as `main.odin` in a directory.

```odin
package main
import "core:fmt"
main::proc(){fmt.println(7)}
```

```sh
odin check /path/to/directory -vet-when-shadowing -vet-packages:main -json-errors
odin check /path/to/directory -vet-when-shadowing -vet-packages:main -json-errors -thread-count:1
```

On macOS arm64, the default-worker command produced 2 SIGSEGV failures in 40
runs. All 40 single-worker runs passed. A generated native input produced 3
failures in 40 default-worker runs and passed all 40 single-worker runs. Recent
crash reports faulted in `ast_file_vet_flags(AstFile*)` while parsing imported
files. This identifies a native parsing failure independently of Thor and of the
previous LLVM type-name-cache issue. The underlying concurrency cause remains
unconfirmed. Scoped-vet regression fixtures use one native worker.

## Validation result

The Rust release compiler and TLS server were rebuilt. The native-policy matrix,
live TLS completion/diagnostic tests, CLI/migration suites and serial unused-local
regressions passed; the ordinary no-policy and no_alloc-only profile fixtures
confirm that the additional native check is absent on those paths. Rust unit tests
and assertion hygiene checks also passed. The separately recorded parallel SDK
failure above remains an upstream caveat.

Runtime regression fixtures compare generated Odin byte for byte with and without
native vet, then execute both checked builds and
`-o:speed -disable-assert -no-bounds-check -no-type-assert` builds. Native policy
checking adds compile-time validation only; its program is never executed and
its output is never linked into the application. The broader fixture's native
executable layout varies even between two identical ordinary builds, so binary
hash identity is not a general assertion; source identity and explicit runtime
verdicts are retained.

The expression-support validation passed 78 combined native-expression/TLS
checks, 115 lowering/comprehension regressions, 97 Rust unit tests, seven
assertion-hygiene tests, and production-release smoke tests. The broader native,
CLI and migration run found one stale fixture missing Odin's mandatory package
selector for unused procedures; that fixture was corrected and reverified. Two
Windows-specific checks remain skipped on this Mac.

[Before/after measurements](benchmarks/native-vet-features-2026-11-20.md) show no
meaningful ordinary compile-time regression on the sampled fixtures. Native vet
adds an opt-in checking pass; ordinary and `no_alloc`-only paths retain their fast
path.

## Translated Odin commands

`thor migrate --command` adds `--odin-compatible` for command defaults and other
compatibility policies. Native vet no longer requires that flag. Migrated vet
metadata still restores per-file native policies in the checking view; both
`// thor-migrate #+vet ...` and the earlier `// Odin vet ...` header are recognized.
Command translation requires neither source inspection nor an Odin installation.
