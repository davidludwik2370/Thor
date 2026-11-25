# One-to-one Windows debugger comparisons: November 23, 2026

Nine matching plain-Odin controls now cover the nine original Windows debugger
failures. The initial comparisons reproduced every original failing check in
plain Odin through direct CodeLLDB. Investigating the surrounding behavior also
found two Thor adapter defects, which are fixed. **One original test now passes;
eight original tests remain failures.** No Odin compiler or SDK source was changed.

## Reproductions and method

The checked-in [fixture directory](../tests/support/debugger_pairs/README.md)
contains all nine `.odin` / `.thor` pairs and maps each to its original live test.
The Thor fixtures preserve their original operations and source coordinates;
explicit conditional runtime guards replace elidable assertions. Native sources
replace only Thor-specific syntax with equivalent Odin constructs. The `using`
control enables Odin's required file feature on the existing comment line.

The native `print` control has the exact installed procedure body. Its formatting
module and the generic map control's scheduler are existing ordinary `.odin`
libraries. These native programs never invoke Thor lowering. Build commands,
flags, source/binary hashes and complete DAP transcripts are retained.

Each pair was exercised twice in three sessions:

1. Thor program through the normal source-mapping adapter.
2. Plain Odin program directly through CodeLLDB, using a framing-only transport.
3. The same plain Odin executable through the Thor bridge with an empty identity
   manifest, separating bridge behavior from native debug-info behavior.

This is 54 sessions across nine pairs. The comparison checks every repeat
separately; one failed native repeat cannot hide a Thor failure in another.
Separate checked and optimized unchecked builds use explicit runtime verdicts,
including `-disable-assert -no-bounds-check -no-type-assert`. All program result
checks and output comparisons pass. Recorded native watch/breakpoint failures
are retained as failures, rather than being skipped or turned into successes.

The Windows configuration is native x64 Odin `9a69916`, CodeLLDB 1.12.3 and its
bundled LLDB. All compared debug builds use `-debug -o:none -thread-count:1`.
The confirmed source snapshot is `candidate7` under the existing validation run.

## Exact results

Values below are representative observations from the recorded sessions.
Uninitialized-looking integers vary between runs; the raw records retain both.

| Original test / fixture | Expected | Direct plain Odin | Thor after fixes | What remains |
| --- | --- | --- | --- | --- |
| Allocated `fn` body: [allocated](../tests/support/debugger_pairs/allocated/main.odin) | First-body `n = 7` | `n = 0`, then 7 after stepping | **7 in watch and variables pane** | Original Thor test fixed; native entry debug-info issue still reproduced. |
| `using` parameter: [using](../tests/support/debugger_pairs/using/main.odin) | `record.x = 4` at first body line | 0, then 4 after stepping; computed local is 5 | Same native first-line failure | Native parameter display; runtime result correct. |
| Shared `basic.print`: [basic](../tests/support/debugger_pairs/basic/main.odin) | `args.len = 1` | Unavailable at entry; 1 after stepping | Same entry limitation; **step-over stays inside print and then shows 1** | Native variadic-argument display at entry. |
| Deferred branch: [deferred](../tests/support/debugger_pairs/deferred/main.odin) | Return-line 11 breakpoint | Unverified; breakpoint in `observe` works and received line is 11 | Same | No native breakpoint location at that return line. |
| Fallback call: [fallback](../tests/support/debugger_pairs/fallback/main.odin) | First-body `x = 7` | Incorrect large integer; returned value is 8 | Same first-body failure; final value is 8 | Native scalar parameter display at entry. |
| Generic parallel callback: [pmap](../tests/support/debugger_pairs/pmap/main.odin) | First-body `a = 2`, `b = 3` | 8 and 0, then 2 and 3 after stepping; result sum 5 | Same | Native generic parameter display at entry. |
| Static read-only slice: [static](../tests/support/debugger_pairs/static/main.odin) | `values.len = 2`, `values.data[1] = 8` | `values` unavailable by name; computed total 16 | Same | Native static-local visibility. |
| Call inside switch: [switch](../tests/support/debugger_pairs/switch/main.odin) | First-body `value = 6` | 0 at entry; computed local 12 | Incorrect entry value; computed local 12 | Native scalar parameter display at entry. |
| Unicode inline column: [unicode](../tests/support/debugger_pairs/unicode/main.odin) | UTF-16 column 13 | Native column 0 | Approximate mapped column 5 | PDB provides no precise column for this stop. |

Each unresolved check above fails in the direct native session as well as the
identity-bridge control. Following the fixes, there are no additional Thor-only
failed observations in the paired Windows matrix. This establishes the scope of
the remaining limitations in this tested toolchain; it does not determine for
every case whether the defect is in Odin's PDB emission or LLDB's interpretation.

## Thor corrections

**Stepping inside a library.** The adapter previously skipped installed runtime
sources even when the user had explicitly stopped inside one. `next` in
`basic.print` therefore returned to `main`, losing the intended inspection scope.
Stepping now honors an explicit library stop and calls within that runtime root.
Stepping from application code still skips runtime implementation details;
compiler-generated helpers and source-less routines retain their existing rules.

**Available owning arguments hidden by a helper frame.** An owned pure function
can have a transparent compiler-created body frame. At its first statement, PDB
reports that frame's `n` as unavailable, while the real owning function frame
already has `n = 7`. CodeLLDB returns a successful response containing an
unavailable marker, so the adapter's earlier error-only fallback did not apply.

The adapter now exposes the real owning value in watches and the merged variables
pane only for that specific pure-body wrapper, a simple identifier, an explicit
unavailable marker, and native metadata identifying both bindings as scalar
procedure arguments. It does not substitute arbitrary expressions, shadowing
locals, pointer/aggregate bindings, optimized-out values or metadata failures.
Tests cover those rejection paths and a live shadowed-local case. It reads native
debugger metadata and existing values; it does not evaluate target-side setup,
guess expected values or add instructions to the application.

Both corrections are confined to the VS Code adapter. Compiler output, normal
build time and generated-program runtime work are unchanged. The editor package
is version **0.22.5**.

## Remaining practical work

There are eight unresolved original Windows tests: five entry-parameter display
cases, static-local visibility, a deferred-return breakpoint and Unicode column
precision. Stepping past entry restores the recorded aggregate, variadic and generic
parameter displays; the scalar calculations also produce their expected results.
The initial display remains wrong/unavailable. Native debug-info /
LLDB fixes are needed for those cases; the strict original tests remain active.
The adapter does not invent missing source columns or breakpoint locations.

The isolated Windows review profile receives the updated extension. The desktop
review launcher still uses the previously validated native compiler and TLS,
which did not change in this investigation. This work does not resolve the
separate Tina inlining-sensitive integration mismatch from the earlier report.

## Repeating the audit

```sh
python3 tools/compare_windows_debugger.py --output /path/to/new-results --repeat 2
python3 -m unittest tests.test_debugger_native_controls
node --test editors/vscode/test/debug-adapter.test.js
```

Per-session requests, responses, frame locations, assembly snippets and watch
values are saved by the comparison tool. The raw matrices, failed exploratory
attempts and final suite logs are retained under
`integration-projects/results/windows-2026-11-23` on the Mac, with matching artifacts
under the Windows validation run. A successful paired comparison means no extra
Thor failures; it does not turn the eight native limitations into passing tests.

## Final verification and evidence identity

- Windows: all 54 paired sessions completed; no Thor-only or identity-bridge-only
  failed checks. One original Thor failure is now an improvement over native.
- Full Windows selection: 57 tests, 49 passed and the eight native limitations
  above failed; no cleanup errors. This includes 54 live debugger cases and three
  cleanup tests. None of the eight failures was skipped or weakened.
- Mac: all 67 debugger/control/hygiene cases passed, followed by three passing
  cleanup regressions and the final hygiene/fixture checks.
- Extension tests: 166 passed on Mac; 165 passed and one POSIX-only skip on Windows.
- The real Windows VS Code run/breakpoint/step/watch/restart workflow passed.
  Package 0.22.5 was installed in the isolated review profile, and its packaged
  adapter is byte-identical to the tested source.
- A Windows exploratory run hit a transient fixture-directory sharing violation
  after debugger exit. Bounded cleanup retries now cover that race; persistent
  locks, unrelated errors and non-Windows permission errors still fail. Tests
  verify all three cases.
- Assertion hygiene and the frozen Python compiler fingerprint pass. The native
  compiler binary is unchanged from the preceding validation, so these fixes add
  no normal compiler or generated-program runtime work.

Raw archive: `integration-projects/results/windows-2026-11-23/debugger-pairs-evidence.zip`.
It includes the before/after matrices, native sources, full protocol transcripts,
assembly listings and strict suite logs. All archive entries and the extension
package were individually checksum-verified after transfer to the Mac.

Tested snapshot: `d1346dbd871b5087ca9d3e7bd07f923f3cf54d8fc6475a5d73eec0f35288618e` (1940 entries).

Adapter SHA-256: `c01c0b47c1d0d61c59ecf4cf562b279cac87991a691089fb335a5ff9f8a72f34`.

Windows VSIX SHA-256: `b7863b26453445828ac8a8439350155675039815eeac3d41907afdbf70259676`.

Evidence archive SHA-256: `6e7cb98aa902293b5dab891083750420e3fc5d4ad43b4db66d8a460521106162` (866 verified entries).

This final verification prose was added after the tested snapshot was recorded.
