# Thor tooling lexer

Adapted from Odin `dev-2026-10:8169255b9`; original license in [LICENSE](LICENSE).
Both Thor's compiler frontend and TLS scan through this package. Token kinds,
positions and token records alias `core:odin/tokenizer` to preserve native AST
compatibility; scanning, keyword lookup and token helpers are owned here.
Contextual syntax such as `:>` is recognized by the shared parser rather than
reserving ordinary identifiers like `fn` or `set`.

With `Tokenizer.thor` enabled, f-strings are single string tokens whose fields
are recursively scanned using this same lexer. Nested quotes, expressions and
interpolations retain original positions. Operators retain native Odin tokens:
`~`/`~=` are xor, and `p^=value` is pointer dereference followed by assignment.
With Thor disabled the scanner preserves ordinary Odin tokenization.

`tokenizer_test.odin` compares token text/kinds and byte/line/column positions
against native Odin for comments, CRLF, Unicode, strings, generics and Thor's
token combinations. Run `odin test frontend/tokenizer`.
