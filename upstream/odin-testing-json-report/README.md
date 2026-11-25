# Native test JSON-report build failure

The official Linux ARM64 November nightly at revision `9a69916` fails when a
test command enables `ODIN_TEST_JSON_REPORT`. The failure is in the shipped
standard library's test runner.

The minimal package contains one empty test. From this directory, run the
following command with the affected distribution.

```sh
odin test . -thread-count:1 -define:ODIN_TEST_THREADS=1 \
  -define:ODIN_TEST_FANCY=false \
  -define:ODIN_TEST_JSON_REPORT=/tmp/odin-test-results.json
```

`core/testing/runner.odin:1050` passes `&{pretty = true}` to
`json.marshal_to_writer`. The selected JSON API expects `Marshal_Options` by
value. The compiler reports the argument mismatch before running the test.

The same distribution passes the test when the JSON-report define is omitted.
The native compiler built from revision
`537fac56cfee40cd51967d509ef089aa0a71444b` also passes with JSON reporting enabled.
The Thor integration comparisons use that working pinned compiler.

- [Native failure output](native-failure.txt)
- [Successful native controls](native-controls.txt)
- [Minimal source](main.odin)

The reproducer was verified on Ubuntu 24.04 ARM64 on November 15, 2026.

The same SDK error was reproduced on native Windows x64 with revision `9a69916`
on November 23, 2026. Windows integration runs use the ordinary native test-state
log (`ODIN_TEST_LOG_STATE_CHANGES=true`) and compare complete per-case verdicts
without enabling the broken optional JSON writer. The installed SDK is unchanged.
