# Odin parallel code-generation name-cache race

This folder is a self-contained upstream reporting bundle. No Thor installation
is needed to run its reproducer. Nothing has been submitted to GitHub, and the
experimental compiler patch has not been installed.

The confirmed finding is a C++ data race in Odin's cached LLVM type names at
commit `8169255b97b85d6a08691d99764f51fb01f92b5a`. It closely matches an
intermittent compiler crash. A later regression run repeated the same SIGSEGV
signature; see `evidence/recurrence-2026-10-29.txt`.
A Rust-migration regression run repeated the null-source/nonzero-length string
signature on November 9; see `evidence/recurrence-2026-11-09.txt`.

## Report material

| File | Purpose |
| --- | --- |
| [issue-title.txt](issue-title.txt) | Suggested GitHub issue title. |
| [issue-body.md](issue-body.md) | Ready-to-paste report, with environment, example, commands, evidence and limitations. |
| [main.odin](main.odin) | Minimal plain Odin example. |
| [reproduce.py](reproduce.py) | Python 3 runner that retains output and classifies the observed race reports. |
| [evidence/](evidence/) | Environment report, selected TSan access pair, crash summary and recorded experiment results. |
| [investigation.md](investigation.md) | Full explanation of the source bug and experimental results. |
| [type-name-cache-publication.patch](type-name-cache-publication.patch) | Experimental three-file Odin compiler patch, based on the pinned commit. |
| [PR-NOTES.md](PR-NOTES.md) | Patch design, validation already performed and work remaining before a PR. |
| [ODIN_LICENSE](ODIN_LICENSE) | Original Odin source license, preserved for the altered-source patch. |
| `attachments.zip` | Portable copy of this bundle for attaching to a future issue. |

The logs and report omit personal checkout paths. The bundle contains no compiler
binaries, generated LLVM IR or full system crash dump. The patch is an altered
source version, not an upstream Odin change; original Odin copyright is retained.

## Reproduce the race

Run these commands from this folder. The runner uses only Python's standard
library and the supplied Odin executable. Use a new, empty output directory for
each experiment; existing evidence is never overwritten.

A normal Odin compiler checks the fixture but will usually not crash:

```sh
python3 reproduce.py --odin odin --runs 3 --output-dir /tmp/odin-codegen-stock
```

For reliable detection, instrument the **C++ compiler executable** with
ThreadSanitizer. Passing Odin's `-sanitize:thread` to `main.odin` instead would
instrument the compiled example and would not test this compiler race.

In a separate Odin source checkout at the pinned commit, build with:

```sh
LLVM_CONFIG=/opt/homebrew/opt/llvm/bin/llvm-config \
CXXFLAGS='-O1 -g -fsanitize=thread -fno-omit-frame-pointer' \
LDFLAGS='-fsanitize=thread' ./build_odin.sh debug
```

These are the tested macOS/Homebrew settings; adjust `LLVM_CONFIG` for another
installation. The script also runs a demo after producing the compiler, so a
sanitizer failure from that demo is distinct from a C++ compilation failure.

Then run the instrumented compiler against this folder's fixture:

```sh
TSAN_OPTIONS='halt_on_error=0:history_size=7' \
python3 reproduce.py \
  --odin /path/to/odin-tsan --odin-root /path/to/Odin \
  --expect-name-cache-race yes --output-dir /tmp/odin-codegen-before
```

Use the SDK checkout matching the compiler for `--odin-root`. Earlier, unrelated
TSan reports must be allowed to continue to reach code generation. If stack
frames are not symbolized, add
`external_symbolizer_path=/path/to/llvm-symbolizer` to `TSAN_OPTIONS`; the runner's
classification requires symbolized function names.

Each output directory contains `results.json`, compiler stderr/stdout and the
emitted `.ll` files. The optional `--expect-name-cache-race` mode requires LLVM IR
and checks reports whose **first access stack** starts in `lb_get_entity_name`.
It does not require a fixed report count and does not classify every compiler
race. In expected-race mode, the runner can succeed despite the compiler's
sanitizer exit status; inspect the retained diagnostics too. A TSan termination
after warnings is distinct from the original stock-compiler SIGSEGV.

Controls, run with the original instrumented compiler:

```sh
TSAN_OPTIONS='halt_on_error=0:history_size=7' \
python3 reproduce.py --odin /path/to/odin-tsan --odin-root /path/to/Odin \
  --thread-count 1 --expect-name-cache-race no \
  --output-dir /tmp/odin-codegen-one-thread

TSAN_OPTIONS='halt_on_error=0:history_size=7' \
python3 reproduce.py --odin /path/to/odin-tsan --odin-root /path/to/Odin \
  --single-module --expect-name-cache-race no \
  --output-dir /tmp/odin-codegen-single-module
```

To compare the prototype, apply the patch only in a separate matching compiler
checkout, rebuild with the same TSan flags, and repeat with
`--expect-name-cache-race no`. See [PR-NOTES.md](PR-NOTES.md) for scope and limits.

## When ready to file

The target is the [official Odin issue tracker](https://github.com/odin-lang/Odin/issues).
Before posting, search open and closed issues/PRs for `ir_mangled_name`,
`lb_get_entity_name`, and parallel code-generation races, and check whether current
upstream still has the unsafe cache. A reliable current duplicate check was not
completed during preparation. The recorded evidence is for the pinned commit;
it does not establish the state of a newer compiler.

Use `issue-title.txt` and `issue-body.md`, and attach `attachments.zip` if useful.
The report can stand on the race evidence without proposing the patch. If a PR
is preferred, use the separate notes to finish upstream validation first.
