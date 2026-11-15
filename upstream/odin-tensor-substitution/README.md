# Generic Tensor substitution traps after source errors

Native Odin revision `537fac56cfee40cd51967d509ef089aa0a71444b`
(`dev-2026-11:537fac5`) traps in `subst_apply` on macOS arm64.
The 86-line source was reduced from Anvil's convolution test and implementation.
Anvil's MIT license is included in `ANVIL_LICENSE`.

```sh
odin build . -build-mode:test -thread-count:1 -out:/tmp/tensor-repro
```

The source intentionally contains unresolved declarations left by reduction.
The expected outcome is ordinary source diagnostics and an unsuccessful exit.
After emitting diagnostics, Odin instead reaches the following assertion.

```text
src/check_type.cpp(2625): Assertion Failure: `found != nullptr`
subst_apply: no instantiation found for 'Tensor($T)'
```

The captured single-worker backtrace is in `lldb.txt`. It passes through
`determine_type_from_polymorphic`, `check_procedure_type`, and overload checking.
For a debugger-controlled reproduction on macOS, run the command below from
this directory. LLDB captures the trap and terminates the stopped compiler.

```sh
lldb --batch -o run -k 'thread backtrace -c 12' -k 'process kill' -- \
  /path/to/odin build . -build-mode:test -thread-count:1 -out:/tmp/tensor-repro
```

Only native Odin and its standard library are required. The reproducer was
reduced from a failed proposed vet correction. Those source errors are valid
rejections, while the subsequent compiler trap is the upstream issue.
