# Evidence inventory

- `odin-report.txt`: installed compiler/OS/hardware/LLVM report.
- `original-crash.txt`: selected crash facts, explicitly a summary rather than a
  raw crash dump. The exact SIGSEGV did not reproduce in that stress batch.
- `recurrence-2026-10-29.txt`: a later stock-compiler crash with the same stack
  and offsets, a null source pointer and length 30; the isolated test retry passed.
- `recurrence-2026-11-09.txt`: another stock-compiler crash with matching initial
  offsets, a null source pointer and length 29. The isolated retry and the clean
  full regression rerun passed. This is observational evidence, not a new TSan
  experiment or independent proof of the precise racing access.
- `type-name-race.txt`: selected original TSan access pair with source line
  numbers; personal temporary paths are replaced with `<temporary>`.
- `summary.json`: aggregate results from the original investigation, including
  the larger print fixture, 2,402 stock builds and 200 prototype release builds.
- `bundle-validation.json`: fresh validation of the standalone hello fixture and
  runner after consolidation into this folder. Compiler command paths are
  omitted; statuses, report counts, emitted IR counts and fixture SHA-256 remain.
- `minimal-fixture-race.txt`: one selected baseline report from that fresh hello
  validation. Function names are symbolized; this copied compiler binary did not
  resolve source lines in the fresh run, so the earlier trace supplies those.

The fresh checks use the same pinned original/prototype compilers as the
investigation, with no TSan suppression option. These are not new tests of current
upstream master. Historical and fresh warning totals differ because the
fixtures/invocations/scheduling differ. In particular, a fresh single-thread
check produced one unrelated parser/global-file-table report despite removing
the observed name-cache race. The prototype also retains other TSan warnings.
Those other findings are not validated or fixed by this bundle.

The runner's `name_cache_read_reports` field counts reports whose first-access
stack starts in `lb_get_entity_name`; it does not indiscriminately count a name
appearing in a previous-writer stack. It is a focused diagnostic classification,
not a general compiler correctness test. Negative compiler exit status `-6` in
these checks is TSan's termination after warnings, not a recurrence of the
original stock-compiler SIGSEGV. Every fresh check emitted LLVM IR.

Raw stderr, stdout and LLVM IR from the fresh checks were retained locally in
the temporary validation output directories; this portable bundle includes
selected traces and structured results rather than unrelated sanitizer output.

The November 15 integration-hardening run repeated the null-source string
signature with length 22 in a native `#run` helper compilation. The extracted
record is in `recurrence-2026-11-15.txt`.
