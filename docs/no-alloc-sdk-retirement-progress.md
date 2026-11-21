# SDK allocation audit and Python retirement work log

Current phase: completed; final coverage and limits are recorded in `python-retirement.md`.
Entries below are chronological checkpoints; later results supersede earlier pending work.

Authorized order:

1. Audit/fix provably nonallocating SDK calls; retain runtime-dependent rejection.
2. Snapshot and freeze the Python reference.
3. Harden the complete language and cross-feature interactions.
4. Validate CLI, vet, TLS, debugger, caches, integration, checked/unchecked and performance on Mac/Linux.
5. Make mandatory verdict tests independent of the Python compiler.
6. Archive Python compiler from normal build/install/test paths; preserve Python tooling.

Initial SDK matrix: 99 native-checked probes in `tests/fixtures/no_alloc_sdk_cases.json`.
The first pass found 28 valid nonallocating probes rejected (plus two initially invalid probe spellings, now corrected).
Primary causes: missing caller-location/intrinsic result facts; multipointer offset result shape; labels/target constants; failure-only runtime reporters; lost callback return identities; callback state through in-place sorting.

Pre-audit executable: `/tmp/thor-before-sdk-audit`.
Tracked working diff backup: `/tmp/thor-before-sdk-audit.patch`.
Existing pre-task dirty work is retained; no broad reset or cleanup is authorized.

Correctness follow-ups uncovered during this pass, to be closed before retirement:

- Multiple-result declaration initializers must never zero-initialize an unmodeled output.
- Runtime numeric casts must not reuse a pre-conversion constant fact.
- Computed callee producers must be checked even when the returned function has a native summary.
- Bound return-fact cache, recursive pointer snapshots and aggregate facts.
- Raw-pointer/transmute record reinterpretation must not preserve incompatible field identities.
- Repeated algorithm callbacks must not assume mutable userdata contents remain unchanged.

Validation is in progress. This log is not a completion report.

## Expanded audit checkpoint (2026-11-21)

The matrix now has 201 native-valid probes (197 accepted, four deliberately
allocating controls). The full first 197 passed against native Odin; the four
matrix-intrinsic additions passed separately. Final combined release checks
and timing are in progress. No Python compiler implementation has been changed
in this audit; Python retirement itself has not started and no tag has been made.

Additional corrections include fixed-capacity append/resize and their result
types; clear/pop/removal; atomics and native matrix/SIMD shapes; labeled native
statements; clocks/process queries; C memory/string boundaries; generic `when`
annotations and callback identities; callback factories and named results;
in-place sorting/key callbacks; verified deferred cleanups; and selected native
failure guards. SDK summaries remain source-pinned. Every producer/ordinary
argument/callback body still undergoes checking. Pointer facts retain their
original pointee type across rawptr erasure and reject incompatible views.

Native matrices retain dimensions and row/column layout. Native matrix
multiplication, matrix/vector results and scalar broadcasts must retain their
actual result shape, as must constrained generic parameter aliases. These have
positive and allocation-hiding negative regressions. Byte/rune aliases are
canonicalized for native type equality. Syntax-only bit-set identity markers
are deliberately not exact enough to prune branches; this prevents an unsafe
proof. Refining structural bit-set identity remains a possible precision task,
not an exemption for a potentially allocating branch.

Validation so far (before the last arithmetic/identity changes): 197 native SDK
probes; 19 SDK regression methods with checked and unchecked zero-allocation
runs; 13 live TLS tests; 98 Rust unit tests; seven assertion-hygiene tests.
A 156-method broad pass had one obsolete expected rejection (a now-provable
returned callback); its test was corrected to keep genuinely unknown callbacks
and growing dynamic arrays negative. The last flow-context pass passed all 45
RNG/SDK methods. Final release, aggregate-shape, timing and broad-suite reruns
are still required. Rust protocol examples also needed the previously added
`native_policy_only` Options field; all eight initializers now include it.

Local Linux: Docker Desktop was restarted and the existing
`thor-linux-validation` container is running (6 GiB cap, bind mount /thor).
The native workspace is /work/thor; pinned native Odin is /opt/odin-8169255/odin.
The workspace is an old copied snapshot (no .git); do not overwrite its native
rust/target or huge .thor-cache while updating sources. No Windows actions.

## Final SDK pass in progress

The native-oracle corpus now contains 207 cases, including fixed-buffer AES,
ChaCha20, SHA-3 and BLAKE2, plus static/dynamic type-information queries and an
allocating query producer control. SHA-3, BLAKE2 and ChaCha20 passed checked
native baselines and allocation contracts. AES exposed a missing native SIMD
`swizzle` result shape, now corrected; its final rerun is pending.

Hardware leaves are a finite exact-link catalog generated offline from resolved
LLVM instruction records. Only reviewed CPU/math/argument-memory operations are
eligible, excluding explicit side-effect intrinsics and managed/TLS/coroutine
runtimes. Exact SDK source paths and the complete SDK source fingerprint remain
required. Ordinary wrapper bodies and arguments still undergo effect checking.

Additional soundness checks cover implicit `any` boxing and `#by_ptr` addresses,
`any` header versus payload facts, implicit integer truncation and floating-point
rounding. These affect analysis only. The seven-method focused pass passed,
including checked/unchecked runtime zero-allocation tests. No growing-array or
runtime-capacity exemption was introduced. An explicitly fixed-capacity dynamic
array is a different native storage type whose operations cannot grow the heap.

## Retirement checkpoint and live hardening

- Commit `f4b0d2c` and annotated tag `python-reference-2026-11-21` preserve the
  reference. `docs/python-reference-frozen.json` hashes all frozen `.py` files
  and the launcher. No frozen Python implementation was changed afterward.
- Native runtime preparation uses Rust. Maintained tests default to Rust-only,
  require explicit nonempty stdout expectations, and forbid Python compilation.
  Explicit old implementation tests and Python variants remain opt-in. The
  maintained gate is `tools/test_compiler.py`.
- All 207 initial native-valid SDK probes passed. The expanded corpus now has
  219 cases: sized-boolean zero/proof controls and direct POSIX/Linux descriptor
  I/O are included (platform exclusions are explicit).
- A focused seven-method boxing/conversion pass and later five-method pass
  succeeded, including zero-allocation SHA/AES known answers in checked and
  unchecked configurations. The sandbox and three examples ran in both modes.
- Quiet paired timings retained identical native code in all eight workloads;
  see `benchmarks/no-alloc-sdk-2026-11-21.{md,json}`.
- A 272-method broad gate had only two subtest failures from a new AES fixture's
  ambiguous empty compound literal; its corrected checked/unchecked runtime
  test passed. A 332-method native-only gate found two missing standalone
  expectations (format stdout and UTF-16 column counting), now corrected.
- The full Mac/Linux native gates are running against the saved release
  executable. Their logs include already-fixed test-harness Python-selection
  errors and already-fixed compiler issues; final focused reruns are required.
- Hardening corrected automatic folding of replaceable `fn` callback defaults,
  generated arithmetic/type-query cascades, and the generated-error budget
  hiding independent interpolation fields. The implicit backend budget is
  10,000; explicit native user limits remain authoritative.
- Sized `b8`/`b16`/`b32`/`b64` values now have Boolean zero facts. Their native
  type predicate and struct bitwise fields are supported consistently. Tests
  include aliases, generics, padding, compile time, checked and unchecked runs.
- Native TLS overloads now map cursor positions independently, separate grouped
  fields, retain native directives, and select a candidate exposing the active
  parameter. The first 1,168-test rerun passed; the additional directive test
  is being run.
- Rust unit/example checks passed all 98 unit tests. All 149 VS Code unit tests
  passed. Live debugger tests in both native gates have progressed successfully.
- Upstream verification passed odecs (125), muninn (39), toml_parser (7), anvil
  tensor/nn (71/13), and Skald (108), plus build-only entries.
- Linux build initially filled its 59 GiB volume. Only the 27 GiB regenerated
  SDK parser cache was removed; sources, binaries, results and native build
  artifacts were preserved. Native Linux release compilation then passed.

Important pending work: complete the Mac/Linux gates, fix genuine regressions,
rerun failures/new tests using final binaries on both hosts, rerun the expanded
SDK audit and final timing, run final editor/native runtime checks, update the
retirement report with actual outcomes and commit the verified retirement batch.
Windows remains deferred and is not contacted.

Local run records (temporary paths, not committed verdict claims):

- Mac full gate: `/tmp/thor-native-hardening-mac.{log,json}`
- Linux full gate, in container `thor-linux-validation`:
  `/tmp/thor-native-hardening-linux.{log,json}`
- Linux workspace `/work/thor`, SDK `/opt/odin-8169255/odin`, Cargo
  `/root/.cargo/bin/cargo`, CodeLLDB `/opt/codelldb/extension`.
- Native-only initial gate `/tmp/thor-native-oracles-first.{log,json}`.
- Latest debug candidate `rust/target/debug/thor-rust`; main installed release
  still predates the last hardening fixes. Do not rebuild/replace it while the
  full gates are running; their cache/behavior measurements should stay stable.
- `/tmp/thor-retirement-followup-targeted.log` had three independent-field
  subtest failures; raising the generated native budget resolved them.
- `/tmp/thor-retirement-final-edges.log` then passed that diagnostic method but
  initially failed the new Boolean-struct fixture's field separator; corrected
  fixture passed in `/tmp/thor-retirement-boolean-struct.log`.
- `/tmp/thor-sdk-fd-bool.json` passed every applicable Boolean/POSIX probe.
- `/tmp/thor-publish-golden-capture2.log` passed the explicit historical/native
  comparison before recording standalone package/dependency expectations in
  `tests/fixtures/publish-packages.json`. Verify native-only mode next.

## Latest native gate follow-up

- Expanded SDK audit on the latest debug candidate passed all 218 Mac-applicable
  probes (Linux-only descriptor probe excluded explicitly):
  `/tmp/thor-sdk-expanded-debug.{json,log}`.
- All 98 latest Rust unit tests and all seven assertion-hygiene tests passed.
- Native-only packaging gate passed five methods, including the standalone
  package/dependency golden snapshots.
- Native scheduling helpers no longer instantiate the historical Python
  evaluator unless explicitly requested. The 95-method acceptance rerun passed.
  The scheduling module itself now has explicit native value/output expectations
  and is removed from the archival-only inventory; its 42-method pass is running.
- Old gate harness failures from CLI packet equality and hardcoded dual-engine
  helper counts are corrected. A formerly nonconverging borrow proof now reports
  the actual `work -> forward -> write` violation; its native regression passes.
- The first VS Code run used the saved old release and reproduced already-fixed
  arithmetic cascades. The editor fixture now accepts `THOR_TEST_COMPILER` in
  its isolated profile, enabling a latest-candidate run without replacing the
  executable underneath the ongoing broad gate. Candidate rerun is running.

## Complete first gates and additional hardening

- Initial broad native gate completed: Mac selected 4,002 methods, ran 3,969
  (three protocol setup timeouts prevented 33 methods); Linux ran 4,002. Their
  failures include stale dual-engine harness assumptions, the earlier diagnostic
  fixes, obsolete caller-default rejection expectations, missing stdout oracles,
  and newly discovered matrix/SIMD struct classification and operator-growth bugs.
  These are not an all-green final verdict. Final native replays are pending.
- Updated the purity inventory to the documented caller-metadata contract:
  captured/default metadata is an input; direct source-dependent body operations
  remain rejected. A caller-location default/override/#run acceptance test passes.
- Native scalar range stdout, consumed native vet headers and fixture-directory
  iteration now have standalone expectations; explicit Python Project flag API
  checks are archival. No Python compiler implementation was modified.
- TLS root discovery now queries `thor root` when an adjacent runtime is absent.
  Explicit collections avoid the query; argv preserves spaces/Unicode, and failed
  queries have a bounded wait. Native tests and all 1,169 unit tests pass.
  The full isolated VS Code suite passed on the debug candidate in
  `/tmp/thor-retirement-candidate-vscode3.log`.
- The complex pure-pmap case exceeded Linux's 6 GiB limit. An isolated Mac sample
  identified exponential nested unknown-type arithmetic dispatch. Abandoned value
  boxing experiments are removed. The final candidate dispatches an entire direct
  operator tree once and uses a compatible leaf as a known-record type anchor.
  Native scalar operations and Boolean storage remain native; no runtime wrapper
  is added. The debug repro ran in 9.70 s including compile-time compilation and
  execution, with 121 MB Thor physical footprint (484 MB child-inclusive max RSS).
- All 51 struct operator/padding tests passed. Five additional scaling tests pass:
  40-operator generic/concrete trees, side effects and #run, literals, native
  rectangular matrix products, native Boolean bit patterns, and identical
  optimized generic/manual arithmetic instructions.
- Release main/example builds are running on Mac and Linux. The final replay
  selection covers the failed classes, protocol setup failures, all affected
  arithmetic/pmap/purity fixtures, SDK additions and native retirement oracles.
  Final SDK audits, quiet paired timing, editor/debugger checks and documentation
  reconciliation remain pending.

## Final release replay status and cache follow-up

- Release SDK audits passed all applicable native-valid cases: Mac 218, Linux
  219, with nine expected allocating controls each. Logs/results are
  `/tmp/thor-sdk-final-{mac,linux}.{log,json}` (Linux files still in container).
- Release VS Code integration passed. Root discovery also passed with a separate
  unchecked TLS binary (both tests, 2.65 s). Latest Rust unit pass: 98/98.
- Final representative upstream verification passed all recorded outcomes again,
  including Skald 108 tests.
- Broad native replays are running: Mac includes live debugger checks; Linux
  includes 1,028 selected methods. They use the flat-operator release candidate.
- The expanded same-argument caller-location test revealed a scalar fast-cache
  bug: two `#run capture(7)` sites produced 12/12 instead of 12/13. Disabling
  caches produced the correct values. Debug fix now uses a `scalar-sites-v2` key
  and does not publish the scalar shortcut when any source directive is in the
  call/dependency graph; the ordinary emitted-helper cache remains active.
  Corrected checked/unchecked test passes (12,13,14 including automatic folding).
  Source change `rust/src/evaluationscalar.rs` has not yet been built into the
  installed release, and Linux has not yet received that fix.
- One final replay harness error was `native` shadowing its imported test module
  in `test_rust_generic_purity`; renamed the local result.
- One new flat-dispatch diagnostic cascade remains for
  `f"{(1-p_loss) * (50+50):.2f}"` with `thor:basic`: an unnecessary struct-helper
  error appears on the known scalar `50+50` subtree. Planned correction: stop
  collecting known non-record scalar subtrees in `struct_tree_value`, treating
  them as ordinary rendered leaves; always collect the root. Do not hide an
  independent native error. Existing regression covers this already.
- A Cargo scheduling-protocol build is currently active on Mac from replay
  setup; avoid editing Rust inputs until it completes. Linux replay has passed
  pmap, purity, scheduling and most arithmetic checks; remaining failures are
  the known scalar cascade and the now-corrected test-module shadow.

## Final corrections under validation

- The known scalar diagnostic cascade is fixed by leaving proven non-record
  subtrees native inside the compact operator dispatch. Both diagnostic fixture
  families pass on the latest debug candidate; the final Linux release contains
  this correction.
- Caller-metadata scalar cache identity now uses the v2 key and falls back to
  the ordinary helper cache for source directives. The 62-method cache suite
  passed on Mac debug, and the expanded caller-location test passes checked and
  unchecked. Final Linux release includes this change too.
- Corrected the native-only generic-purity harness import/shadowing, and
  inventoried the historical mocked Python driver entry-point test separately.
  Native filesystem-alias/launcher protection remains a mandatory acceptance
  test; no production protection was removed.
- Latest Rust library units pass on Mac and Linux: 98 each. All seven assertion
  hygiene tests and the frozen-reference source check pass on Mac.

## Completed retirement and final release validation

- Both final native release executables and protocol examples are rebuilt.
  The last 185-method release follow-up passed on each platform. Distinct current
  coverage across initial full gates and corrective replays is 4,051 passed / 3
  environment skips on Mac, and 4,004 passed / 50 platform skips on Linux, with no
  unresolved current failures. These are cumulative coverage counts, not one
  fresh all-green full-suite run. See `validation/python-retirement-2026-11-21.json`.
- Final SDK audits pass: Mac 218, Linux 219; nine expected allocating controls
  remain rejected on each platform. All native baselines compile successfully.
- Final VS Code release integration passes all 71 checks; native TLS units pass
  1,169 per platform. Rust units pass 98 per platform. Editor Node units pass
  149, assertion hygiene passes seven, and eight checked/unchecked demos/sandbox
  runs preserve exact expected output. Python frozen-source integrity passes.
- Representative upstream usable entries match native baselines on both hosts:
  odecs 125, muninn 39, toml_parser 7, anvil tensor/nn 71/13, Skald 108. The
  native-failing anvil models/flux entry remains explicitly unavailable. Linux's
  initial 4 GiB per-test guard was below the known 4.3 GiB large Skald workload;
  the bounded 5 GiB retry passed with the VM limit unchanged at 6 GiB. No new
  OOM kills occurred in the fixed arithmetic repro. Large SDK/helper memory
  cost remains a documented limitation, not a claim of universally small builds.
- Quiet final paired timing covers eight workloads, nine transpiles and three
  builds per variant. All main native source comparisons match. Ordinary
  unannotated transpiles remain roughly 35–141 ms in these samples; enabled
  SDK proofs have their explicit analysis cost and introduce no runtime wrapper.
- Python maintenance is frozen at `python-reference-2026-11-21` (`f4b0d2c`).
  Mandatory verdicts are native-only, historical checks are explicit opt-in,
  normal runtime preparation invokes Rust, and documentation records the policy.
  Windows remains deferred; no Windows work was performed in this continuation.
