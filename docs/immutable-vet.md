# Immutable declaration advice

`thor vet --vet=immutable` proposes `:>` for initialized bindings whose
conversion passes the compiler in the current configuration. Grouped results,
typed declarations, value copies, aliases, active `when` branches and native
declaration attributes participate. Ordinary declaration writes remain distinct
from writes through a pointer or another reference header. The existing
mutability rules are unchanged; the checker decides which operations require a
mutable binding.

Unused bindings and explicit discard-only suppression keep their existing
precedence. Excluded files and inactive branches are not presented as validated
suggestions. A grouped declaration has one separator: all of its names must
support the conversion together.

## Warnings and automatic edits

Some declarations compile as immutable now, but changing them may affect an
escaping slice contract, another `#config`, another target or another generic
specialization. Global bindings also require review because external callers
can depend on their mutability. Vet still reports those declarations. It appends a **Manual
review** explanation and leaves them unchanged with `--apply` or `--ask`.
Their JSON diagnostics have a `manual_review` explanation and no `fix` payload;
TLS displays the warning without offering an unsafe quick fix.

For example, a local buffer returned by a procedure can currently be made
readonly when its callers only read it. Doing so can also change the procedure's
contract for future callers, so that choice requires manual review. Likewise,
an inactive compile-time branch may write a currently unchanged variable.

Fully verified corrections retain their normal fix payload and editor actions.
When a validated batch contains manual suggestions, vet separately checks the
automatic subset before publishing it. Cleanup edits are included exactly once.
Applying the safe subset preserves and remaps the remaining manual warnings;
running `--apply` again leaves those declarations alone.

Some slice aliases require related declarations to change together. Those
corrections remain part of the verified `--apply` batch. A source declaration's
standalone editor action is withheld while it could leave a writable alias of
readonly storage; its diagnostic has `batch_only: true` and explains the batch
operation. After the alias is readonly, the source's individual fix becomes
available. This is separate from manual review: batch-only corrections are
automatically applied by `--apply`.

## Cost and regression coverage

Candidate discovery scans writes and projections once per source. Type and
call-graph properties are shared within the analysis. Diagnostic snapshots are
hashed once per source file; manual warnings do not need a fix snapshot.
Verification operates on batches, with diagnostic-root rejection for invalid
readonly aliases rather than a separate compile for every declaration.
When several unconditional readonly origins fail at the same operation, its
diagnostic retains all their declaration notes so vet rejects them together.
Conditional origins retain their existing guards and are not treated as proven
failures by this optimization.

`tests/test_vet_immutable_oracle.py` independently compiles original and readonly
forms, compares vet findings with compiler acceptance, checks automatic/manual
edits, and runs programs before and after application. Its cases include grouped
factory results, output parameters, nested aliases, pointers, dynamic headers,
native attributes, compile-time branches, generics and escaping results. Runtime
verdicts remain enabled in unchecked builds. Large valid, manual and invalid-alias
batches exercise validation counts and shared analysis. `tests/test_vet_tls.py`
covers grouped quick fixes, manual warnings, alias dependencies and unsaved recovery.

[The audit benchmark](benchmarks/immutable-vet-2026-11-21.md) records wall times,
validation counts and the cost of the newly reported findings.
