This is a modified copy of Odin's tooling parser from
`odin-lang/Odin`, version `dev-2026-10:8169255b9`.

Thor additions are marked by `p.thor` and the `Edit` / `Selection` metadata.
They add selective imports, `#if` / `#else`, newline continuation before a
ternary colon, inferred array literals, and membership in generic constraints.
The AST and tokenizer continue to come from the installed Odin distribution.
`file_tags.odin` has its AST import redirected to `core:odin/ast`.
Both files use the package name `thor_parser` so TLS can link this parser
alongside the native Odin formatter's parser. Selection metadata retains name
positions for TLS; the transpiler continues to consume the same AST spans.

The original license is reproduced in [LICENSE](LICENSE). Changes to this copy
do not modify the user's Odin installation.
