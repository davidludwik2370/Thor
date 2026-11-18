# Mixed-type multi-result `or_else` fallback loses its values

The native Odin reproducer in [main.odin](main.odin) requires no Thor source or
runtime. On Odin `dev-2026-10:8169255b9` on macOS ARM64, the operation returns a
false status, but the direct procedure-call fallback does not supply its results:

```text
direct: 0 ""
packed: 7 "ok"
```

Both lines should contain `7 "ok"`. The final explicit panic makes the failure
observable with assertions disabled. Reproduction commands:

```sh
odin run .
odin run . -o:speed -disable-assert -no-bounds-check -no-type-assert
```

Packing and immediately expanding the fallback results works:

```odin
a, b := read() or_else expand_values(compress_values(fallback()))
```

Thor applies that workaround to known multi-result fallback calls. Native
optional-result calls retain their ordinary single-value behavior. New explicit
fallback lists use a typed aggregate followed by `expand_values`, which also
avoids this issue. Neither approach adds a heap allocation or changes the
fallback's lazy evaluation. No Odin compiler patch is included, and no issue
has been submitted. Verified November 17, 2026.
