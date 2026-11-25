# Attributes, directives, and file tags

This is the complete name reference for the Thor toolchain as of **2026-11-20**,
including inherited Odin syntax. The native inventory was checked against Odin
`dev-2026-10`, revision
[`8169255b97b85d6a08691d99764f51fb01f92b5a`](https://github.com/odin-lang/Odin/tree/8169255b97b85d6a08691d99764f51fb01f92b5a).
Thor-specific behavior is checked against this repository's parser, lowering,
validation, and regression tests. Target-specific features still require native
compiler, linker, operating-system, and architecture support.

“Complete” here covers every built-in attribute name, directive, file tag, and
file-tag option recognized by that implementation, **including recognized but
rejected or obsolete forms**. It does not mean that every form is valid on every
declaration. Arbitrary user-defined attribute names have no finite inventory;
see [custom attributes](#custom-attributes). Later Odin revisions can add names
or change restrictions. The [inventory audit](#maintaining-this-reference) makes
such differences visible.

## Contents

- [Reading the syntax](#reading-the-syntax)
- [Declaration attributes](#declaration-attributes)
  - [Visibility, metadata, and unused declarations](#visibility-metadata-and-unused-declarations)
  - [Storage and linking](#storage-and-linking)
  - [Procedure behavior and lifecycle](#procedure-behavior-and-lifecycle)
  - [Deferred cleanup](#deferred-cleanup)
  - [Optimization, instrumentation, and sanitizers](#optimization-instrumentation-and-sanitizers)
  - [Synchronization analysis](#synchronization-analysis)
  - [Objective-C interoperability](#objective-c-interoperability)
- [Thor directives](#thor-directives)
- [Type and layout directives](#type-and-layout-directives)
- [Parameter and result directives](#parameter-and-result-directives)
- [Control flow and checking directives](#control-flow-and-checking-directives)
- [Compile-time resources and source information](#compile-time-resources-and-source-information)
- [Assembly directives](#assembly-directives)
- [File tags and their options](#file-tags-and-their-options)
- [Obsolete, removed, and reserved forms](#obsolete-removed-and-reserved-forms)
- [Related syntax that is not an attribute or directive](#related-syntax-that-is-not-an-attribute-or-directive)
- [Sources and maintaining the inventory](#sources-and-maintaining-the-inventory)

## Reading the syntax

An **attribute** annotates a declaration. Its spelling is `@(name)` or
`@(name=value)`, with multiple entries allowed inside the parentheses. Attribute
arguments must satisfy that attribute's compile-time value/type requirements.
Separate attribute groups may precede the same declaration; duplicate entries
are generally errors. Parentheses are part of the attribute syntax:

```odin
@(private="file")
Helper :: struct { value: int }

@(deprecated="Use replacement instead")
old_helper :: proc() {}
```

A **directive** begins with `#` and belongs to an expression, statement, type,
parameter, or procedure signature. Placement is part of its meaning; it is not
a general-purpose annotation. Some directives resemble calls, such as
`#config(MODE, 1)`, but are compiler operations rather than procedures.

A **file tag** begins with `#+` and goes in the file header, before `package`.
It affects file selection or compilation policy rather than one declaration:

```odin
#+build darwin, linux amd64
package example
```

Most entries below are inherited Odin features. **Thor** labels identify additions
or deliberate differences. Ordinary attributes do not bypass Thor's pure-function,
read-only slice, ownership, or compile-time evaluation rules. In particular, a
native optimizer hint is not a proof of purity.

## Declaration attributes

### Visibility, metadata, and unused declarations

<a id="attr-private"></a>
#### `@(private)`, `@(private="package")`, `@(private="file")`

Restricts declaration visibility. Bare `private` and `"package"` make a declaration
private to its package; `"file"` additionally hides it from other files in that
package. This is source-level visibility, distinct from exporting a linker symbol.
It is used on supported file-scope declarations, including types and imports of
foreign declarations through annotated foreign blocks. A file-wide default is
available through [`#+private`](#file-private). It is not a C++-style access
modifier for arbitrary struct members.

<a id="attr-tag"></a>
#### `@(tag="text")`

Attaches a constant string of user metadata to a declaration. The native attribute
handlers accept it on procedures, overload groups, variables, constants, types,
imports, foreign imports, foreign blocks, and assembly declarations. The compiler
does not interpret the string as executable behavior, ownership, or a diagnostic
suppression rule. External tools may assign conventions to it.

<a id="attr-unused"></a>
#### `@(unused)` — Thor

Marks a variable, constant, type, `proc`, or `fn` declaration as intentionally
unused and suppresses its unused-declaration warning. **Referencing the tagged
binding is a compile error.** Reads, writes, address-taking, aliases, type queries,
interpolations, compile-time references, and `_ = binding` all count as uses.
A shadowing declaration is a different binding and is unaffected.

The attribute takes no argument. On a grouped declaration it applies to every
named binding; blank-only declarations and duplicate annotations are rejected.
It is not a parameter annotation or an unused-import annotation. It does not
suppress warnings for unrelated parameters or locals inside a tagged procedure,
and it does not prevent other applicable vet recommendations. `vet --apply`
preserves the attribute and may still make other safe corrections.

An initializer still runs normally and allocated resources still need their
normal cleanup. Automatically invoked/retained declarations, including `main`
and active `init`, `fini`, `test`, `export`, or `require` declarations, cannot be
marked unused. Inactive build files and proven inactive `when` branches are
excluded; potentially active procedure bodies are still checked. TLS reports the
use and relates it to the tagged declaration.

```odin
@(unused)
future_limit :: 100
// print(future_limit) // Error: this declaration was promised to be unused.
```

<a id="attr-vet_ignore"></a>
#### `@(vet_ignore={.checks})` — Thor

Suppresses selected Thor vet recommendations for the annotated declaration or
import, including their `vet --apply` edits. Supply a nonempty set of contextual
rule names: `@(vet_ignore={.immutable, .unused})`. Rules are `.immutable`, `.pure`
(alias `.fn`), `.unused`, `.semicolon`, `.lifetime_unknown`, and `.leak_unknown`.
Use braces for one rule too, e.g. `@(vet_ignore={.unused})`. Members are metadata
labels, not references to ordinary variables; no global enum/type or runtime
collection is created. Multiline sets and trailing commas follow ordinary
compound-literal syntax. Repeated members collapse to one rule.

The additional `.lifetime_unknown` and `.leak_unknown` names acknowledge
unverified lifetime/cleanup effects rooted in this declaration, including uses
through aliases. Definite lifetime and leak findings cannot be suppressed;
`lifetime` and `leak` are not valid suppression names. See [Lifetime vet](lifetime-vet.md).
Unknown/empty names and duplicate attributes are errors. For example,
`@(vet_ignore={.fn, .unused})` can retain an intentionally public `proc` without
converting or removing it. Normal source visibility and linker retention rules
still apply. TLS offers the supported rules after a dot inside the set, filters
as you type, and omits rules already selected. Existing constant-string forms
such as `@(vet_ignore="unused, pure")` remain accepted for compatibility.

This does not suppress compiler errors, purity/access violations, native Odin
vet policies, or findings on other declarations. A procedure annotation does not
silence unrelated local declarations inside its body. Unlike `@(unused)`, it does
not prohibit later references to the declaration. TLS diagnostics and quick fixes
use the same suppression rules as the CLI.

<a id="attr-no_alloc"></a>
#### `@(no_alloc)` — Thor

Checks that a procedure or `fn` and its reachable runtime calls are provably
allocation free. Takes no arguments and requires an inspectable definition;
foreign declarations and callback types cannot acquire an unchecked promise.
Potential allocation, unknown effects, allocating generated Thor helpers, and
owned-result promotion are compile errors. Aliases, selected overloads, concrete
specializations, omitted defaults, recursion, and compile-time materialization
are included in the check. This does not imply purity and does not change emitted
runtime code. Fatal diagnostic reporting may allocate; eagerly evaluated check
arguments and ordinary nonreturning callees remain checked. See the
[complete conservative contract](no-alloc.md), including
generic limitations and the file-wide `#+vet no_alloc` form. Reviewed native I/O
can use fixed buffers and proven writers; the [I/O contract](no-alloc.md#input-and-output)
documents SDK verification and opaque-callback restrictions.
The [RNG contract](no-alloc.md#random-number-generators) also covers the native
default generator, seeded state, borrowed-buffer operations, and verified custom
callbacks. Unknown replacements of the ambient generator remain errors.

<a id="attr-builtin"></a>
#### `@(builtin)` — runtime implementation only

Marks declarations that the compiler's runtime package supplies as builtins.
This is recognized native syntax but is restricted to the runtime package;
ordinary application/library declarations cannot use it to acquire builtin
behavior. Aliasing an existing builtin is a separate language operation.

<a id="attr-raddbg_type_view"></a>
#### `@(raddbg_type_view)` or `@(raddbg_type_view="expression")`

Provides a RAD Debugger display view for a named type. The string form supplies
its debugger expression; the bare form requests the compiler's field-tag-based
view, using applicable field `fmt` tags. A supplied string must be nonempty.
This changes debugger metadata, not object layout, formatting through `print`,
or the type's runtime value. RAD-specific views are not automatically a display
extension for every debugger, including CodeLLDB.

<a id="custom-attributes"></a>
#### Custom attribute names

Odin's `-custom-attribute:NAME` and `-ignore-unknown-attributes` can allow otherwise
unknown attribute names to pass validation where supported by the command.
That does not implement a new language feature: the native compiler supplies no
semantics for the accepted custom name. For example, accepting `borrows` this way
would not add borrow checking or automatic cleanup. Recognized attributes with
invalid arguments or placement remain subject to their built-in checks.

### Storage and linking

<a id="attr-align"></a>
#### `@(align=N)`

Requests alignment for a **variable's storage**. `N` must be a positive constant
power of two within native/target alignment limits; the checker bounds the
request to at most `2^31`. It can increase padding and affect ABI-visible storage.
Use [`#align`](#dir-align) on a record type when the type itself needs a layout
alignment; aligning one variable does not change its type's layout.

<a id="attr-static"></a>
#### `@(static)`

Gives a procedure-local variable persistent storage instead of a fresh automatic
instance for each call. It takes no argument. The value is shared across calls
and, without other arrangements, across threads. This is not an implicit lock,
lazy initializer, or per-thread value. File-scope variables already have persistent
storage. It is invalid on a compile-time constant value declaration.

Storage duration and mutability are independent. `@static x :> 10` remains
immutable: assigning to `x`, compound assignment, and taking `&x` are errors.
Use `@static x := 10` when persistent mutable state is intended. The same
permission rules apply with any preceding attributes, including `vet_ignore`.
Generic instantiations have distinct static storage, matching native Odin.

A `fn` or `#run` call chain cannot read or write mutable static state. Immutable
static values and direct reads from constant `@(static, rodata)` tables remain
eligible. Read-only tables do not grant mutable references in pure code.

<a id="attr-rodata"></a>
#### `@(rodata)`

Places a variable in read-only data and requires a constant initializer. It takes
no argument. A persistent procedure-local read-only table normally uses
`@(static, rodata)`. Storage cannot be made writable by copying its pointer or
slice header; attempts to write native protected storage are invalid and may
fault if safety is bypassed. The attribute does not recursively allocate, clone,
or make every external object reachable through a pointer read-only.

Thor can generate these attributes for proven constant immutable slice literals;
see [constant literal storage](slice-access.md#constant-literal-storage). General
`:>` declarations do not necessarily live in read-only memory. Static literal
backing is borrowed storage and must not be deleted.

<a id="attr-thread_local"></a>
#### `@(thread_local)` or `@(thread_local="MODEL")`

Gives a variable independent storage per thread. The bare form selects `default`.
Accepted models are `default`, `globaldynamic`, `localdynamic`, `initialexec`, and
`localexec`; these are TLS access/linkage models, not scheduling policies. Model
availability and suitability depend on the target and whether code is loaded
as an executable or shared library. Native TLS variables do not accept an
initializer value, cannot be exported, and cannot be foreign-block variables.
A local TLS variable also has persistent storage semantics. This attribute does
not automatically arrange ownership cleanup when a thread exits.

<a id="attr-export"></a>
#### `@(export)` or `@(export=condition)`

On a procedure or global variable, exports its linker symbol when the optional
constant Boolean is true; the default is true. This makes it externally visible
at the binary boundary and keeps the needed definition. ABI compatibility still
requires the right procedure calling convention, layout, and symbol name. It is
separate from source visibility. Native TLS variables cannot be exported.

On a **foreign import**, bare `export` exposes the imported library name to the
package rather than declaring a new exported procedure body. This placement has
a different purpose from procedure export.

<a id="attr-require"></a>
#### `@(require)` or, on a procedure, `@(require=condition)`

Retains an otherwise unreferenced declaration or dependency. A procedure accepts
an optional constant Boolean, default true. On a global variable, regular import,
or foreign import, the attribute takes no value. A required regular import keeps
the package dependency; a required foreign import forces the native library to
participate even without an ordinary referenced symbol. It is useful for
registration and library side effects, not merely for silencing unused warnings.
It is invalid on a compile-time constant value declaration.

<a id="attr-link_name"></a>
#### `@(link_name="symbol")`

Sets the external symbol name of a procedure or variable instead of its generated
name. It is a constant string, not an expression evaluated when the procedure is
called. This is commonly used for foreign declarations and exported entry points.
An explicit link name overrides inherited prefix/suffix naming. It does not
change ABI, argument layout, or calling convention.

<a id="attr-link_prefix"></a>
#### `@(link_prefix="prefix")`

Adds a prefix to a procedure or variable's linked name. On a foreign block it
supplies the default prefix for declarations in that block. It changes symbol
spelling, not the source identifier. An explicit `link_name` takes precedence;
this is not a namespace import or source alias.

<a id="attr-link_suffix"></a>
#### `@(link_suffix="suffix")`

The suffix counterpart of `link_prefix`, with the same procedure, variable, and
foreign-block uses and the same explicit-`link_name` precedence. Prefixes and
suffixes must match the symbols actually supplied by the native library.

<a id="attr-link_section"></a>
#### `@(link_section="section")`

Requests an object-file section for procedure code or global variable storage.
The string is platform/object-format specific; for example, Mach-O section
spelling differs from ELF and COFF. It does not create a portable memory
allocator or override the operating system's memory protections. Section
placement and retention can also depend on the linker.

<a id="attr-linkage"></a>
#### `@(linkage="kind")`

Sets native linkage for a procedure or global variable. Accepted kinds are
`internal`, `strong`, `weak`, and `link_once`. They control symbol visibility and
how duplicate definitions are resolved/coalesced by the backend/linker. `internal`
is object/module-local; `strong` requires an ordinary definition; `weak` and
`link_once` permit the corresponding native merging/selection behavior. They do
not implement procedure overloads and are invalid on constant value declarations.

<a id="attr-default_calling_convention"></a>
#### `@(default_calling_convention="convention")`

Sets the default convention for procedures declared inside a foreign block.
Ordinary procedures can spell their convention directly after `proc` instead.
Recognized convention strings are `odin`, `contextless`, `cdecl`/`c`,
`stdcall`/`std`, `fastcall`/`fast`, `none`, `naked`, `win64`, `sysv`,
`preserve/none`, `preserve/most`, `preserve/all`, and `system`. Acceptance for an
actual signature still depends on target and backend support. `system` selects
the platform convention (stdcall on Windows, C on other targets in this revision).
`naked` is a calling-convention string, **not an `@(naked)` attribute**.

<a id="attr-extra_linker_flags"></a>
#### `@(extra_linker_flags="flags")`

Adds native linker arguments associated with a foreign import. The value is a
constant string interpreted by the native toolchain; syntax and available flags
are platform dependent. It is not a Thor option string, runtime argument list,
or portable way to locate packaged application resources.

<a id="attr-priority_index"></a>
#### `@(priority_index=N)`

Assigns an integer ordering priority to a foreign import. Native library ordering
sorts lower indices before higher ones, then uses the usual package/declaration
ordering. The default index is zero. This can matter to linkers with order-sensitive
library resolution; it is not runtime initialization priority.

<a id="attr-ignore_duplicates"></a>
#### `@(ignore_duplicates)`

Allows the native foreign-import handling to tolerate duplicate library entries
for that import. It takes no value. It does not legalize duplicate procedure
bodies or conflicting ABI declarations, and it does not promise that a linker
will accept arbitrary repeated definitions from different libraries.

### Procedure behavior and lifecycle

These attributes apply to `proc` declarations; Thor's `fn` lowering also preserves
applicable procedure attributes, subject to its additional purity rules.
Attributes do not make an otherwise impure body a valid `fn`.

<a id="attr-init"></a>
#### `@(init)`

Registers a file-scope initialization procedure to run before the program entry
point. It takes no attribute argument; the procedure takes no parameters and
returns no values. Native lifecycle calling-convention/context restrictions apply
(the `global-context` feature changes applicable context rules). Package dependency
ordering is respected. Do not use declaration order across unrelated packages as
a substitute for an explicit dependency. `-disable-init-fini` rejects these hooks.

<a id="attr-fini"></a>
#### `@(fini)`

Registers a file-scope shutdown procedure with no parameters/results, under the
native lifecycle calling-convention rules. Normal shutdown invokes registered
finalization; this is not a guarantee that cleanup runs after abort, a fatal
signal, or process termination. It takes no attribute argument and is rejected
by `-disable-init-fini`. Use explicit ownership/defer for local resources rather
than relying on program shutdown for every allocation.

<a id="attr-test"></a>
#### `@(test)`

Registers a procedure as a test for `thor test` and native test build mode. Its
signature is the native testing entry shape, ordinarily
`proc(t: ^testing.T)` with no result. It takes no attribute argument. The testing
package supplies the test state and runner; the attribute is not a runtime
assertion. [`#+test`](#file-test) instead selects whole files only for tests.
A test's unused `t` can be named `_` when it is unnecessary.

<a id="attr-require_results"></a>
#### `@(require_results)`

Requires callers to acknowledge the procedure's results instead of silently
calling it as a statement. Assign/use results or explicitly discard them with
`_` as appropriate. The attribute takes no value. It is also accepted on
procedure overload groups and as a default on a foreign block. It does not
validate an error value or force a panic: explicit discarding remains an
acknowledgment, not error handling.

<a id="attr-disabled"></a>
#### `@(disabled=condition)`

Conditionally disables calls to a procedure using a required constant Boolean.
This is a compile-time code-generation facility, commonly used for optional
logging/debug hooks; it is not an `if` tested at runtime. Call syntax and signature
still have to be valid. Do not place required behavior in calls intended to be
disabled, including their argument expressions; use ordinary control flow when
the evaluation of those expressions is part of the program's contract.

<a id="attr-deprecated"></a>
#### `@(deprecated="message")`

Marks a procedure or named type as deprecated and supplies a required nonempty
constant diagnostic message. Use sites can produce native deprecation diagnostics
under the selected vet/warning policy. The declaration remains callable/usable;
the compiler does not rewrite it to a replacement. It is not a general annotation
for every variable or compile-time value.

<a id="attr-entry_point_only"></a>
#### `@(entry_point_only)`

Restricts a procedure to direct use from the user-level entry-point procedure.
The checker rejects references/calls from other procedures, including a helper
called by `main`. It takes no value. It does not register a new `main`, export a
platform startup symbol, or arrange execution automatically.

### Deferred cleanup

A deferred-procedure attribute attaches a cleanup procedure to another procedure.
Calling the decorated procedure registers the cleanup at the end of the
**caller's enclosing scope**, using the selected inputs/results. This is how a
library can offer scoped construction. It is not necessarily cleanup at the end
of the constructor itself. At most one of these attributes may decorate a
procedure, and the cleanup procedure's parameter types/arity must match the
chosen mode. A procedure cannot defer itself, and a cleanup target cannot itself
have a deferred-procedure attribute: chaining is rejected. A polymorphic cleanup
requires an appropriately polymorphic constructor, and `deferred_none` cannot
infer a polymorphic cleanup's types without inputs. A disabled cleanup target
disables the associated deferral. Allocator/context state, ownership, and the
lifetime of arguments still have to be valid for the delayed call.

<a id="attr-deferred_none"></a>
#### `@(deferred_none=cleanup)`

Calls `cleanup()` when the caller's scope exits. It forwards neither constructor
arguments nor results. Use it only when the cleanup needs no captured resource
value or obtains its state through an explicitly valid mechanism.

<a id="attr-deferred_in"></a>
#### `@(deferred_in=cleanup)`

Forwards the decorated call's input values to cleanup. The cleanup signature must
match those input types. Capturing a pointer/slice value does not deep-copy its
pointee/backing storage.

<a id="attr-deferred_out"></a>
#### `@(deferred_out=cleanup)`

Forwards the decorated call's results to cleanup. This is useful for a constructor
whose returned resource can supply everything its destructor needs. It does not
transfer ownership to an arbitrary unrelated caller alias.

<a id="attr-deferred_in_out"></a>
#### `@(deferred_in_out=cleanup)`

Forwards input values followed by output values. The cleanup signature must
accept the combined lists in that order. Use the more specific `_in` or `_out`
mode when one list is unnecessary.

<a id="attr-deferred_in_by_ptr"></a>
#### `@(deferred_in_by_ptr=cleanup)`

The input counterpart of `deferred_in`, passing addresses of the associated input
values to cleanup. Those are pointers to the deferred machinery's corresponding
values, not a promise of an arbitrary caller variable's address. The cleanup's
parameter types must match the pointer form.

<a id="attr-deferred_out_by_ptr"></a>
#### `@(deferred_out_by_ptr=cleanup)`

Passes addresses of result values to cleanup. This allows destructors that take
a pointer to a returned resource descriptor. It does not make escaping pointers
to that descriptor safe beyond its scope.

<a id="attr-deferred_in_out_by_ptr"></a>
#### `@(deferred_in_out_by_ptr=cleanup)`

Combines the two lists, passing input addresses followed by result addresses.
The signature check applies to every forwarded value. See the actual scoped
arena implementation in [the basic library](../runtime/basic/basic.thor) for
Thor's library-based scope cleanup; no special arena syntax is required.

### Optimization, instrumentation, and sanitizers

<a id="attr-cold"></a>
#### `@(cold)` or `@(cold=condition)`

Marks a procedure as unlikely to run when the optional constant Boolean is true
(default true). This guides optimizer/code-layout decisions. It does not change
control flow, add a branch prediction check, or prove that the procedure cannot
return. Measure code before relying on the hint for performance.

<a id="attr-optimization_mode"></a>
#### `@(optimization_mode="mode")`

Overrides native optimization policy for one procedure. In the pinned compiler,
the accepted strings are **`none`** and **`favor_size`**. `none` disables that
procedure's normal optimization and conflicts with force-inlining; `favor_size`
requests size-oriented optimization. The historical strings `minimal`, `size`,
and `speed` are rejected here. These strings are not the CLI's `-o:MODE` values.

<a id="attr-fast_math"></a>
#### `@(fast_math=flags)`

Sets a constant bit set of type `intrinsics.Fast_Math_Flags` for a procedure.
Its flag enum contains `Allow_Reassoc`, `No_NaNs`, `No_Infs`, `No_Signed_Zeros`,
`Allow_Reciprocal`, `Allow_Contract`, and `Approx_Func`. These permit reordering,
exceptional-value assumptions, signed-zero simplification, reciprocal transforms,
operation contraction, and approximate functions respectively. An empty set
requests none of these assumptions. It is **not a Boolean speed switch**: enabled
flags can change floating-point results and invalidate algorithms that depend on
IEEE behavior. Target/backend support still governs the transformations.

<a id="attr-enable_target_feature"></a>
#### `@(enable_target_feature="features")`

Enables specified native target features for a procedure or assembly declaration.
The constant string follows the native target-feature syntax (including
comma-separated feature names and supported `+`/`-` adjustments). Names depend
on architecture/backend. This can produce instructions unavailable on some CPUs;
it does not insert a runtime CPU check or dispatch to a fallback implementation.

<a id="attr-require_target_feature"></a>
#### `@(require_target_feature="features")`

Declares the target-feature requirements of a procedure or assembly declaration
and lets the native checker validate callers against them. It does not itself
perform runtime feature detection. Coordinate it with the function's actual
enabled features and any explicit runtime dispatch.

<a id="attr-no_instrumentation"></a>
#### `@(no_instrumentation)` or `@(no_instrumentation=condition)`

Disables Odin procedure instrumentation for that procedure by default. A constant
false value explicitly enables it, overriding a file's `#+no-instrumentation`
default. It concerns profiler entry/exit instrumentation, not allocation tracking,
ASan, bounds checks, or compile-time purity. It is not accepted on foreign
procedure prototypes.

<a id="attr-instrumentation_enter"></a>
#### `@(instrumentation_enter)`

Registers the native procedure-entry instrumentation hook. There can be only one
such hook; it is file-scope, takes no attribute argument, and must have the
contextless signature
`proc "contextless"(proc_address: rawptr, call_site_return_address: rawptr, loc: runtime.Source_Code_Location)`
with no results. The compiler avoids recursively instrumenting the hook itself.
The corresponding instrumentation build option must be enabled for ordinary
procedures to call it. The addresses are machine/code addresses, not owned data.

<a id="attr-instrumentation_exit"></a>
#### `@(instrumentation_exit)`

The matching procedure-exit hook, with the same signature, uniqueness, and
nonrecursive instrumentation rules as `instrumentation_enter`. It observes
instrumented normal procedure exits; it is not a guaranteed resource-finalization
hook after a crash or abnormal termination.

<a id="attr-no_sanitize_address"></a>
#### `@(no_sanitize_address)`

Suppresses native AddressSanitizer instrumentation in the annotated procedure.
It takes no value and matters only when that sanitizer is enabled/supported.
It does not disable instrumentation in separately compiled callees, erase bugs,
or disable Thor's tracking allocator. Avoid placing it on code merely to hide
a real memory error.

<a id="attr-no_sanitize_memory"></a>
#### `@(no_sanitize_memory)`

The corresponding MemorySanitizer opt-out for one procedure. It takes no value;
availability is target/toolchain dependent. It does not initialize memory or
change the program's ownership obligations.

<a id="attr-no_sanitize_thread"></a>
#### `@(no_sanitize_thread)`

The corresponding ThreadSanitizer opt-out for one procedure. It takes no value.
It does not synchronize accesses, make a data race safe, or disable Thor's static
read-only slice checks.

### Synchronization analysis

These attributes describe synchronization to native atomic-access analysis.
**They do not generate a lock, fence, atomic instruction, futex, or memory-order
operation.** Apply them only to procedures whose implementations actually provide
the declared behavior. Native flow-analysis policies inspect a source-preserving
checking view; see [native-check compatibility](#native-check-compatibility).

<a id="attr-futex"></a>
#### `@(futex=operation)`

Describes a futex-like wait/wake procedure using a constant
`intrinsics.Futex_Operation`: `.Wait` or `.Wake`. The analysis treats waiting as a
relaxed read/comparison of the futex word and waking as no access to its stored
value. This special modeling avoids inappropriate escape/local-atomic warnings;
it does not confer acquire/release ordering on an OS call.

<a id="attr-futex_parameter"></a>
#### `@(futex_parameter="parameter")`

Selects which parameter is the futex word when `@(futex=...)` is present. Without
it, the first parameter is used. The selected parameter must have the native
pointer-to-integer (or applicable polymorphic) shape. The name is a constant
string naming that procedure's parameter; the attribute has no standalone futex
semantics without `futex`.

<a id="attr-synchronizes"></a>
#### `@(synchronizes=order)`

Models a procedure as synchronization on the object identified by its first
argument. The value is `intrinsics.Atomic_Memory_Order`; native analysis rejects
`.Relaxed` for this attribute. The enum's names are `.Relaxed`, `.Consume`,
`.Acquire`, `.Release`, `.Acq_Rel`, and `.Seq_Cst`, with the applicable non-relaxed
order specifying the synchronization effect. This is suited to lock-like wrappers
whose implementation already provides that ordering. It cannot be combined with
`synchronizes_shared` on the same procedure.

<a id="attr-synchronizes_shared"></a>
#### `@(synchronizes_shared=order)`

The shared/read-side counterpart for readers of an RW lock. Shared acquisition
can protect plain reads; a shared release does not publish arbitrary plain
writes as an exclusive writer's release would. It uses the same constant enum
and excludes relaxed ordering. It cannot be combined with `synchronizes` and
does not turn a normal procedure into an RW-lock implementation.

### Objective-C interoperability

These attributes form a coordinated native Objective-C binding/implementation
facility. They require an appropriate platform/runtime and compatible procedure
signatures. They do not make arbitrary Thor records automatically Objective-C
objects. Imported methods and implemented methods have different constraints;
see the native [Objective-C checking source](https://github.com/odin-lang/Odin/blob/8169255b97b85d6a08691d99764f51fb01f92b5a/src/check_decl.cpp).

<a id="attr-objc_class"></a>
#### `@(objc_class="ClassName")`

Associates a named type with a nonempty Objective-C runtime class name. The
remaining class-layout/superclass/implementation annotations describe the
representation where needed. A class-name annotation is not an allocation or
initialization call.

<a id="attr-objc_superclass"></a>
#### `@(objc_superclass=SuperType)`

Specifies the superclass type for an Objective-C class declaration. The type
must satisfy the compiler's Objective-C class constraints, rather than merely
having fields similar to a class. It participates in class generation and method
inheritance checking, not Odin struct field promotion.

<a id="attr-objc_ivar"></a>
#### `@(objc_ivar=IvarType)`

Identifies the named type describing the instance-variable layout for the class.
The checker validates that layout in the Objective-C declaration context. It is
not a per-field readonly/writeable annotation and does not manage referenced
objects' lifetimes.

<a id="attr-objc_context_provider"></a>
#### `@(objc_context_provider=provider)`

Selects the procedure supplying an Odin `runtime.Context` for an implemented
Objective-C class. It requires `objc_implement` and a provider with the native
checked signature for that class. This is needed when implemented methods use
the Odin calling convention and therefore need context. The provider must supply
a valid context for the receiving object; no arbitrary global context is created
by the attribute.

<a id="attr-objc_implement"></a>
#### `@(objc_implement)` or `@(objc_implement=condition)`

Marks an Objective-C class type or method procedure as an implementation, using
an optional constant Boolean (default true). The native compiler then supplies
the applicable implementation/runtime metadata instead of treating it solely
as an imported binding. Required class, selector, receiver, and calling-convention
relationships are checked separately.

<a id="attr-objc_type"></a>
#### `@(objc_type=ClassType)`

Connects a method procedure or procedure group to the named Objective-C class
type on which it is declared. That type must carry the appropriate `objc_class`
information. It is not an argument cast or a way to attach general methods to
arbitrary Odin types.

<a id="attr-objc_name"></a>
#### `@(objc_name="name")`

Provides the Objective-C method name for a procedure or procedure group. The
nonempty constant string is used by the binding/implementation machinery rather
than changing the ordinary Thor source identifier. For implementations it can
also supply the selector name when a distinct `objc_selector` is not specified.

<a id="attr-objc_selector"></a>
#### `@(objc_selector="selector")`

Specifies the Objective-C selector for an imported or implemented method procedure.
Imported methods require the applicable selector annotation. The selector's
argument slots must agree with the method signature; this is not a general linker
symbol rename and is rejected on unrelated procedure declarations.

<a id="attr-objc_is_class_method"></a>
#### `@(objc_is_class_method=condition)`

A required constant Boolean on a method procedure or procedure group. True marks
a class method; false marks an instance method. It controls Objective-C dispatch
metadata, not procedure static storage or whether ordinary inputs are mutable.

## Thor directives

<a id="dir-run"></a>
### `#run call(...)` — explicit compile-time execution

Executes an eligible resolved call during compilation and substitutes its result.
The call can resolve to a Thor `fn`, an eligible `proc`, or eligible Odin/standard
library code. Eligibility is checked transitively: merely writing `#run` does not
permit arbitrary I/O, ambient context, nondeterministic global state, or unsafe
escaping mutation. Local scratch mutation can be valid when the purity rules
prove it confined. Diagnostics identify the originating call and the reason a
transitive callee is unsuitable.

Thor prepares a helper project, includes required dependencies, compiles/runs it,
and materializes the returned value. Results are cached using the relevant code
and inputs. There is no execution timeout by default; `--run-timeout SEC` supplies
one and `0` explicitly selects unlimited execution. If an explicit timeout is
hit, compilation fails; the compiler does not silently run that call at runtime.

```odin
square :: fn(x: int) -> int { return x*x }
LIMIT :: #run square(12)
runtime_value := #run square(7)
```

The receiving context still matters. A genuine `::` constant must be representable
as a compile-time value; a runtime declaration can receive materialized allocated
data with the normal ownership rules. Compile-time execution is not automatic
lifetime management. `Integer`/`Rational`, dynamic results, and library operations
such as `pmap` have their documented materialization contracts. See
[the language specification](language-spec.md) and
[arbitrary-precision numbers](arbitrary-precision.md).

<a id="dir-runtime"></a>
### `#runtime expression` — prevent Thor compile-time execution

Evaluates the operand normally at runtime rather than automatically executing
eligible `fn` calls or power expressions during Thor compilation. Parenthesize a
larger expression when needed: `#runtime (calculate(7) + calculate(8))`.
The marker preserves the operand's value/type, evaluation order, ownership, and
read-only slice permissions. It does not allocate, add runtime dispatch, or
disable Odin's optimizer; the native optimizer can still inline/fold emitted code.

With `required`, `normal`, or `aggressive` execution policy, a required
compile-time context, such as `VALUE :: #runtime calculate(7)`, is an error. Explicit `#run` cannot execute a retained runtime barrier either. A `fn`
containing a barrier can still be called at runtime; automatic evaluation must
respect barriers reached through its callees. Inactive native `when` branches,
including selected generic specializations, do not introduce a barrier. Existing purity rules remain in
force. This is not an escape hatch for impure code in a `fn`.

`--comptime=override` explicitly removes these barriers, including inside helper
projects and transitive callees. All other eligibility and ownership checks
remain in force. `--comptime=required` retains explicit `#run` while suppressing
optional execution. See [compile-time execution modes](comptime-modes.md).

<a id="dir-switch"></a>
### `#switch` — compile-time branch selection

Selects a switch branch during compilation. The selector, when present, and
needed case conditions must be compile-time known. An omitted selector means a
condition switch, as with ordinary `switch`. Conditions are considered in order;
unselected later branches are not executed merely to decide a branch already
selected. `fallthrough` is not supported by compile-time selection.

In statement form, only the selected statements are emitted; an unmatched switch
without a default emits none. It creates no runtime switch/break target. Expression
form follows Thor's switch-value rules, including contextual result typing,
multiple results where supported, and nonreturning branches. A normal value
construction needs a result on every reachable path. Assignment switch expressions
can preserve an already initialized destination on an unmatched path or explicit
`case: break`, according to the ordinary assignment-switch rules. This is not
support for the removed `#if` family.

<a id="dir-mut"></a>
### `#mut parameter` — explicit writable slice contract

Marks the **immediately following parameter name** as requiring writable slice
access. It is supported on slice parameters, including variadics and generic
specializations that are slices. Other parameter types are rejected; a record
merely containing a slice is not itself a slice parameter. It is not a new
general pointer-const syntax.

```odin
copy_values :: proc(source, #mut destination: []int) {
    copy(destination, source)
}
// proc(#mut a, b: []int): only a is explicitly marked.
// proc(#mut a, #mut b: []int): both are explicitly marked.
```

The contract applies even if the current body happens not to write. Unannotated
parameters have their actual mutation/escape behavior inferred, including
transitive calls; a read-only slice may be passed to a proven reader. A writable
alias cannot be recovered from a protected slice by slicing, return aliases,
address-taking, or a wrapper call. Copies of inline numeric/record data retain
the ordinary value-copy rules rather than becoming needlessly immutable.

TLS can show inferred `#mut` hints in grey. Lowering removes this Thor annotation
before native compilation; checking it adds no runtime permission object. See
[read-only slices](slice-access.md) for the complete alias/escape and copy rules.
Other inherited parameter directives retain Odin's grouped-field rules;
the immediate-name extension applies to `#mut` and `#ro`.

<a id="dir-ro"></a>
### `#ro parameter` — enforced read-only slice contract

Makes the immediately following slice input a read-only view inside the callee,
including aliases derived from it, nested slice views, transitive calls, and
returned aliases. Both writable and read-only caller slices are accepted. Writes,
writable element addresses, writable aliases, and writable escapes through the
view are rejected. Independent copies of inline data and independent cloned
backing storage remain writable under the normal slice rules.

```odin
copy_values :: proc(#ro source, #mut destination: []int) {
    copy(destination, source)
}
```

As with `#mut`, this is per name: `#ro a, b: []int` explicitly annotates only `a`.
It is valid on slice inputs, variadics, and generic specializations that are
slices. Scalar/pointer/record parameters, result fields, duplicates, and combining
`#ro` with `#mut` on the same name are rejected. `#ro` is erased before native
compilation and adds no runtime permission object.

An explicit callback type with `#ro` must receive a procedure proven to preserve
that access contract; a writer cannot be smuggled into it through a procedure
assignment/argument. The contract does not freeze external writable aliases,
make allocation ownership automatic, or protect against freeing backing memory.
See [the slice reference](slice-access.md) for copy/escape rules.

## Type and layout directives

<a id="dir-type"></a>
### `#type TypeExpression`

Forces the following syntax to be interpreted as a type where ordinary expression
syntax is ambiguous. A common use is `Callback :: #type proc(x: int) -> int`,
which declares a procedure **type**, rather than a procedure implementation.
It does not calculate the type of a value (`type_of` does that), instantiate a
generic callback, or add runtime type information to a raw pointer.

<a id="dir-packed"></a>
### `struct #packed { ... }`

Requests packed struct layout, removing ordinary padding between fields.
Misaligned fields can make access slower or require target-specific handling;
foreign interfaces must agree on the resulting ABI. Packing is not serialization:
pointers, byte order, and representations still have their native meaning.
Combining layout directives must satisfy native alignment restrictions.

<a id="dir-raw_union"></a>
### `struct #raw_union { ... }`

Overlays struct fields in the same storage instead of laying them out successively.
The result has no active-variant discriminator. The programmer must know which
representation is valid before reading a field. This differs from a tagged
`union`, where checked type assertions can inspect the active variant. It neither
initializes every overlaid field nor makes ownership of different views independent.

<a id="dir-align"></a>
### `struct #align(N) { ... }`, `union #align(N) { ... }`

Requests alignment of the record type. `N` is a supported positive constant power
of two; using parentheses is the normal syntax (unparenthesized legacy operands
can warn). It affects type alignment, containing-object padding, and ABI. It does
not require every field to have alignment `N`. [`@(align=N)`](#attr-align) applies
to a variable instead. The same directive name also has an
[assembly padding form](#assembly-data-and-padding).

<a id="dir-min_field_align"></a>
### `struct #min_field_align(N) { ... }`

Sets a lower bound on field alignment for this struct's layout, subject to native
power-of-two/target checks. It can insert padding between fields and increase the
record size. It does not merely align the start of the struct, and is distinct
from `#max_field_align` and packed layout.

<a id="dir-max_field_align"></a>
### `struct #max_field_align(N) { ... }`

Caps the alignment used for fields in the struct. It can reduce natural padding
and produce less naturally aligned fields, with the same ABI/access concerns as
other nondefault layouts. `N` must satisfy native alignment validation. Explicit
minimum/maximum directives must not request contradictory bounds.

<a id="dir-all_or_none"></a>
### `struct #all_or_none { ... }`

Requires a compound literal of that struct to initialize either all required
fields or none of them. Empty initialization remains the ordinary zero value;
nonempty partial initialization is rejected unless explicitly relaxed with the
supported `#partial` literal form. It is an initialization check, not a guarantee
that fields can never be modified later.

<a id="dir-simple"></a>
### `struct #simple { ... }`

Requires fields to satisfy the native compiler's simple/nearly-simple type rules
and marks the resulting record for its simple comparison/type treatment.
Eligible fields include the native numeric/simple scalar kinds, pointers,
procedure values, bit sets/fields, compatible arrays/vectors/matrices, and records
whose fields recursively qualify; supported single-variant unions can also
qualify. Slices, maps, dynamic arrays, and fixed-capacity dynamic arrays do not.
Pointer comparison is of the pointer value, not a recursive pointee comparison.
The simple-comparison path can use bytewise equality, including for numeric
fields that would otherwise need semantic comparison. In particular, floating
point signed-zero/NaN bit patterns need not compare like ordinary scalar floats.
Only opt in when that representation-based comparison is the intended contract.
It adds no constructors, cloning, destructors, serialization, or resource ownership.
Thor's fieldwise arithmetic is a separate feature with its own allowed fields.

<a id="dir-no_nil"></a>
### `union #no_nil { ... }`

Removes the tagged union's extra nil state and requires at least two variants.
Its zero/default representation uses
the first variant rather than an independent nil variant. Code must not assume
it can assign/test nil as the union's empty state. It is incompatible with
`#shared_nil`; variant and layout constraints are checked natively.

<a id="dir-shared_nil"></a>
### `union #shared_nil { ... }`

Shares the nil representation of the union's variants rather than adding a
separate nil payload representation. All variants must support the native
nil-capable representation required by the checker. It is useful for suitable
pointer-like alternatives, not for arbitrary integer variants. It is incompatible
with `#no_nil` and changes representation/zero-value assumptions.

<a id="dir-subtype"></a>
### `#subtype field: Type`

Marks a supported struct field as a subtype/promoted conversion relationship.
Unlike `using field: Type`, it does not also import that field's member names
into the containing scope. Native subtype rules determine which conversions and
field shape are valid. It is a field directive, not a way to declare general
class inheritance or an implicit user-defined conversion procedure.

<a id="dir-soa"></a>
### `#soa` arrays and slices

Selects structure-of-arrays layout for a supported collection of struct values,
for example `#soa [N]Point`, `#soa []Point`, or `#soa [dynamic]Point`.
Constituent fields are stored in separate columns rather than interleaved records;
indexing/iteration exposes the corresponding logical element. Supported fixed,
dynamic, slice, and pointer forms have native layout and address restrictions.
A normal `[]Point` header is not interchangeable with an SOA slice header.

`soa_zip` (also exposed as `basic.zip`) constructs an SOA view from matching
component slices; it is a builtin call, not another directive. SOA representation
does not imply parallel execution or independent ownership of each field view.
Read-only slice permissions still propagate through Thor's views and aliases.

<a id="dir-simd"></a>
### `#simd [N]Element`

Forms a native SIMD vector type from a supported fixed-length numeric element
array. Element kinds/counts and generated instructions are target dependent.
Operators use vector semantics and ABI, not an allocated dynamic-array loop.
It cannot turn an arbitrary slice, record, or runtime length into a vector type;
explicit CPU feature requirements may be needed for particular instructions.

<a id="dir-row_major"></a>
### `#row_major matrix[Rows, Columns]Element`

Selects row-major storage for a native matrix type. Contiguous stored components
follow rows. The directive affects representation/interop and memory access,
not the mathematical indexing contract. It does not transpose the logical matrix.

<a id="dir-column_major"></a>
### `#column_major matrix[Rows, Columns]Element`

Explicitly selects column-major native matrix storage, which is the default in
this Odin revision. Use explicit layout at ABI boundaries where ambiguity would
be costly. Matrices with different storage layouts are not simply interchangeable
raw byte sequences even when they have the same dimensions.

<a id="dir-sparse"></a>
### `#sparse [Enum]Element`

Allows an enum-indexed array for an enum whose values are not a contiguous range.
Its backing representation covers the required numeric index span, including gaps;
it is not a sparse hash table and can waste substantial space for widely separated
values. This replaces the removed enum-array meaning of `#partial`.

## Parameter and result directives

Parameter directives appear before the applicable parameter name, not as
`@(attribute)` on the procedure. Their ABI, specialization, and optimizer effects
are different from Thor's inferred mutability. They do not automatically prove a
callback eligible for a pure `fn` or `#run`.

<a id="dir-no_alias"></a>
### `#no_alias parameter`

Makes a native nonaliasing promise for a supported pointer-like parameter.
The optimizer can assume the declared accesses do not alias conflicting accesses
through other parameters. This is a contract, not a runtime alias check; violating
it can make optimized behavior invalid. It does not say the parameter is readonly,
noncapturing, or owning, and does not replace Thor's slice permissions.

<a id="dir-c_vararg"></a>
### `#c_vararg args: ..Type`

Marks the native C-ABI variadic parameter, subject to its calling-convention and
last-parameter restrictions. Calls use the native C varargs representation and
required promotions, rather than Odin's ordinary typed variadic slice mechanism.
The callee must understand the supplied argument types/count by its own protocol.
It cannot safely infer an arbitrary list's types at runtime from this annotation.

<a id="dir-const"></a>
### `#const parameter`

Requires an argument known at compile time for a supported procedure parameter
and participates in native specialization. This is a requirement on the argument's
value, not a C-style const qualification of memory reachable through a pointer.
Ordinary Thor procedure parameters already have immutable bindings; `#const`
adds a different compile-time constraint.

<a id="dir-any_int"></a>
### `#any_int parameter`

Allows integer arguments of differing integer types at a compatible integer/enum
parameter, using native conversion rules instead of requiring an exact argument
type match. It does not accept arbitrary truthy values or silently promote to
Thor's `Integer`. Native representability/conversion checks still apply; use
explicit conversions when a narrowing operation is intentional.

<a id="dir-by_ptr"></a>
### `#by_ptr parameter`

Requests pointer-based ABI passing for a supported value parameter, especially
an aggregate that would otherwise be passed by value. Callers still pass a value;
the parameter's source semantics remain the native readonly value form rather
than an explicitly writable pointer input. It is not an ownership transfer or
permission to mutate the original argument. Native restrictions exclude
inappropriate pointer-like types and incompatible signatures.

<a id="dir-no_broadcast"></a>
### `#no_broadcast parameter`

Disables the normal implicit scalar broadcast to a supported vector/matrix-like
parameter. Callers must supply the appropriate shaped value or explicit
construction instead of having one scalar repeated across components. It does
not disable all implicit conversions throughout the procedure.

<a id="dir-optional_ok"></a>
### `proc(...) -> (Value, bool) #optional_ok`

Allows a two-result procedure to be used in a one-result context that omits its
trailing Boolean status. The complete two-result form remains available.
Exactly two results are required, with a Boolean second result. Discarding the
status is permitted, not automatically checked or panicked on. Normal `or_else`
and related status handling can be used where the result shape supports them.
It cannot be combined with `#optional_allocator_error`.

<a id="dir-optional_allocator_error"></a>
### `proc(...) -> (Value, mem.Allocator_Error) #optional_allocator_error`

The allocator-error counterpart of `#optional_ok`. There must be exactly two
results, and the second must be the native `runtime.Allocator_Error` type (also
exposed by `core:mem`). A caller may take just the value or handle both results.
The attribute does not turn allocation failure into success, free partial
resources, or automatically panic; the procedure's own documented failure
contract still matters. It cannot be combined with `#optional_ok`.

`#subtype` is described under [field layout](#dir-subtype). `#no_capture` is a
[reserved/restricted form](#dir-no_capture), not a working general borrow contract.

<a id="param-automatic-caller-metadata"></a>
### Automatic caller metadata: `name :> directive`

In a procedure input list, `:>` declares an automatic metadata parameter:

```odin
capture :: proc(value: int,
    expression :> #caller_expression(value),
    call       :> #caller_expression,
    location   :> #caller_location,
) {
    // Metadata is available as ordinary immutable parameter values here.
}
```

These parameters follow ordinary inputs, use one nonblank name per declaration,
and accept only the three caller-metadata forms shown. A parameter-specific
capture names an earlier ordinary input. If an ordinary default argument was
omitted, its argument-expression capture follows native behavior (empty text).
Whole-call capture uses the original call expression with native canonical
formatting. Arguments are evaluated only once; captured strings are embedded
static data rather than newly allocated strings.

A caller cannot supply an automatic parameter positionally or by name. This rule
and the capture target are part of procedure compatibility through aliases,
explicit callback types, assignments, arguments, and returns. A callback type
must retain matching automatic metadata slots rather than exposing them as
ordinary string/location inputs. Parameter names may differ when their input
positions and capture meanings match.

The parameters remain actual native parameters. They are rejected on foreign
calling conventions, foreign declarations, exported procedures, and automatic
entry points (`main`, `init`, `fini`, `test`). Ordinary `:=` caller defaults remain
overridable and keep native semantics. `fn`/`#run` treat captured metadata as
call inputs, and mapping helpers exclude metadata from job payload shape.
TLS retains `:>` in displayed signatures, excludes automatic parameters from
named-argument suggestions, and skips them in positional signature guidance.
The directives still cannot be placed directly in a procedure body.

## Control flow and checking directives

<a id="dir-partial"></a>
### `#partial switch`, `#partial` compound literals

On a switch/type switch, permits the native incomplete-case form instead of
requiring exhaustive enum/union coverage. It does not invent a result for an
unmatched branch. A Thor `#partial switch` **value expression** still needs the
required fallback/result coverage; use its assignment form when preserving an
existing value is intended.

On a compound literal, permits partial field initialization where an
`#all_or_none` type would otherwise demand all fields. Remaining fields retain
the normal zero initialization. This is not uninitialized memory. The historical
`#partial [Enum]T` array form is removed; use `#sparse` instead.

<a id="dir-unroll"></a>
### `#unroll for ...`

Requests compile-time expansion of a supported loop with compile-time-known
iteration bounds/values. The compiler emits copies of the loop body rather than
a normal loop for those iterations. This can expose constants to optimization
but also increases code size and compilation work. Runtime operations in an
emitted body remain runtime operations; unrolling is not a substitute for `#run`.
Unsupported iteration shapes are rejected rather than being arbitrary macros.

<a id="dir-reverse"></a>
### `#reverse for ...`

Iterates a supported range/collection in reverse order. It does not mutate or
reverse the underlying data. Native indices refer to the corresponding original
positions rather than becoming a fresh increasing counter. Labels can precede
the directive, as in `label: #reverse for ...`. The supported iterable shape is
checked by the native loop rules.

<a id="dir-bounds_check"></a>
### `#bounds_check`

Enables native bounds checks in its supported statement/block/expression or
procedure-body context. It is a scoped checking policy, not a call to an assertion
procedure. It is useful when overriding a wider unchecked region, subject to
whole-build checking options and the native compiler's precedence rules.
It does not add ownership/lifetime checks or make out-of-bounds arithmetic valid.

<a id="dir-no_bounds_check"></a>
### `#no_bounds_check`

Disables the corresponding bounds checks in a supported region. Invalid indexing
then need not produce a clean runtime bounds error and can corrupt memory.
It does not disable Thor's static slice permission or purity analysis. Apply it
only where validity has been established independently; optimizer speed is not
proof that the access is valid.

<a id="dir-type_assert"></a>
### `#type_assert`

Enables native runtime checking of supported type assertions in its region.
This concerns union/`any` assertion behavior, not a compile-time `#assert` or the
Boolean `assert` procedure. Whole-build options and `force-type-assert` can affect
the final policy. A checked failure follows native assertion/panic behavior.

<a id="dir-no_type_assert"></a>
### `#no_type_assert`

Disables corresponding native type-assertion checks for a supported region.
It does not change the actual active union variant or dynamic `any` type; reading
an incompatible representation remains invalid. It cannot safely replace an
explicit status-returning assertion when failure is expected normal control flow.

<a id="dir-force_inline"></a>
### `#force_inline`

Requests forced native inlining in supported procedure-literal/call contexts,
including applicable return-call syntax. It exposes the callee body to the caller's
optimization rather than adding a runtime dispatch. Signature, recursion, ABI,
foreign-body, and optimization-mode restrictions still apply; in particular,
a procedure optimized with `none` cannot simultaneously demand inlining.
It is not a promise of faster execution or purity.

<a id="dir-force_no_inline"></a>
### `#force_no_inline`

Prevents native inlining in its supported procedure/call contexts. This can retain
a distinct call for code size, debugging, or measurement, at the cost of normal
call overhead. It does not stop optimization inside the callee or prevent Thor
from performing independently required compile-time evaluation.

<a id="dir-must_tail"></a>
### `return #must_tail call(...)`

Requires the native call to be emitted as a tail call in a supported return
position. The compiler checks compatible result/parameter shapes, calling
conventions, and other ABI requirements. A call followed by arbitrary cleanup
cannot simply be declared tail-safe. It is an enforced call-shape requirement,
not a general promise that every recursive call uses constant stack space.

## Compile-time resources and source information

Resource paths are resolved by the native resource rules, with Thor preserving
the original file's identity and supported collection paths when lowering into
hidden directories. Embedded file bytes are part of the binary; separate publish
assets and runtime file reads have different packaging/lifetime rules.

<a id="dir-assert"></a>
### `#assert(condition[, message])`

Checks a constant Boolean during compilation. A false condition fails compilation,
optionally with the supplied constant diagnostic message. It is evaluated only
where the enclosing compile-time branch is active. Runtime `-disable-assert` does
not turn it into a runtime check or remove compile-time validation.

```odin
#assert(size_of(u64) == 8, "This representation needs eight bytes")
```

Assertions should only inspect state. Required allocation, I/O, mutation, parser
advancement, or cleanup belongs outside an assertion, including in generated code.
That rule also applies to ordinary runtime assertions, whose removal must not
change successful program behavior.

<a id="dir-panic"></a>
### `#panic("message")`

Unconditionally rejects compilation when reached in an active compile-time
branch. The message must be a constant string. This is distinct from the runtime
`panic(...)` procedure and from a `-> !` nonreturning call used in a switch value
branch. It cannot serve as a recoverable runtime error result.

<a id="dir-config"></a>
### `#config(NAME, default_value)`

Obtains a compile-time configuration value from `-define:NAME=value`, falling back
to the supplied constant default. `NAME` is the configuration identifier, not a
runtime string lookup or an environment-variable name. The selected value must
satisfy the compiler's type/constant rules for the default and its use.

```odin
WORKERS :: #config(WORKERS, 4)
// thor build . -define:WORKERS=8
```

<a id="dir-defined"></a>
### `#defined(name)` or `#defined(package.member)`

Checks whether a name exists in the applicable native scope, yielding a compile-time
Boolean. The native checker restricts this directive to procedure bodies.
It is a legacy mechanism for conditional code; prefer `#config` for declared
build configuration. It does not test whether a file exists or whether a runtime
variable has been initialized, and cannot replace a normal capability contract.

<a id="dir-exists"></a>
### `#exists("path")`

Checks a compile-time filesystem path and returns a constant Boolean. The argument
must be a constant string. It observes the build machine's filesystem under the
resource path rules, not the eventual application's runtime environment. A true
result does not embed, open, or keep the file available after compilation.

<a id="dir-load"></a>
### `#load("path"[, Type])`

Embeds file contents while compiling. Without an explicit element representation,
the usual result is a byte slice; an explicit/contextual supported type can
request `string` or a slice of simple native data. The byte count must fit the
requested representation. It does not parse a text file into numbers, convert
endianness, or deep-deserialize pointer-bearing records. Embedded data has static
backing storage and must not be freed as a heap allocation or modified through
an unsafe alias. Missing/unreadable/incompatible inputs are compile errors.

<a id="dir-load_directory"></a>
### `#load_directory("path")`

Embeds the files directly in a directory, in sorted path order, as a slice of
`runtime.Load_Directory_File` values. Each entry has `name: string` and
`data: []byte`, with immutable embedded data. This is not recursive directory
walking and does not synthesize nested filesystem objects. Invalid, unreadable,
or empty directories fail compilation in the pinned native implementation.
The result is a compiler-provided value, not a
promise that arbitrary use qualifies as an ordinary native `::` scalar constant.
Do not delete or mutate its static backing.

<a id="dir-hash"></a>
### `#hash("text", "algorithm")`

Computes a compile-time integer digest of constant string bytes. The complete
algorithm list in this revision is `adler32`, `crc32`, `crc64`, `fnv32`, `fnv64`,
`fnv32a`, `fnv64a`, `murmur32`, and `murmur64`. The result is an untyped integer
subject to representability when assigned to a concrete integer type. These are
noncryptographic hashes/checksums; do not use them as password hashing or a
collision-resistant trust boundary. [`basic.hash`](../runtime/basic/README.md)
is a separate runtime-capable library facility.

<a id="dir-load_hash"></a>
### `#load_hash("path", "algorithm")`

Computes the same compile-time digest from the bytes of a build-time file.
It accepts exactly the algorithms listed for `#hash`. It does not automatically
embed the file or produce a runtime file watcher. Path resolution and missing-file
errors follow the native resource rules preserved by Thor.

<a id="dir-file"></a>
### `#file`

Produces the source file's path as an untyped string. Thor maps it to the original
Thor file rather than the generated Odin file. The value is source identity,
not necessarily a portable runtime asset path on another machine. It takes no
call parentheses.

<a id="dir-directory"></a>
### `#directory`

Produces the containing source directory as an untyped string. Thor preserves the
original source directory, including its trailing platform separator, so lowering
into `.thor-build` does not redirect source-relative resource logic. This is not
the process's current working directory or the published application's resource
folder. It takes no call parentheses.

<a id="dir-line"></a>
### `#line`

Produces the original source's one-based line number as an untyped integer.
Thor maps generated helpers back to their original source location. This is a
value expression, not a C-style directive for resetting subsequent source line
numbers. It takes no call parentheses.

<a id="dir-procedure"></a>
### `#procedure`

Produces the enclosing procedure's name as an untyped string. It is valid inside
a procedure and follows lexical source identity, with Thor avoiding generated
helper names where source mapping applies. It does not return a callable pointer
or the name of a caller higher on the runtime stack.

<a id="dir-location"></a>
### `#location()` or `#location(entity)`

Produces `runtime.Source_Code_Location`, with `file_path`, `line`, `column`, and
`procedure`. The no-argument form describes the current source location; the
argument form identifies a valid named entity, such as an identifier or package
selector. It is not a general runtime expression evaluator. Thor preserves useful
original source locations in place of lowered helper locations. Use
`#caller_location` in a parameter default when the calling site is desired.

<a id="dir-caller_location"></a>
### `parameter := #caller_location`

A special parameter default that supplies the location of the call site when the
argument is omitted. It is not a general expression for stack walking inside a
procedure body. The caller can explicitly supply an argument instead. Thor maps
the default to original Thor source, including calls routed through generated
helpers where supported by its source identity handling.

<a id="dir-caller_expression"></a>
### `parameter := #caller_expression` or `#caller_expression(earlier_parameter)`

A special parameter default that captures the caller's expression spelling as a
string. The parameter-specific form captures the expression supplied for the
named earlier parameter; the no-argument form represents the applicable calling
expression. It can be used to improve assertion/error messages without manually
passing the source string. It is valid only in the default-argument context and
accepts at most one applicable name.

The result is a compiler-rendered representation, not a guarantee of byte-for-byte
whitespace/comments from the file. Thor preserves source expressions rather than
exposing its generated call syntax. Inspecting the spelling does **not** reveal
ownership, lifetime, whether an expression allocated, or whether a procedure kept
an alias; it cannot implement a general borrow/cleanup checker.

<a id="dir-branch_location"></a>
### `#branch_location`

Used within a deferred statement to obtain the source location of the branch
that caused the enclosing scope to exit, such as `return`, `break`, `continue`,
`fallthrough`, or normal scope completion. It is different from the deferred
statement's own location and from a procedure's caller location. Thor preserves
the original source exit location when inserting its lowering machinery.

The pinned Odin compiler has a separately recorded backend failure for an
anonymous-procedure/defer combination using this directive. That is a native
limitation, not a new Thor branch-location meaning; see the
[compatibility report](odin-superset-audit-2026-11-13/fixes.md) and
[upstream issue inventory](../upstream/README.md). Named-procedure regression
coverage remains in the toolchain. Do not interpret recognition as a promise
that every upstream backend corner case is fixed.

## Assembly directives

These spellings belong to native `asm` syntax. They are not available as arbitrary
ordinary-language annotations. Register names, instructions, operand classes,
calling conventions, and supported architectures remain native/backend specific;
this reference inventories the **hash directives**, not every instruction set.
See [Odin inline assembly](https://odin-lang.org/docs/inline-asm/) and the pinned
[native assembly checker](https://github.com/odin-lang/Odin/blob/8169255b97b85d6a08691d99764f51fb01f92b5a/src/check_asm.cpp)
for complete operand grammar.

<a id="dir-volatile"></a>
### `#volatile` in an assembly specification

Marks assembly as having observable effects so it must not be treated as a
removable unused computation. Outputs/clobbers must still accurately describe
its interaction with native code. It is not a CPU memory barrier, C volatile
variable qualifier, or atomic operation.

<a id="dir-pure"></a>
### `#pure` in an assembly specification

Requires the native assembly checker to verify that the template qualifies as
pure under its control-flow/effect analysis. Purity can also be inferred without
the annotation; the explicit spelling makes failure a diagnostic. Observable
effects, `#volatile`, `#clobber memory`, and a diverging signature prevent this
classification. This is not a Thor `fn` declaration, and accurate declarations
of actual assembly effects remain necessary for correct optimization.

<a id="dir-align_stack"></a>
### `#align_stack` in an assembly specification

Requests the native backend's required stack-alignment treatment around the
assembly. Its necessity and effect depend on target ABI and the assembly's use
of the stack. It does not declare arbitrary stack writes safe or replace accurate
clobber information.

<a id="dir-clobber"></a>
### `#clobber operand` in an assembly specification

Declares a register, flag, or supported memory operand modified by the assembly
outside its ordinary declared outputs. The compiler uses this to avoid keeping
live values in overwritten locations and to model memory effects. Operand forms
are architecture specific. Missing a real clobber is a correctness bug; declaring
one does not emit the instruction that modifies it.

<a id="dir-preserve"></a>
### `#preserve operand` in an assembly specification

Declares a physical register as preserved, rather than clobbered. Native assembly
data-flow analysis treats its incoming value as available. Only supported
registers are accepted: flags and memory are not valid preservation operands.
This declaration does not itself emit save/restore instructions; the assembly
must actually uphold its preservation contract. Valid register names depend on
architecture and ABI, and unrelated memory ownership is unaffected.

<a id="dir-no_init"></a>
### `#no_init` on an assembly operand specification

Suppresses the normal initialization of the applicable assembly register/operand
storage. It is appropriate for a scratch/output location that the assembly writes
before reading. Reading the location before a valid write remains invalid. This
is not a directive for making an ordinary Thor local variable uninitialized.

<a id="dir-pre"></a>
### `#pre` in an assembly memory operand

Selects the supported pre-indexed/writeback memory-address form, particularly for
native ARM-style operands. The base update and effective address follow that
architecture's addressing rules. It is not a `for` loop prefix operator or an
ordinary expression-level preincrement.

<a id="dir-post"></a>
### `#post` in an assembly memory operand

Selects the corresponding post-indexed/writeback address form: access and base
update follow native instruction semantics. It is only valid in the relevant
assembly operand grammar, not as a general pointer arithmetic directive.

<a id="assembly-data-and-padding"></a>
### Assembly data and padding

These directives occur in the assembly instruction/data stream; their operands
must be compile-time constants in the native accepted range.

<a id="dir-byte"></a>
#### `#byte value, ...`

Emits one or more raw bytes, each representable as `u8`. This is useful for exact
instruction/data encoding. The compiler does not validate that arbitrary emitted
bytes form safe instructions, proper alignment, or an ABI-correct function.

#### `#align N`

The [assembly meaning of `#align`](#dir-align) emits padding up to the requested
power-of-two byte alignment. It aligns the following stream location rather than
changing a struct's alignment or a variable's address. Target assembler padding
rules govern the emitted bytes.

<a id="dir-skip"></a>
#### `#skip N`

Reserves/emits the native assembler's skipped byte span (the backend uses `.skip`).
Exactly one nonnegative constant byte count is required in the pinned checker.
It does not advance a Thor iterator, seek a runtime file, or allocate a heap block.

<a id="dir-nop"></a>
#### `#nop N`

Emits target-specific no-operation padding using the backend's `.nops` machinery.
Exactly one nonnegative constant size is required. The emitted instruction
encodings/counts depend on target padding support; the byte-size request should
not be confused with `N` ordinary source statements or a portable timing delay.

## File tags and their options

Use the canonical `#+` spelling before the `package` declaration. Some tooling
parsers also recognize historical `//+` header forms; prefer `#+` for a portable,
explicit source tag. Tags are not runtime statements, and exclusions occur before
normal checking of a file's declarations. Filename platform suffixes and test
suffixes also affect source discovery independently of tags.

<a id="file-build"></a>
### `#+build expression`

Includes a file only when its target-selection expression matches. **Spaces join
conditions with AND; commas separate alternatives with OR.** A `!` negates an
individual condition. A group normally contains at most one OS and one
architecture condition. For example:

```odin
#+build darwin, linux amd64
package platform_code
```

This means macOS on its selected architecture **or** Linux on amd64, not
“macOS and Linux.” Multiple selection tags must each permit the file. Supported
conditions use these native OS/architecture names:

| Kind | Recognized names |
| --- | --- |
| OS | `windows`, `darwin`, `linux`, `freebsd`, `openbsd`, `netbsd`, `wasi`, `js`, `orca`, `freestanding` |
| Architecture | `amd64`, `i386`, `arm32`, `arm64`, `wasm32`, `wasm64p32`, `riscv64` |

OS subtargets use the native `os:subtarget` form. Recognized suffixes are
`default`, `generic`, `iphone`, `iphonesimulator`, `android`, and `playdate`,
subject to valid target combinations. The special `ios` suffix matches either
iPhone or iPhone Simulator. An omitted or `generic` suffix permits that OS's
subtargets; explicit `default` selects its default subtarget only.
The special `ignore` condition excludes its group, and `bedrock` selects the
native bedrock build configuration. A tag's names select **targets**, not the OS
currently running the compiler; available target implementations remain native
compiler dependent. An empty build condition does not exclude a file.

<a id="file-build-project-name"></a>
### `#+build-project-name expression`

Filters by the native build project name, using whitespace-conjoined conditions,
comma-separated alternatives, and individual `!` negation. An empty condition
allows the file. The comparison is with build identity (`ODIN_BUILD_PROJECT_NAME`),
not the source's declared `package` name or executable filename supplied by an
output flag. Thor preserves the original project's identity instead of replacing
it with a generated helper directory name.

<a id="file-test"></a>
### `#+test`

Includes the file only for test compilation. This allows test helpers as well as
`@(test)` declarations to be absent from normal builds. It does not make every
procedure in the file a test. Native `_test` filename filtering is an additional,
independent source-selection mechanism retained by Thor.

<a id="file-ignore"></a>
### `#+ignore`

Excludes the entire file from the build. Its declarations are not made available
merely because another file mentions them. This is useful for reference material
or deliberately disabled source, not runtime conditional execution. Use `when`
for active compile-time declaration/control-flow choices within an included file.

<a id="file-private"></a>
### `#+private`, `#+private package`, `#+private file`

Sets file-wide declaration privacy defaults. Bare/package privacy hides the
file's declarations from importing packages; `file` also limits visibility to
this source file under native privacy rules. This controls source lookup rather
than symbol export or operating-system memory protection. For one declaration,
use [`@(private=...)`](#attr-private).

<a id="file-lazy"></a>
### `#+lazy`

Enables native lazy handling of the file's declarations: unused entities do not
force the same eager declaration processing as ordinary included source.
It is a compiler dependency/checking optimization, not runtime lazy loading of a
library. `-ignore-lazy` overrides it; native documentation mode also has root-package
exceptions. Mark required registration/lifecycle dependencies explicitly rather
than assuming an unreferenced lazy declaration is necessarily emitted.

<a id="file-no-instrumentation"></a>
### `#+no-instrumentation`

Disables normal procedure entry/exit instrumentation for the file. Individual
procedures can explicitly opt back in with `@(no_instrumentation=false)`.
This does not disable sanitizers, the tracking allocator, bounds checks,
source mapping, or debugger information.

<a id="file-feature"></a>
### `#+feature option ...`

Selects per-file native opt-in features. Feature names are separated by whitespace;
applicable Boolean features can be negated with `!`. The full finite list in this
compiler follows. Enabling a feature does not suspend unrelated Thor checks.

<a id="feature-dynamic-literals"></a>
#### `dynamic-literals`

Permits the native dynamically constructed map/dynamic-array compound literal
forms that otherwise require the feature. Those forms can allocate and carry
normal allocator/error/lifetime obligations. This is unrelated to native fixed
array literals or Thor comprehensions, which have their own construction rules.

<a id="feature-global-context"></a>
#### `global-context`

Permits the native implicit context in applicable global initialization and
lifecycle contexts. This enables context-dependent initialization where the
native default would prohibit it. It does not make runtime global initialization
a compile-time constant, or make ambient context usable in a pure `fn`.

<a id="feature-using-stmt"></a>
#### `using-stmt`

Allows standalone native `using value` statements in contexts requiring this
opt-in. **Thor automatically supplies the tag under the hood when included Thor
source actually needs it**, preserving original diagnostics/debugger locations.
Ordinary `using` forms already legal in Odin do not need redundant insertion.
An explicit `#+feature !using-stmt` requests the restrictive policy; native `.odin`
source retains native handling. The tag does not allow `using` on a scalar that
has no fields, and it does not grant writable access to immutable fields.

<a id="feature-force-type-assert"></a>
#### `force-type-assert`

Forces native type-assertion checking under the file's feature policy, including
where weaker local checking defaults would otherwise apply. This is a check
policy for type assertions, not a rule about Thor function purity or static slice
permissions. The name is accepted in this revision even though one native
invalid-feature diagnostic's suggestion list omits it.

<a id="feature-integer-division-by-zero:trap"></a>
#### `integer-division-by-zero:trap`

Selects the native error/trapping behavior for integer division by zero: a
proven invalid constant operation is diagnosed, and a runtime zero divisor traps
under the native policy. It does not provide a recoverable optional error. Only
one integer-division-by-zero mode may be enabled in a file.

<a id="feature-integer-division-by-zero:zero"></a>
#### `integer-division-by-zero:zero`

Selects a zero quotient for applicable native integer division by zero. Remainder
and modulo by zero return the dividend under this mode. The native policy also
handles supported typed integer constant evaluation; it is not solely a runtime
switch. It is an arithmetic behavior change, not merely a diagnostic suppression,
and is mutually exclusive with the other modes.

<a id="feature-integer-division-by-zero:self"></a>
#### `integer-division-by-zero:self`

Selects the dividend itself as the quotient for applicable native integer division
by zero; remainder/modulo by zero instead return zero. Supported typed integer
constant evaluation follows this policy too. Native operand/operator rules
determine applicability; it does not affect floating-point division or Thor's
separately implemented arbitrary-precision failure contract.

<a id="feature-integer-division-by-zero:all-bits"></a>
#### `integer-division-by-zero:all-bits`

Selects the all-bits-set quotient for applicable native integer division by zero
(`-1` for signed integers, the maximum value for unsigned integers). Remainder
and modulo by zero return the dividend. Supported typed integer constant
evaluation follows the selected policy too. Use one explicit zero-division mode
rather than combining them or relying on `!` to undo one: the native parser
rejects negation for several mode names. This is not a policy for arbitrary
untyped rational/floating-point arithmetic or Thor's `Integer`/`Rational` runtime.

<a id="file-vet"></a>
### `#+vet option ...`

Changes native per-file vet policy. A name enables its check; `!name` disables it.
A bare `#+vet` enables the native bundle. This is **not** the same facility as
`thor vet` / `--vet`, which analyze original Thor source and conventions.
Checking-view support for Thor extensions is explained below. The complete recognized
option list is:

Thor additionally accepts <a id="vet-no_alloc"></a>`no_alloc`, enforcing the
[allocation-free contract](no-alloc.md) for every procedure and runtime
initializer in this file. This option produces compile errors without requiring
`thor vet`; it is consumed before Odin sees the file. It can be combined with the
native names below. A bare native `#+vet` does not enable it.

| Native option | What it checks |
| --- | --- |
| <a id="vet-unused"></a>`unused` | Bundle of unused variable and import checks; procedure checking is a separate option in this revision. |
| <a id="vet-unused-variables"></a>`unused-variables` | Variable declarations that native usage analysis considers unused. |
| <a id="vet-unused-imports"></a>`unused-imports` | Package imports not used by the file. |
| <a id="vet-unused-procedures"></a>`unused-procedures` | Procedure declarations not used under native scope/reachability rules. |
| <a id="vet-shadowing"></a>`shadowing` | Native declaration shadowing policy. |
| <a id="vet-using-stmt"></a>`using-stmt` | Disallows the standalone using-statement form under vet policy. |
| <a id="vet-using-param"></a>`using-param` | Disallows using-parameter forms under vet policy. |
| <a id="vet-style"></a>`style` | Native syntax/style requirements, including required trailing commas and deprecated syntax. |
| <a id="vet-semicolon"></a>`semicolon` | Unnecessary statement terminators. |
| <a id="vet-deprecated"></a>`deprecated` | Deprecated constructs covered by the native vet policy; declaration deprecation also has its own diagnostics. |
| <a id="vet-cast"></a>`cast` | Redundant/inappropriate explicit casts under native vet rules. |
| <a id="vet-tabs"></a>`tabs` | Native tab/indentation formatting policy. |
| <a id="vet-explicit-allocators"></a>`explicit-allocators` | Calls omitting allocator arguments where the native explicit-allocator policy requires them. |
| <a id="vet-when-shadowing"></a>`when-shadowing` | File-scope conditional declarations shadowing builtins. |
| <a id="vet-nil-deref"></a>`nil-deref` | Dereferences that native flow analysis identifies as invalid nil access. |
| <a id="vet-uninitialized"></a>`uninitialized` | Reads identified as uninitialized by native flow analysis. |
| <a id="vet-atomic-access"></a>`atomic-access` | Plain/atomic access consistency under native synchronization analysis. |

`extra` appears in an old diagnostic suggestion list but is **not** a recognized
option in this revision. Unknown option names are not a way to enable Thor checks.

<a id="native-check-compatibility"></a>
#### Native-check compatibility

All names above, bare `#+vet`, negation and repeated tags retain native behavior
for Odin-compatible text in `.thor` files. Source-sensitive policies inspect a
separate native checking view, before ordinary Thor validation and compilation.
It preserves original scopes, formatting and usage; it also supports `fn`,
immutable declarations and `#mut` parameters. No `--odin-compatible` flag is
needed for this checking view. Native file tags override package selectors as in
Odin. Native severities and option errors are preserved.

F-string fields and formatting widths count as caller reads. Comprehension
inputs, bindings, filters, keys, values and spreads participate in native checks.
The checking view preserves their control flow and evaluation order; original
formatting is checked before projection. Compiler-owned helpers and implicit
construction allocations do not create source-policy findings. Source-written
allocation/deletion calls still need explicit allocators when requested.
See [the native-policy audit](native-vet-audit.md) for details. `semicolon` and
`when-shadowing` retain support through the full Thor lowering. Ordinary compilation and `#+vet no_alloc` alone do not run
an additional native check.

Odin uses **spaces**, not commas, between native policy names. Thor accepts
commas in lists containing its custom `no_alloc` option and removes that option
before native validation. `extra` is an obsolete native diagnostic suggestion,
not an accepted policy. See the [native vet audit](native-vet-audit.md) for
coverage and the remaining checking-view boundary.

<a id="file-analysis"></a>
### `#+analysis option ...`

Selects native per-file analysis. A bare tag enables the analysis bundle; `!name`
disables a named analysis. These analyses diagnose/inspect code; they do not emit
the synchronization or ownership operations a program needs. There are exactly
two recognized names in this revision:

<a id="analysis-escape"></a>
#### `escape`

Enables native stack/address escape analysis for the file, including where
`-no-escape-analysis` would otherwise disable the broader analysis. It detects
supported invalid escapes of automatic storage. It is not a tracing GC or
ownership type system and is distinct from Thor's slice escape/effect summaries.

<a id="analysis-atomic"></a>
#### `atomic`

Enables native atomic-ordering analysis for the file, including where
`-no-atomic-analysis` would otherwise disable it. It can diagnose mismatched
release/acquire/relaxed usage under native analysis rules. It uses supported
synchronization descriptions such as `@(synchronizes=...)`; an annotation alone
does not perform a synchronization operation. Flow-sensitive conclusions on
lowered code have the [compatibility limitations](#native-check-compatibility)
described above; this tag is not a claim of exhaustive race detection for Thor.

## Obsolete, removed, and reserved forms

These entries are included so that an exhaustive inventory does not recommend
syntax that is recognized only to issue an error or is left in a tooling AST.
They are not supported alternatives to the preceding features.

<a id="attr-deferred"></a>
### `@(deferred=cleanup)` — removed

The old native deferred-procedure attribute is rejected. Choose the explicit
`deferred_none`, `deferred_in`, `deferred_out`, `deferred_in_out`, or applicable
`_by_ptr` mode according to the cleanup procedure's exact signature.

<a id="attr-force"></a>
### `@(force)` on a foreign import — removed

The historical force-link annotation is rejected. Use `@(require)` on the
foreign import when the library must be retained without ordinary referenced
symbols. This does not mean arbitrary `force` custom attributes gain semantics.

<a id="dir-field_align"></a>
### `#field_align(N)` — deprecated

Recognized as the old minimum-field-alignment spelling and can emit a deprecation
diagnostic. Use [`#min_field_align(N)`](#dir-min_field_align) instead. Do not
replace it with `#align(N)` unless changing the layout contract is intended.

<a id="dir-maybe"></a>
### `union #maybe { ... }` — removed

The old nil-inclusive union tag is rejected. Ordinary tagged unions already
include the nil alternative unless a supported nil-layout directive changes it.
Use normal `union`, `#no_nil`, or `#shared_nil` according to the desired contract.

<a id="dir-relative"></a>
### `#relative(IntegerType) ^T` — removed

The old compiler-provided relative pointer/slice representation is rejected by
the native checker. Use the explicit facilities in `core:relative` and follow
their base-address and representation rules. Thor does not restore the removed
syntax while forwarding it to the same backend.

<a id="dir-no_copy"></a>
### `#no_copy` — tooling-only/reserved

Thor's inherited tooling parser can record this name, but the pinned native
compiler does not provide a usable `#no_copy` type directive. It is not an
implemented move-only type or clone/delete safeguard. Use documented library
ownership contracts; parser recognition is not semantic support.

<a id="dir-no_capture"></a>
### `#no_capture parameter` — reserved/restricted

The AST has this flag, but the pinned checker reports it reserved for concrete
pointer-like parameters. It rejects unsuitable value types; ordinary variadic
parameters already imply the relevant native no-capture treatment, and explicitly
adding it there is diagnosed. It is incompatible with C varargs. Polymorphic
parsing acceptance does not make it a complete enforced borrowing feature after
specialization. Do not use it as a replacement for Thor's slice-effect analysis.

<a id="dir-include"></a>
### `#include` — rejected

Recognized to give an error directing code toward normal `import` syntax.
It does not textually insert a file like the C preprocessor. Use package imports
for source and `#load` for embedding resource bytes.

<a id="dir-define"></a>
### `#define` — rejected

Odin/Thor do not provide C-preprocessor macros through this spelling. Use named
`::` constants, `#config` with build `-define:...` flags, normal `when`, generic
procedures/types, or supported Thor compile-time constructs as appropriate.

<a id="dir-load_or"></a>
### `#load_or` — diagnostic stub, not implemented

A native error path recognizes this spelling as something that would require a
call, but the callable-directive checker has no implementation for it. It is not
a supported optional-file-loading facility. Use an explicitly supported
`#exists`/compile-time selection arrangement and `#load`, subject to normal
resource errors.

<a id="dir-if"></a>
<a id="dir-else"></a>
<a id="dir-else_if"></a>
<a id="dir-elif"></a>
<a id="dir-end"></a>
<a id="dir-endif"></a>
### `#if`, `#else`, `#else_if`, `#elif`, `#end`, `#endif` — no Thor conditional-preprocessor feature

Thor's former hash-conditional feature was removed. None of these spellings is
a supported replacement for ordinary Odin `when ... { ... } else { ... }`.
Use `when` for native compile-time conditions and `#switch` for Thor's supported
compile-time switch selection. There is no standalone `#else` attached to a
`#switch` case.

## Related syntax that is not an attribute or directive

- `when`, `defer`, `using`, `distinct`, `context`, `or_else`, `or_return`,
  `or_continue`, `or_break`, and Thor's `or_panic` are keyword/operator forms,
  not `#` directives.
- `len`, `cap`, `size_of`, `align_of`, `type_of`, `type_info_of`, `typeid_of`,
  `offset_of`, `swizzle`, `soa_zip`, and Thor's `result_type_of` are builtin
  expressions/calls. `basic.zip` and other library aliases do not introduce
  additional directives.
- A procedure convention such as `proc "contextless"` or `proc "c"` is string
  syntax in a signature. It is not an attribute; the foreign-block default
  attribute is listed separately.
- A struct field's string tag, for example a display/serialization name, is
  metadata in field syntax. Libraries interpret applicable tags; it is not a
  general `@(name)` member attribute.
- `#config` consumes `-define:NAME=VALUE` options, but CLI flags themselves are
  not source directives. `thor help -full` documents command options.
- `@(borrows=...)`, `@(naked)`, and `@(warning=...)` are not built-in attributes in
  this implementation. Custom-name acceptance alone does not implement them.
- `@(static)`, `@(thread_local)`, `@(require)`, `@(linkage=...)`,
  `@(link_name=...)`, `@(link_prefix=...)`, `@(link_suffix=...)`, and `@(rodata)`
  cannot be used to turn a compile-time constant **value** declaration into a
  runtime storage declaration. The native constant-value handler explicitly
  rejects these placements; type and procedure declarations have their own
  attribute handlers even though they also commonly use `::` syntax.

## Sources and maintaining the inventory

The implementation takes precedence over broader or newer web documentation.
Primary native sources for this reference are the pinned
[attribute handlers](https://github.com/odin-lang/Odin/blob/8169255b97b85d6a08691d99764f51fb01f92b5a/src/checker.cpp),
[parser and file-tag grammar](https://github.com/odin-lang/Odin/blob/8169255b97b85d6a08691d99764f51fb01f92b5a/src/parser.cpp),
[type checks](https://github.com/odin-lang/Odin/blob/8169255b97b85d6a08691d99764f51fb01f92b5a/src/check_type.cpp),
[callable directives](https://github.com/odin-lang/Odin/blob/8169255b97b85d6a08691d99764f51fb01f92b5a/src/check_builtin.cpp),
[expression checks](https://github.com/odin-lang/Odin/blob/8169255b97b85d6a08691d99764f51fb01f92b5a/src/check_expr.cpp),
[synchronization analysis](https://github.com/odin-lang/Odin/blob/8169255b97b85d6a08691d99764f51fb01f92b5a/src/check_atomics.cpp),
and [feature/vet names](https://github.com/odin-lang/Odin/blob/8169255b97b85d6a08691d99764f51fb01f92b5a/src/build_settings.cpp).
Official introductions are available for
[attributes](https://odin-lang.org/docs/attributes/),
[directives](https://odin-lang.org/docs/directives/), and
[assembly](https://odin-lang.org/docs/inline-asm/); they do not list every
internal/reserved spelling or every restriction in this compiler revision.

Thor's additional semantics are implemented in
[the parser](../frontend/parser/parser.odin),
[Thor expression parsing](../frontend/parser/thor_expressions.odin),
[strict unused declarations](../rust/src/unused_attributes.rs),
[source identity](../rust/src/sourceidentity.rs), and
[branch-location lowering](../rust/src/branchlocations.rs).
The [language specification](language-spec.md),
[slice-access reference](slice-access.md), and
[native vet compatibility audit](native-vet-audit.md) expand on the relevant
contracts and regression coverage.

<a id="maintaining-this-reference"></a>
### Checking completeness after changes

Run the source inventory audit from the compiler checkout:

```sh
python3 tools/check_language_reference.py
# Or select an installed source checkout explicitly:
python3 tools/check_language_reference.py --odin-root /path/to/Odin
```

It extracts names from native attribute handlers, directive parser/checker paths,
parameter flags, assembly checks, file-tag handlers, and feature/vet/analysis
registries, and compares them with this document's entry anchors. It also checks
Thor's directive spellings and local documentation links. A newly recognized name
must receive a description with its status and placement, not just a passing
mention in a list. The audit catches inventory drift; it is **not** a proof of
all semantic descriptions or of target-specific runtime correctness. Review
changed handlers and run the relevant language regressions when behavior changes.
