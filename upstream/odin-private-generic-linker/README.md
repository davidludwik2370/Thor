# File-private generic symbols collide across native modules

Native Odin revision `537fac56cfee40cd51967d509ef089aa0a71444b` produces duplicate
symbols on macOS arm64 when its internal test package contains this modified
`test_modmod.odin`. Adding local generic indexing helpers changes code-generation
partitioning and exposes duplicate `not_const` specializations from the unchanged
`test_mem_copy_small.odin`. A single-module control builds successfully.

The reduction retained the SDK internal package and only one changed file.
This is a package-level reproducer. Odin's license is included in `ODIN_LICENSE`.
Original source-location string literals are preserved because changing them
can change native partitioning and mask the failure. They are diagnostic data
and require no files at those locations. The small `support/sequence` package contains the indexing helper implementation
used by the supplied native source. Original linker output is in
`original-linker.txt`.

```sh
python3 reproduce.py --odin /path/to/odin \
  --sdk-internal /path/to/Odin/tests/internal
```

Use a clean SDK checkout at the revision above. The script copies the package
into a temporary directory, replaces one file, builds the default and
single-module configurations, and prints both commands and results as JSON.
It exits successfully when the duplicate-symbol failure and successful control
are reproduced. Both configurations only compile their test executables.

The expected outcome is successful linking in both configurations. The observed
duplicate symbols are `test_internal::not_const` for `i8`, `i32`, and `i64`,
originating in separate object partitions for `test_mem_copy_small`.

A fresh temporary-directory bundle run reproduced the default-linker failure
and successful single-module control on 2026-11-15. Its JSON command and result
record is retained in `validation.jsonl`. Object partition names differed from
the original capture, while the three duplicate specializations were identical.
