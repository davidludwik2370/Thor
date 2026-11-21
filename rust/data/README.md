# Reviewed allocation-free I/O snapshot

`noalloc-io-sdk.json` pins the source assumptions used by `noallocio.rs` and the
independent Python reference in `thorlang/noallocio.py`. It describes Odin SDK
revision `8169255b97b85d6a08691d99764f51fb01f92b5a` and Thor's basic/format bridge.

This is not an allowlist of allocation-free procedures. The checker only uses
summaries for individually reviewed operations and proven stream targets/modes.
All other calls still undergo the ordinary effect analysis. A familiar name in
a collection override does not inherit a summary.

Before changing fingerprints, inspect every changed execution and explicit
failure path covered by a summary. In particular, review native standard-file
backends, buffer initialization/read/write/flush, `fmt`'s temporary storage,
its width/precision/argument-count checks, reflective formatting, formatter
registration, and `basic.print`'s owned-number conversion path. Add regression
coverage for changed effects before updating the snapshot. Do not automatically
refresh hashes to make a rejected SDK pass.

The full non-test `base`/`core` source tree is hashed without loading its syntax,
including added files that could introduce initialization hooks. Actual loaded
source overlays participate too. Hashes are checked once per I/O proof; projects
without allocation contracts do not read this data. The program graph separately
rejects mutable user-formatter registries and replacement or escape of native
standard-file callback metadata.

Reviewed native boundaries currently cover macOS and Linux. Windows remains
unreviewed and receives a proof error. This pass changes no emitted I/O code,
procedure signature, allocator, or application runtime policy. See
[the contract documentation](../../docs/no-alloc.md#input-and-output).
