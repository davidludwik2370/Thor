This is a modified copy of Odin's tooling parser from
`odin-lang/Odin`, version `dev-2026-10:8169255b9`.

Thor additions are marked by `p.thor` and the `Edit` / `Selection` metadata.
They add selective imports, `#if` / `#else`, newline continuation before a
ternary colon, inferred array literals, membership in generic constraints, and
immutable runtime declarations (`:>` / `: Type >`), quoted/raw interpolation,
switch expressions, statement/expression `#switch`, contextual `set[T]` types and
Set symmetric difference. `Feature` records original field/arm/type spans; virtual
call nodes carry nested expressions through the native AST without rewriting
source. Both consumers adapt these nodes using the same metadata.
Immutable metadata remains
separate from the native mutable-storage AST, so it is never mistaken for a
compile-time constant. The scanner is the Thor-owned `../tokenizer` package;
AST and token types remain aliases of the installed Odin distribution.
`file_tags.odin` has its AST import redirected to `core:odin/ast`.
Parser files use the package name `thor_parser` so TLS can link this parser
alongside the native Odin formatter's parser. Selection metadata retains name
positions for TLS; the transpiler continues to consume the same AST spans.

`file_redeclarations` supplies a shared early check of original, unconditional
top-level value declarations in untagged files. It does not alter the parser's
syntax-error state or attempt type/branch resolution. The CLI runs it before
compile-time evaluation; TLS publishes its results immediately while editing.

The original license is reproduced in [LICENSE](LICENSE). Changes to this copy
do not modify the user's Odin installation.
