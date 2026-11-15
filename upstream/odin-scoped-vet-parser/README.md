# Intermittent scoped-vet parsing crash

On macOS arm64, Odin revision
`8169255b97b85d6a08691d99764f51fb01f92b5a` intermittently faults in
`ast_file_vet_flags(AstFile*)` while parsing imports for this three-line input.

```sh
odin check . -vet-when-shadowing -vet-packages:main -json-errors
odin check . -vet-when-shadowing -vet-packages:main -json-errors -thread-count:1
```

The default-worker command failed with SIGSEGV in 2 of 40 recorded runs.
All 40 single-worker controls passed. A generated native input failed in 3 of
40 default-worker runs and passed all 40 single-worker controls. The expected
outcome for this valid input is successful checking on every run.

The underlying concurrency cause remains unconfirmed. These results and crash
frame observations were recorded in the Thor native-vet audit. This folder
preserves a standalone reproducer for a future upstream report.

To capture an intermittent fault under LLDB on macOS, run the following command
repeatedly as needed. Its crash handler records the stack and kills the stopped
compiler before exiting.

```sh
lldb --batch -o run -k 'thread backtrace -c 20' -k 'process kill' -- \
  /path/to/odin check . -vet-when-shadowing -vet-packages:main -json-errors
```
