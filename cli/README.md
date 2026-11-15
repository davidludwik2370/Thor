# Command catalog

`help.json` is shared by the Rust CLI and Python reference. It contains the
public command list, normal Thor option sections, compatible native flag
syntax/descriptions, and command scopes. Rust embeds it in the compiler, so
installed help does not depend on this file being present at runtime.

The native flag catalog was checked against Odin dev-2026-10:8169255b9. Native
help descriptions are adapted from Odin, distributed under the accompanying
[BSD license](LICENSE). Thor-specific normalization and differences are described
in [the CLI guide](../docs/cli.md).

When updating compatibility, inspect the selected compiler’s command and flag
registrations as well as its per-command help. Some accepted flags are not shown
by native help. Add descriptions and applicable scopes here, then test actual
commands using Thor features. Exhaustive help also queries the selected native
compiler for additional flags, without adding discovery work to normal builds.

Validation includes `tests.test_cli_compatibility`, `tests.test_rust_cli_controls`,
and `tests.test_cli_default_source`. The native option catalog is documentation
and command-scope validation; the native compiler remains responsible for flag
values and platform support.
