# SDK allocation audit and Python retirement work log

Current phase: standard-library `no_alloc` correctness, before the retirement gate.

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
