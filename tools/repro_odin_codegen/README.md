# Odin type-name cache race

This is a plain Odin reproducer and an **experimental, uninstalled** compiler
patch, prepared during the Thor investigation. The original compiler source is
Odin, copyright Ginger Bill; its [license](ODIN_LICENSE) is preserved here.
The patch is an altered source version, not an upstream Odin change.

See [the investigation](../../docs/odin-codegen-race.md) for scope and evidence.
`evidence.json` records the compact results; `type-name-race.txt` preserves a
selected original TSan access pair. Temporary paths in that trace are redacted.

Run ordinary compiler checks (a crash is intermittent, so success alone does not
establish that this race is absent):

```sh
python3 tools/repro_odin_codegen/reproduce.py \
  --odin odin --runs 100 --output-dir /tmp/odin-codegen-stock
python3 tools/repro_odin_codegen/reproduce.py \
  --odin odin --single-module --output-dir /tmp/odin-codegen-single
```

Use an empty output directory for each experiment. The script retains compiler
stderr/stdout, LLVM IR and a JSON summary; it never invokes Thor or modifies the
compiler. `--thread-count 1` provides the other control. Use `--odin-root` when a
temporary compiler should use the installed standard library.

For reliable race detection, build a **temporary copy of the C++ Odin compiler**
with `-g -O1 -fsanitize=thread -fno-omit-frame-pointer`, linking with
`-fsanitize=thread`, using the checkout's normal LLVM build configuration. This
investigation used LLVM 22.1.7 and `build_odin.sh debug` with those extra flags;
that script also runs the demo afterward, whose sanitizer failure must be
distinguished from the C++ compiler build succeeding.

Then run the fixture with that binary:

```sh
TSAN_OPTIONS='halt_on_error=0:history_size=7' \
python3 tools/repro_odin_codegen/reproduce.py \
  --odin /path/to/odin-tsan --odin-root /path/to/Odin \
  --expect-name-cache-race yes --output-dir /tmp/odin-codegen-before
```

The original compiler has unrelated earlier sanitizer reports too. Letting TSan
continue is necessary to reach code generation. `--expect-name-cache-race` checks
only reports whose **first access stack** starts in `lb_get_entity_name`, and
requires the compilation to emit IR. It does not call a build globally clean just
because this one race family is absent. TSan can abort on exit after reporting
warnings; those intentional failures are distinct from the original SIGSEGV.

To test the patch, apply it only in a separate compiler checkout based on
`8169255b97b85d6a08691d99764f51fb01f92b5a`, rebuild with the same flags, and repeat
with `--expect-name-cache-race no`. The patch is
`type-name-cache-publication.patch`; it can be checked/applied using ordinary
`git apply --check` / `git apply` from that separate checkout.

The patch removes the type-name cache race in this reproducer. It does not fix
all sanitizer reports or all similar compiler caches, and it has not been
installed as Thor's backend.
