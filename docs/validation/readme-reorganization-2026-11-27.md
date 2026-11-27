# README feature tour — 2026-11-27

The first reorganization (revision `78061bd`) starts with the logo and installation
commands, followed by a linked index of 45 named Thor extensions. The detailed sections follow that
order, with small Thor examples, Odin comparisons, ownership/contract caveats
and links to full references. Numbered implementation milestones are omitted.
Compiler commands, vet, VS Code/TLS and compatibility follow the feature tour.
The extended language and compiler material is retained in
[the language guide](../language-guide.md) and
[the compiler guide](../compiler-guide.md).

## Executed examples

`tests/test_readme_examples.py` reads the exact marked README fences.
`tests/fixtures/readme-examples.json` supplies wrapping context, expected output,
expected diagnostics and compile-time-mode selection, without duplicating code.

On macOS 15.6 ARM64, the maintained runner passed **51 tests**, with no skips,
failures or errors, in 173.561 seconds:

```sh
THOR_TEST_PREBUILT=1 python3 tools/test_compiler.py \
  --match tests.test_readme_examples \
  --output dist/readme-reorganization-2026-11-27/examples-final.json
```

The 48 example groups contain 92 displayed snippets:

- 44 Thor/Odin pairs, with exact expected output and cross-language comparisons.
- One Thor-only compile-time example, explaining why native Odin has no
  corresponding constant procedure-call facility.
- Three deliberate Thor rejections: immutable writes, allocation under a
  `no_alloc` contract and use of an `@(unused)` declaration.

Every snippet is compiled in checked and unchecked configurations. Unchecked
runs use `-o:speed -disable-assert -no-bounds-check -no-type-assert`; Python output
comparisons and explicit native conditionals remain active. Two compile-time
examples also exercise `required`, `normal`, `aggressive` and `override`.
Together these perform 196 native compile/run or rejection invocations.
Temporary sources, executables and isolated caches are automatically removed.

Comparisons demonstrate the displayed behavior, not identical compiler
contracts or allocation strategies. The prose distinguishes enforced purity,
immutability and allocation guarantees, library support shared with Odin,
and the small explicit-thread counterpart to bounded `pmap` scheduling.

## Documentation checks

The three structure tests check every displayed source fence, manifest/index
order, absence of milestone identifiers, local links and heading anchors in the
README and both moved guides, and tracked incoming README anchors.
The final structure rerun passed after guide cleanup. Assertion hygiene also
passed all seven checks. `git diff --check` was clean.

The README suite found and corrected a generic-name collision in an Odin
comparison and an unsupported flag in the test harness's rejection command.
It required no additional compiler changes. The preceding implicit switch-return
implementation and its broader compiler/TLS results are recorded separately in
[its validation report](implicit-switch-returns-2026-11-27.md).

Raw logs/results are retained locally in
`dist/readme-reorganization-2026-11-27`. No downloaded binary release is
published as part of this documentation change; the README explains how to
install the checked-out revision with `--from-source`.

## Follow-up: imports and introduction order

The feature tour now introduces immutability and slice permissions immediately
after imports, before later examples use them. The selective-import example uses
`import "core:fmt" / print, println`. A new basic-package import example uses
`import * "thor:basic"`; the basic helpers remain together in the final feature
section, while the separate `thor:format` package is beside formatted strings.
The linked index and example manifest use the same order. The prior
`basic-library` anchor remains available.

There are now 46 indexed entries, 49 example groups and 94 displayed snippets.
All six affected-example and structure tests passed, with no skips: the three
new/updated pairs ran in checked and unchecked builds, and the three structure
tests checked the full reordered index, code-fence inventory and links. This
follow-up changed documentation and example expectations only. Results are
retained locally as `dist/readme-reorganization-2026-11-27/follow-up.json`.

## Native-package import comparison

The opening import pair now combines comma-separated `core:fmt` selections,
namespace-free `core:math` selection and wildcard `core:strings` imports. The
Odin counterpart uses the same packages with qualified calls. Both versions
produce `Square root: 3` and `Has prefix: true` in checked and unchecked builds.
The example test and all three README structure checks passed (four tests,
no skips). Results are retained locally as
`dist/readme-reorganization-2026-11-27/native-imports.json`.
