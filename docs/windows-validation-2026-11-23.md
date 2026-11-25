# Native Windows validation: 2026-11-23

This is the initial pass. See the [follow-up report](windows-validation-followup-2026-11-23.md)
for subsequent fixes, wider coverage and the current remaining limitations.

The existing key-based SSH connection worked for source transfer, native builds,
tests and log collection. No security-review interruption occurred during this
run. Work used the isolated snapshot at:

```text
C:\Users\david\Code\thor-validation\runs\20261123-480b053b\src
```

The original Windows checkout was retained. Account, firewall, SSH and machine
environment settings were not changed. Compiler builds used two Cargo workers.

## Tested source and tools

- Base Git revision: `5234d28fc753c02bc8c67cb939e19c3b64ca1f02`, plus the working tree.
- Snapshot: 1,905 source files, checked individually against SHA-256 manifests.
- Final source fingerprint: `96c741426ff340f9892ca4623879b181cdcde83acab74e7c70b227ebdb1ed52b`.
- Native Windows 11 x64, build `10.0.26200.9974`.
- Rust/Cargo 1.100.0, MSVC developer tools, Odin `dev-2026-11-nightly:9a69916`.
- Python 3.15.0, Node 24.22.3 and CodeLLDB 1.12.3.

The fingerprint identifies the tested snapshot; this final report was written
after creating that snapshot. The selected Odin installation is separate from
the Mac's reviewed October SDK. WSL was not used.

## Results

| Group | Result |
| --- | --- |
| Native release compiler and TLS builds | Passed |
| Native Odin baseline | Passed |
| Thor smoke: checked and optimized unchecked | Passed |
| Rust library unit tests | 114 passed |
| TLS native unit tests, checked | 1,170 passed |
| TLS native unit tests, unchecked | 1,170 passed |
| Focused compiler acceptance | 239 run, 237 passed, 2 explicit skips |
| Live LSP/hover transport | 149 run, 145 passed, 4 explicit skips |
| Additional TLS and path regressions | 22 passed |
| Parallel mapping runtime/compile-time probes | Passed, checked and unchecked |
| VS Code extension tests | 159 run, 158 passed, 1 POSIX-only skip |
| CodeLLDB source debugging probes | 4 run, 1 passed, 3 failed; see below |

Unchecked native paths used `-disable-assert -no-bounds-check -no-type-assert`.
Program verdicts used captured output, exit status and explicit runtime guards.
The ordinary checked smoke took about 0.83 seconds and the optimized unchecked
smoke about 2.66 seconds, including native compilation and execution. These are
functional probes, not a matched performance benchmark. Building the Thor tool
itself with Cargo/LTO takes several minutes and is a separate measurement.

Mac verification also passed 116 Rust unit tests, 1,170 TLS unit tests in each
configuration, 194 compiler/diagnostic/CLI checks and 149 LSP/hover tests.
Assertion hygiene and the frozen Python reference fingerprint checks passed.
The Windows hover-layout audit compared 8,216 size/alignment results across eight
target profiles. At collection, no test or build processes remained for this
snapshot, and Windows reported 21.44 GiB of available memory.

## Corrections made

- Generated directory links can be replaced or removed on Windows, including
  dangling links, without following the link or changing its target. Windows
  requires directory-link removal rather than ordinary file deletion.
- Test launchers and oracle outputs select native `.exe` filenames. SIMD layout
  expectations use the selected host ABI. A temporary expected string in that
  fixture is now freed explicitly.
- Test fixtures no longer depend on Unix `/tmp` aliases or Windows verbatim-path
  spelling. Help tests distinguish option entries from explanatory prose.
- The LSP test client treats encoded and unencoded Windows drive colons as
  equivalent URI spellings, including workspace-edit keys. Other escaping is
  retained.
- TLS recognizes result queries and `pmap` metadata when AST filenames use
  Windows separators. Callback aliases and polymorphic result fields retain
  their inferred types.
- TLS discovers native Windows compilers rather than the POSIX launcher and
  normalizes a queried runtime collection before indexing it.
- Open-buffer diagnostics retain editor paths through directory links, including
  related locations. Unsaved new files can use the native compiler checker.
- Compiler diagnostic restoration accepts both Windows path spellings and maps
  known support-library views to their authoritative runtime sources. Format
  contract errors point to the user's interpolation rather than a cached library
  file; byte-offset tests account for Windows CRLF source files.

These changes affect compiler filesystem operations, diagnostics, editor analysis
and tests. They add no work to generated programs. The Python compiler remains
frozen and unchanged.

## Explicit capability limits

The two compiler-gate skips remain visible:

1. The selected Windows SDK does not match the reviewed fatal-reporting source
   profile used to prove allocation freedom. Ordinary aggressive folding stays
   at runtime when that proof is unavailable. A regression verifies this safe
   fallback; explicit `#run` and pure-function evaluation remain supported.
   A Windows SDK/I/O effect-summary review is still required.
2. Metadata-backed scalar evaluation caching is intentionally disabled on
   Windows because its native file-identity signature is not implemented.
   Evaluation still runs correctly; this is a performance capability gap.

The LSP suite also has four existing Windows skips for executable Python/POSIX
mock-compiler launchers. The extension's one skipped test exercises its POSIX
shell fallback. These skips are not Windows success verdicts.

## Debugger findings left open

Basic source breakpoints and stepping passed. The other three probes found:

- A local `@(static, rodata)` slice is unavailable by name in watches.
- A by-value record parameter displays `0` where the program receives `4`.
- Generic callback parameters display incorrect values at their first statement.

Plain Odin binaries built with `-debug -o:none`, without Thor lowering, reproduce
the missing static local and incorrect record/generic argument displays through
the same CodeLLDB path. Their explicit runtime result guards pass. This excludes
Thor lowering as the cause; further debugger/toolchain isolation is still needed.
The failing probes remain failures and were not suppressed.

Representative native controls:

```odin
Record :: struct { x: int }
read :: proc(record: Record) -> int {
    value := record.x + 1 // Watch record.x here; expected 4.
    return value
}
main :: proc() {
    result := read(Record{4})
    if result != 5 { panic("result") }
}
```

```odin
main :: proc() {
    @(static, rodata) values := []int{7, 8}
    total := values[0] + values[1] // Watch values here.
    if total != 15 { panic("result") }
}
```

## Repeating and extending validation

See [the stage runner](windows-validation.md). Stage JSON records retain exact
commands, source fingerprints, exit statuses, timings and log paths. Local raw
results and prior failed attempts are under
`integration-projects/results/windows-2026-11-23/`. The final collected stage logs
and JSON records are in `final-results.zip` in that directory; its archive
integrity was verified. Windows copies are under the snapshot's
`windows-results/` directory.

The complete maintained compiler acceptance suite, GitHub project integration
matrix, full debugger suite and live VS Code GUI checks have not been certified
on Windows by this initial pass. The results above establish a working native
validation workflow and substantial core coverage, not a complete Windows
release certification.
