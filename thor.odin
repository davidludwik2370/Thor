package main

import "core:fmt"


// V0 - brainstorming
/*

// Thor lang: maybe can compile to odin?

// FEATURES

// specific imports: / pulls in the names that follow, the lib is always pulled in also
import "core:math" / sin, cos, tan 
import "core:math" { sin, cos, tan }
// becomes
import "core:math"
sin :: math.sin
cos :: math.cos
tan :: math.tan

// pure function type: fn
// subset of procs
// evaludated at compile time when called with compile time known args
// prob makes sense to not accept any pointers/stuff with pointers as inputs?
foo :: fn(x: $T) -> T
// becomes 
foo :: proc(x: $T) -> T

// #run directive, forces any proc to run at compile time, come what may in terms of io
// maybe give warning if proc has side effects?
// maybe set any global variables refrenced to their 0 value?
// not sure how this will work, maybe pull func and dependencies out to new file, compile, run that, then put the answer back in its place?

// const declaration, value does not need to be known at compile time
// value stored in rodata if compile time known
x :> 10
y: int > 22
// compile error if you try to change value once set
// open question: what do we want to do about const pointers / should we have const refrences?

// sets, can use normal set operations
// all values must be same type
// still not sure about the best syntax
s: set[int] // pretty sure I do want this
// not sure what the brackets should look like
// I want in place sets to be as thing
// as in `if x in [1, 2, 3] {}`
// options:
s = [1, 2, 3, 4, 5]
x := [1, 2, 3, 4, 5]
x := (1, 2, 3, 4, 5)
x :> <1, 2, 3, 4, 5>
// or maybe
s: set[int] = {1, 2, 3}
s = {1, 2, 3, 4, 5}
x :> {1, 2, 3, 4, 5} // maybe curlys default to a set? prob not, confusing with type coersion
y := set[int]{1, 2, 3, 4, 5}
// whatever the syntax is, 
my_set: set[int]
my_set += "apple"
my_set |= "banana" // same thing
my_set -= "apple"
//becomes
my_set: map[string]struct{}
my_set["apple"] = struct{}{}
my_set["banana"] = struct{}{}
delete_key(&my_set, "apple")

// maps / sets will allocate by default
// can be backed with static buffer like dynamic arrays as in:
a: map[string; 22]int
b: set[int; 18]
// not sure how to do that, prob create a custom buffer backed allocator, and set that as the allocator?

// general `in` operator, evals to bool, uses linear search or hashmap
b: bool = x in [1, 2, 3]
if x in my_array {}
// becomes
b: bool = x == 1 || x == 2 || x == 3
// maybe do something like isin() proc from the Library.odin for larger sequences

// switch stmt returns: if all arms must are only an expression of the same type, and the expression is captured by something
// if a return is being captured, no break of fallthrough operator, must be exhaustive or have default case:
x := switch y {
    case 1: 1
    case:   2
}
// becomes
x: int
switch y {
    case 1: x = 1
    case:   x = 2
}

// f strings like python basically, not sure how to handle those
f"a, b {myval}"
// becomes something like
fmt.aprintf("a, b {}", myval)
// but I want r strings as well, also I want to be able to do 
f"a, b {my_num:,}"
// and the other justify right formatting stuff python can do

#if #else instead of when
#if X==1 {
    Y :: 3
} #else if X==2 {
    Y :: 4
}

// or also valid:
X :: #switch V {
    case 1: 3
    case 2: 4
}
    
// both become
when V == 1 {
    X :: 3
} else when V==2 {
    X :: 4
}

// last element of array accessesd by -1
foo[-1]
foo[:-1]
// becomes
foo[len(foo)-1]
foo[:len(foo)-1]
// or really
foo[x]
// becomes
foo[x<0 ? len(foo)-x : x]

// also this now works
x := 2 > 1 ? 27
           : 31
becomes
x := 2 > 1 ? 27 \
           : 31      
           
// MAYBE - not sure about these:
// all allocations are dropped at the end of created scope, unless the lifetime is taken by something else?
// implicit methods as in any funt that takes an address of type T, you could do 
    my_dynamic_int_array.append(22)
    // becomes
    append(&my_dynamic_int_array, 22)
           
*/

// v1
/*
1. ok lets keep it 
import "core:math" / sin, cos, tan 
and just for now assume that the last one will not have a comma
so you could do 
import "core:math" / 
sin, 
cos, 
tan

still a bit torn on the syntax, lmk if you have other ideas

2 & 3. ok for now lets make it so that pure fn can only accept regular values, no pointers, handles etc
and they can only call other literal pure fn()

that said, the compiler must have the ability to determine the purity of a function, so for now we can make it so the 
#run directive only works on functions that either are literal pure fns or would be allowed to be if the function and those it calls would be allowed to be pure fn
then it should be easier to do the strategy to pull out the relevant code, compile it to an aswer, and put it back. 
again it would be that fn() with constant args automatically gets compiled at compile time, 
as far as differences due to cross compilation are concerned, we'll ignore those for now
and ya we will def want to cache results
like we should be able to create an array of size cube root of 7, for instance:
SIZE :: pure_cube_root_fn(7)
my_array: [SIZE]int

4. 
ya immut it the better thing to call it
lets just enforce immutable storage

but this should work like:
x: int > rand_int() // OK
x += 1 // Compile error

5. 
names := set[string]{"apple", "banana"}
I would keep [...] for sequences and avoid angle brackets, which interact awkwardly with comparisons. Explicit set[T]{...} also gives empty sets an unambiguous type.
For operations, I would distinguish element insertion from set union: s += element is reasonable, while s |= other_set should mean union. Giving |= both meanings buys little.
Keep set[T] as a distinct Thor type internally, even if its representation uses a map.
I'm cool with the above

6. ya it already works that way

7. ya that seems difficult, will punt on that for now

8.
ya we need to not do the get_value() repeatedly thing
so the [...] in get_value() in [1, 2, 3] is a collection type
def want not_in as well
is a new type called a collection?
should x := [1, 2, 3] work?
or would they only be used in expressions

9. 
I would initially support single-expression arms, require a default unless exhaustiveness is established, and permit an explicit destination type when inference is ambiguous.
ok that sounds good
i basically want it to work the same as the ternary operator...
actually maybe we implement with nested ternarys?

10.
honestly the biggest thing is I want an , as thousands seperator. if
v := f"a, b {my_num:,}"
becomes
fmt.aprintf("a, b {}", format_commas(value))
that would be fine, but I would like python's padding if ability possible, seems like it wouldn't be much harder
I forgot about the `` being raw, don't need the r then, we can drop that, as long as f`a b c {next}` then also works

11. all that sounds good to me, except I would prefer if comp time switch worked like regular switch, it\n that it expects an evaluation if you assign
something to it, otherwise a statement

12. good catch on the sign error
all sounds good to me

13. ya that is easy

14. ok ya that's to hard, we won't do that

15. we'll punt on that for now, maybe come back later
*/

// v1 pt2
/*
1. all good, we'll retain the file-private alias behavior discussed earlier
2. I would like Strings and slices
maps / sets would be nice but we can leave those out if the allocation situation makes them more akward
otherwise I agree with all you're suggestions there
3. agree fully
4. agree fully
5. agree fully
6. agree fully
7. agree fully, we can free intermediate string, or do it another way that avoids the second allocation, in you prefer
8. agree fully
9. sounds good

*/

// V1 SPEC
/*
THOR V1 IMPLEMENTATION SPECIFICATION

0. STATUS AND SCOPE

This is the implementation reference for v1. It supersedes the exploratory
FEATURES, v1, and v1 pt2 notes above wherever they differ.

Thor is a source-language superset of Odin plus the additions specified here.
Existing Odin constructs remain accepted in Thor source; additions such as #if
do not replace or remove Odin's original syntax. Thor transpiles to ordinary Odin
and invokes the existing Odin compiler. Odin remains responsible for ordinary
type checking, machine-code generation, linking, and its existing runtime rules.

V1 includes:
- Selective imports with file-private aliases.
- Multiline ternaries without explicit line continuation.
- Compile-time #if and statement/expression forms of #switch.
- Inferred fixed-array literals and general in / not_in membership.
- Runtime switch expressions.
- Runtime-initialized immutable storage.
- Map-backed sets and set operations.
- Negative sequence indexing and slicing.
- Ordinary and raw interpolated strings, integer grouping, and basic padding.
- Verified pure fn declarations, approved internal allocation, compile-time
  execution of eligible calls, #run, and persistent result caching.

Deferred:
- OLS changes, a Thor formatter, polished diagnostics, and debugger source maps.
- General ownership, borrow checking, or automatic cleanup in ordinary proc code.
- Fixed-capacity maps/sets and implicit method syntax.
- Maps, sets, and dynamic arrays as pure-function inputs or results. Dynamic
  buffers may be used internally through approved pure allocation operations.
- Arbitrary pointers, foreign calls, or side effects during compile-time execution.
- Cross-compilation correctness for compile-time execution; v1 runs natively.
- Full Python formatting, including locale support and dynamic format fields.

The allocation-bearing pure-result fallback in section 12 is explicitly allowed
if needed. Cases outside the supported subsets must be rejected. Required v1
features cannot be silently deferred, and purity, immutability, and required
compile-time execution must not be weakened to make an unsupported case compile.


1. COMPILER AND PROJECT CONTRACT

- Use a real tokenizer/parser, preferably adapted from Odin's tooling packages.
  Preserve source spans and comments. Regular-expression replacement is not a
  sufficient implementation for nested syntax, scopes, or purity checks.
- Recognize Thor forms in their grammatical positions. Existing Odin identifiers
  named fn or set should remain usable where there is no Thor syntax ambiguity.
- Default source extension: .thor. Existing .odin files can coexist in the project
  and remain ordinary Odin. Do not require editing core/vendor packages.
- Provide thor transpile, check, build, run, and test commands. Accept a project
  directory or a single source file, using Odin's corresponding package/file mode.
- Transpile imported project Thor packages as needed. Preserve package layout,
  relative imports, library collections, file-selection/build tags, and resource
  lookup such as relative #load paths. Exclude generated output from input scans.
- Emit into a separate build directory, never over the source files. Diagnose
  collisions such as foo.thor and foo.odin both generating the same output path.
- Pass applicable defines, native target settings, compiler flags, and collection
  paths consistently to helper compilations and the final Odin invocation.
- Generated helpers/imports/temporaries must avoid user-name collisions. Add
  required support imports without depending on a user import named fmt, etc.
- Ordinary Odin semantics remain in force except for explicit Thor additions.
  Do not globally change evaluation order or allocation behavior of Odin code.
- For new Thor constructs, evaluate operands once, in source order, except where
  the construct explicitly selects a branch or short-circuits.
- Keep enough symbol/type information for Thor-specific checks; delegate ordinary
  checking to Odin or generated generic helpers wherever practical.
- Basic Thor errors and raw Odin diagnostics against generated files are adequate
  for v1. Failed parsing, checking, helper execution, or compilation must produce
  a nonzero exit status. Keep generated files available for investigating errors.
- Source-location expressions and runtime traces may identify generated Odin in
  v1. Retaining source spans now does not require implementing full remapping.


2. SELECTIVE IMPORTS

Syntax:
    import "core:math" / sin, cos, tan
    import m "core:math" / sin, cos

Multiline syntax:
    import "core:math" /
        sin,
        cos,
        tan

- The package namespace is still imported normally.
- The slash begins the selected-name list. A comma continues the list across a
  newline; the final name has no trailing comma. Permit intervening comments.
- Each selected name introduces a file-private alias to that package member.
  For example, sin aliases math.sin in the importing file only.
- Preserve procedure groups and polymorphic declarations through aliases.
- Reject conflicts with another declaration in that file and invalid/private
  imported members. Do not introduce unintended package-wide exports.
- V1 aliases cover procedures, types, and constants. Selecting a mutable package
  variable is unsupported; do not substitute a copied or frozen value for it.
- Renaming individual selected members and brace-form imports are deferred.


3. MULTILINE TERNARIES

This must parse without a continuation character:
    x := condition ? first_value
                   : second_value

- Treat a ternary awaiting its colon or an operand as an unfinished expression.
- Preserve existing Odin statement-termination behavior elsewhere.
- Handle nested ternaries, comments, and surrounding parenthesized expressions.
- Emit parentheses or continuations as necessary so the generated Odin parses.


4. COMPILE-TIME BRANCHING

Statement syntax:
    #if CONDITION {
        VALUE :: 1
    } #else if OTHER_CONDITION {
        VALUE :: 2
    } #else {
        VALUE :: 3
    }

- Lower #if / #else if / #else to Odin when / else when / else.
- Conditions must be compile-time-known booleans, including values obtained from
  eligible compile-time function calls.
- Match Odin when behavior for declarations and their visibility. Parse untaken
  branches, but do not execute compile-time calls or enforce active-branch
  semantic requirements in branches that Odin would leave unchecked.
- Ordinary Odin when statements, else when chains, and compile-time conditional
  expressions remain valid in both .thor and .odin files, with Odin's normal
  meaning. #if is an additional spelling, not a mandatory replacement.
- Preserve when control flow in generated Odin. Lower any nested Thor constructs
  as needed while retaining the same branch-selection and declaration-scope rules.
  Odin when and Thor #if may coexist in one file and be nested within each other.

#switch uses the same case layout in statement and expression positions:
    #switch MODE {
        case 1: VALUE :: 10
        case:   VALUE :: 20
    }

    VALUE :: #switch MODE {
        case 1: 10
        case:   20
    }

- Both statement and expression forms also permit omitting the selector:
    #switch {
        case FIRST_CONDITION:  VALUE :: 10
        case SECOND_CONDITION: VALUE :: 20
        case:                  VALUE :: 30
    }

    VALUE :: #switch {
        case FIRST_CONDITION:  10
        case SECOND_CONDITION: 20
        case:                  30
    }

- Position determines whether #switch selects statements or produces a value.
- An omitted selector means true. Its case expressions are boolean conditions,
  following the same first-match rules as an ordinary selectorless Odin switch.
- The selector, when present, and each evaluated case match/condition must be
  compile-time known. Evaluate conditions in source order, at most once each,
  stopping at the first match. Do not execute later conditions or their #run/fn
  calls after a match, including when conditions share a comma-separated case.
- Statement form lowers to a when chain and may omit a default, selecting no
  statements when no case matches.
- Expression form follows section 7's single-expression-arm and exhaustiveness
  rules. Lower to compile-time conditional expressions or equivalent declarations.
- Only the selected expression arm is evaluated. Its result must meet the
  surrounding constant-expression/type requirements.
- No fallthrough between compile-time branches. A #switch does not introduce a
  runtime switch target for break. Control flow in statement arms must otherwise
  have valid Odin targets, such as return or break from an enclosing loop.


5. INFERRED FIXED-ARRAY LITERALS

Syntax:
    values := [1, 2, 3]

Meaning:
    values := [3]int{1, 2, 3}

- These are ordinary fixed arrays, not a new collection type and not sets.
  Inline lists of types used for compile-time membership are a separate syntax
  case described in section 6; they do not require runtime arrays of types.
- Allow literals wherever an array-valued expression is valid, including
  declarations, assignments, procedure arguments, and membership tests.
- Infer the element type from an expected array type when available; otherwise
  use the first element's type, applying Odin's normal defaulting for literals.
- Every element must be assignable to that element type. Do not invent general
  mixed-type promotion. Ambiguous first elements require explicit type context.
- An empty literal requires an expected element type and has length zero.
- Every element is evaluated exactly once in source order. type_of-based
  inference must not execute the first expression a second time.
- Preserve Odin's existing [N]T and [?]T syntax; distinguish array types and Thor
  literals through the grammar.
- Array-to-slice use follows Odin's rules. V1 does not introduce an implicit
  escaping slice conversion for these literals; named backing arrays can use [:].
- No heap allocation is implied solely by using this literal syntax.


6. MEMBERSHIP

Syntax:
    value in container
    value not_in container
    next_value() in [1, 2, 3]

- The result is bool. not_in negates the same membership operation.
- Support ordinary fixed arrays, slices, dynamic arrays (including fixed-capacity
  dynamic arrays), Odin maps/bit sets, and Thor sets.
- Sequence membership uses equality and linear search. Types must support the
  necessary equality operation and ordinary element/key assignability.
- Map, bit-set, and Thor-set membership uses the corresponding container lookup.
- No substring-search meaning for strings in v1.
- Evaluate the needle and container once in source order.
- A literal container is constructed before searching: all its element
  expressions are evaluated, even if an earlier element would match.
- An equality-chain optimization is allowed only if it preserves all evaluation
  behavior. In particular, never duplicate the needle or skip element side effects.
- Retain Odin's operator precedence and existing membership meanings.

Compile-time type membership and generic constraints:
    my_gen :: proc(x: $T) -> T where T in [int, f64] {
        return x
    }

- Support in / not_in against inline type lists when the left operand denotes a
  compile-time type, including generic type parameters in Odin where clauses.
- This produces a compile-time bool usable in generic constraints and other
  constant-expression contexts. It must not depend on a runtime contains helper,
  allocation, an array of runtime typeid values, or native helper execution.
- Lower the example constraint to (T == int || T == f64). Lower
  T not_in [int, f64] to (T != int && T != f64), or an equivalent constant form.
  Parenthesize the result so surrounding constraint operators retain precedence.
- Compare exact Odin type identity. Aliases match their aliased type; distinct
  types match only when explicitly included. This is not an underlying-type or
  implicit-conversion test. Resolve type names in their actual lexical scope.
- List entries must denote types known when the constraint is instantiated.
  Reject mixed lists of types and ordinary values. Duplicate types are harmless;
  an empty type-membership list gives false for in and true for not_in.
- Preserve Odin's existing generic inference, where-clause composition, and
  rejection of nonmatching instantiations. Support proc and fn declarations;
  fn purity requirements still apply independently of its type constraint.
- Ordinary constant-value membership in generic constraints must likewise remain
  a valid constant expression after lowering, preserving section 6's operand
  evaluation rules. Do not turn a compile-time constraint into a runtime call.


7. RUNTIME SWITCH EXPRESSIONS

Syntax:
    result := switch selector {
        case 1: first_expression
        case 2: second_expression
        case:   default_expression
    }

    result := switch {
        case bool_expression_1: first_expression
        case bool_expression_2: second_expression
        case:                   default_expression
    }

- Ordinary Odin switch statements remain unchanged.
- Expression switches support an explicit value selector or an omitted selector.
  The selectorless form behaves as switch true: cases are boolean conditions,
  checked in source order, and the first true condition selects the result.
  Type switches are not a new expression feature in v1.
- Each arm contains exactly one result expression. No switch-targeting break or
  fallthrough is allowed in expression form.
- Require a default unless finite exhaustiveness is actually established. The
  first implementation may require a default for every expression switch.
- Never manufacture a zero-valued fallback for a missing arm.
- Use Odin's ternary typing/assignability rules and surrounding expected type.
  Permit explicit destination types where inference is ambiguous.
- Preserve case matching rules, including supported multi-value/range cases and
  rejection of invalid or duplicate constant cases. A ternary lowering must not
  accidentally accept a switch Odin would reject.
- Evaluate an explicit selector once. Evaluate case matches/boolean conditions
  at most once each, according to switch order, and stop after the first match.
  Apply the same short-circuit order within comma-separated cases. Evaluate only
  the selected result arm; do not eagerly compute later conditions or arms.
- Nested ternaries are a preferred lowering where suitable.
  A selectorless switch can lower directly to condition_1 ? result_1 :
  (condition_2 ? result_2 : default_result), preserving conditional evaluation.
- Support nesting in other expressions. Introduced temporaries must remain in
  the appropriate lazy branch, loop iteration, or other evaluation context.


8. IMMUTABLE STORAGE

Syntax:
    x :> expression
    y: int > rand_int()

- Evaluate the initializer once. It may be a runtime computation.
- Infer or check the declared type using ordinary Odin rules.
- After initialization, reject assignment, compound assignment, increments,
  decrements, and writes to directly stored fields/fixed-array elements.
- Reject obtaining a mutable address of the binding or its directly stored
  subobjects, including implicitly through a generated Thor operation.
- Track actual symbols and scopes, not identifier spelling; ordinary shadowing
  rules still apply.
- This is shallow storage immutability. It does not make data reached through an
  existing pointer/slice/map deeply immutable. Operations that require writing
  the immutable container header or taking its mutable address are rejected.
- Copying a reference-containing value does not create a read-only reference.
- Preserve Odin :: compile-time constants and normal mutable declarations.
- Lower to ordinary Odin storage plus Thor checks. Suitable constant-initialized
  global/static storage may use rodata when semantics are preserved. Do not
  require runtime-initialized locals to live in rodata or become static.
- Pure-function input protection in section 12 is stronger than this rule.


9. SETS

Syntax:
    names: set[string]
    names = set[string]{"apple", "banana"}
    names += "pear"
    names -= "apple"
    names |= other_names

- Use a distinct Thor type backed by an Odin support type containing a map.
- Elements are homogeneous and must satisfy the underlying map-key constraints.
- Duplicate insertion is harmless; removing a missing element is harmless.
- Zero-initialized sets are usable. Allocate lazily on insertion using the
  applicable context allocator, consistent with ordinary Odin maps.
- Support in / not_in, len, element iteration, clear, and delete.
  Iteration order is unspecified. Destroying the table does not recursively
  destroy resources referenced by elements such as separately allocated strings.
- += inserts one element. |= takes another set and performs union; it does not
  insert a scalar element.
- Support union (|), intersection (&), difference (-), symmetric difference (^),
  and logical equality/inequality between sets of the same element type.
- Non-mutating binary set operations produce an independent result table.
  Elements themselves retain Odin's ordinary shallow value-copy behavior.
- Support |=, &=, ^=, and -= for the corresponding in-place set operations.
  -= also removes an individual element when the operand has the element type.
- Handle self-operations and aliased operands correctly. In particular, do not
  invalidate iteration while performing difference/intersection in place.
- Ordinary set assignment follows Odin's reference-containing container copying;
  it is not an implicit deep clone or ownership transfer.
- Newly allocated result tables are caller-managed. Do not automatically delete
  operands, overwrite live storage with hidden cleanup, or infer ownership.
- Preserve ordinary Odin operators on non-set operands.
- Fixed-capacity sets/maps and pure-function set/map support are deferred.


10. NEGATIVE SEQUENCE INDEXING AND SLICING

Examples:
    values[-1]
    values[:-1]

For a signed index i and sequence length n, normalize a negative index to n + i.
The general formula uses addition, not n - i.

- Support ordinary integer-indexed arrays, slices, dynamic arrays, and strings.
  Preserve enumerated-array indexing and pointer/multi-pointer behavior as Odin
  defines it; do not apply sequence normalization to those types.
- Positive indices retain ordinary behavior. Unsigned indices do not take a
  negative-index path or get narrowed merely to support it.
- Never normalize map keys; integer_map[-1] still accesses key -1.
- Evaluate the base and each supplied index/bound once in source order.
- Preserve lvalue behavior for assignment, compound assignment, and address-taking
  where the corresponding Odin access permits them.
- Normalize supplied negative slice bounds independently. Omitted bounds retain
  their normal defaults. Preserve half-open bounds and existing Odin forms.
- Keep normal bounds checking after normalization. No Python-style clamping,
  wrapping, slice step, or special treatment of an excessively negative bound.
- String lengths/indices remain byte-based; this does not add rune/grapheme access.
- Integrate with immutable-storage and pure-input write restrictions.


11. INTERPOLATED STRINGS

Syntax:
    f"value: {expression}"
    f`raw text: {expression}`
    f"count: {number:,}"
    f"count: {number:>12,}"

- f prefixes an ordinary quoted string or Odin raw backtick string. No new r
  prefix. Multiline interpolated-string delimiters are not required in v1.
- Parse actual Thor expressions inside fields, including nested syntax and quoted
  literals. Use parentheses around a ternary inside a field where needed to
  disambiguate its colon from the formatting separator.
- {{ and }} produce literal braces. Diagnose unmatched braces/invalid fields.
- Apply ordinary string escapes in quoted literal portions; retain raw bytes in
  raw literal portions. Escape literal formatting characters in generated calls.
- Evaluate each field once, left to right.
- Default formatting uses Odin-style value formatting. Add integer decimal
  thousands grouping with a comma, including zero and negative signed values.
- Minimum padding syntax is [[fill]align][width][,], with align <, >, or ^,
  optional single-rune fill, nonnegative literal width, and default space fill.
  Width alone right-aligns numeric values and left-aligns strings. For centering,
  put the extra padding rune on the right when the padding count is odd.
- Group digits first, then pad the formatted text, counting signs/separators in
  its width. Match Odin's rune-based width convention, not terminal display cells.
- Examples include >12, <12, ^12, 0>8, and >12,. This is a documented subset of
  Python-style formatting; other Python specifiers are not implicitly supported.
  A comma requests integer grouping and is rejected for unsupported value types.
- Ordinary proc use allocates the final string with context.allocator; the caller
  owns it under normal Odin cleanup rules. No implicit temp-allocator lifetime.
- Release owned intermediate formatting buffers, or write directly into the final
  builder. Never delete borrowed field values merely because they were formatted.
- Pure-function use is limited to approved deterministic formatters; do not invoke
  arbitrary registered formatters or read mutable formatter/context state.


12. PURE FUNCTIONS AND ALLOCATION

Declaration syntax:
    square :: fn(x: int) -> int {
        return x * x
    }

Purity means deterministic results from the input values and permitted constants,
with no externally visible mutation or I/O. Compiler-managed allocation is an
allowed effect; allocation addresses, identity, and allocator callbacks are not
part of the operations available to fn bodies. Allocation failure may fail the
operation; it must not be silently converted into a successful result.

Inputs and operations:
- Allow supported scalar values, enums, fixed arrays, strings, slices, and
  aggregates composed of supported values. Reject raw pointers, arbitrary
  pointer-containing types, handles with effectful operations, any, and runtime
  function pointers. Generic type parameters are permitted when instantiated
  types meet these rules; procedure groups require a resolved eligible target.
- Strings/slices are read-only input sequences. Protect referenced elements
  transitively through copies, fields, subslices, conversions, and calls.
- Do not infer purity from an integer-shaped handle type. Allowed operations and
  call targets establish purity; an unknown foreign/resource operation is rejected.
- Local variables and mutation of fresh local storage are allowed. An explicit
  supported copy of input data can be changed without changing the input.
- No mutable-global reads or writes, I/O, ambient context queries, time/randomness,
  foreign calls, assembly, unsafe casts, pointer extraction/address observation,
  or indirect procedure calls.
- Permit reading genuine compile-time constants. An immutable runtime binding
  does not become a constant merely because it cannot be reassigned.
- A declared fn calls declared fn targets, plus a small explicit whitelist of
  deterministic built-ins and compiler-controlled allocation/copy/format helpers.
  Do not trust an arbitrary Odin procedure based solely on its name/signature.
- The initial whitelist covers len, safe numeric conversions, compile-time
  type/size queries, fresh-buffer creation/copying, and the supported deterministic
  scalar formatters. Add other operations only after verifying their effects.
  Buffer-copy operations may write only to proven locally owned storage.
- Arithmetic, comparisons, len, supported indexing/slicing, local control flow,
  and recursion are allowed subject to these restrictions.
- Apply purity rules to the active call graph, including generic instantiations.
  Recursive call-graph components can be valid; do not reject recursion merely
  because graph traversal encounters a cycle.
- Lower runtime fn calls to Odin procedures with the necessary generated support.
  contextless alone is not evidence of purity.

Allocation and results:
- Allow approved temporary allocation and locally owned mutable buffers inside fn.
  Inputs remain protected. User-supplied allocator callbacks are not allowed.
- Use compiler-managed scratch storage for internal work. Dispose of it on every
  function exit after any returned contents have been preserved.
- Intended result support includes scalar values, strings, slices, and supported
  aggregates. No raw pointers, resources, cyclic graphs, or arbitrary alias graphs.
- String/slice results have value semantics and independent backing storage;
  returning an input view produces an independent result, not an escaping alias.
- Runtime allocation-bearing results are copied out into caller-owned storage
  using the saved caller allocator before scratch storage is released. For nested
  fn calls, the receiving function's scratch allocator may be that caller allocator.
- Apply copying recursively to supported allocation-bearing aggregate members.
  The support library must document cleanup of such results; shallow delete of
  an outer container alone must not be presented as recursive destruction.
- Ordinary proc code still follows manual Odin allocation/cleanup rules.

Explicitly authorized fallback:
- If allocating results makes the initial implementation too complex, retain
  string/slice inputs and internal scratch allocation, but reject result types
  requiring independent allocated storage, including nested members.
- A conservative initial fallback may reject all string/slice result types.
- Functions can still allocate internally and return integers, booleans, enums,
  or supported pointer-free fixed arrays/records.
- Document the selected result subset and reject unsupported results clearly.
  Allocation-bearing returns are an extension goal if this fallback is used,
  rather than a reason to weaken purity or delay all compile-time execution.


13. COMPILE-TIME FUNCTION EXECUTION AND #run

Examples:
    SIZE :: integer_cube_root(27) // declared fn, folded to integer 3
    buffer: [SIZE]int
    VALUE :: #run eligible_proc(known_argument)

- Automatically evaluate a declared fn call when its argument values and required
  types are compile-time known. Calls with runtime inputs remain ordinary calls.
- Compile-time-known inputs include literals, Odin constants, supported constant
  expressions, and earlier evaluated results. Do not require optimizer-style
  propagation through arbitrary mutable runtime control flow.
- For strings/slices, the contents must be known, not just the descriptor or the
  immutability of a binding. Preserve observable distinctions such as nil/empty.
- #run forces compile-time execution of a direct eligible call. All arguments
  must be known. A runtime argument or unsupported operation is a compile error.
- #run accepts declared fn targets and ordinary proc targets whose relevant
  transitive call graph passes the same purity rules. That verification may
  inspect ordinary proc callees without requiring fn spelling on each one.
- Arbitrary side-effecting execution and unverified foreign calls are excluded.
  Never zero globals or otherwise change a procedure to make it qualify.
- Extract the call's dependency closure: functions, required types/constants,
  imports, approved helpers, and relevant configuration.
- Lower that code to a temporary native Odin helper, compile and execute it,
  serialize its result, and substitute a typed constant representation.
- Batch independent ready calls where practical. Resolve dependent evaluations in
  rounds. Distinguish invalid constant-dependency cycles from valid fn recursion.
- Follow selected compile-time branches and lazy evaluation. Do not eagerly run
  a helper for an expression that is not evaluated in the selected path.
- Preserve arbitrary string bytes/escaping, signed/unsigned integer values,
  supported aggregate types, and exact floating-point representations, including
  special values and negative zero. Never embed helper-process memory addresses.
- Serialize before helper storage is released. A helper crash, compilation error,
  invalid result, allocation failure, or timeout is a compilation failure. Use
  bounded execution with a configurable timeout and an actionable basic message.
- Values used in :: declarations, array lengths, #if, or #switch must remain
  valid Odin constants after substitution. Diagnose unsuitable types, such as a
  non-integral array size. Runtime-initialized globals are not an equivalent.
- A cached result represents contents, not a runtime allocation. When folding a
  call in runtime code, preserve its normal type, ownership, and fresh-storage
  semantics. Materialize independent writable results when the result contract
  requires them; do not share one mutable buffer across calls or loop iterations.
- Native execution only in v1. Do not claim host-evaluated results are correct for
  cross targets. Use consistent native configuration for helpers and final code.

Persistent cache:
- Key by Thor/support-library schema version, Odin compiler version, instantiated
  types, argument values, transitive function/type/constant dependencies, and
  relevant defines/compiler/native-target configuration.
- Hash sequence contents recursively, not pointer addresses. Include type and
  nil/empty distinctions that can affect behavior.
- Changes to a transitive callee or referenced constant must invalidate callers.
- Cache only successful complete results. Publish entries atomically; incomplete
  or incompatible entries are misses. Bound cache corruption to a recomputation
  or reported failure, not silent generation of a different value.
- Provide a way to clear/bypass the cache. Helper work directories must not be
  confused with persistent result entries or source files.


14. VALIDATION AND COMPLETION CRITERIA

Each implementation task includes focused compile/run fixtures and rejection
fixtures where relevant. These are future compiler tests, not tests that merely
compare generated text to its own implementation.

Minimum observable checks:
- Ordinary Odin examples still transpile/build, including comments, generics,
  normal operators, directives, mixed source packages, and imported user packages.
- Ordinary when / else when / else statements and compile-time conditional
  expressions work in Thor source. Fixtures must cover coexistence and nesting
  with #if, preserving selected branches and declaration visibility.
- Import aliases stay file-private; duplicate and unsupported selections fail.
- Multiline/nested ternaries compile and produce the expected selected value.
- Literal/member operands with counters execute once, in order; every literal
  element is evaluated before membership, including for an early match.
- Generic constraints accept T in [int, f64] for int/f64 and aliases, reject bool
  and unlisted distinct types, and invert correctly for not_in. Cover empty and
  duplicate type lists, scoped type names, composed constraints, and ordinary
  constant-value membership without runtime helper calls.
- Sets implement duplicate insertion, missing removal, logical equality,
  self/aliased operations, and caller-managed independent result tables.
- Immutable storage rejects direct, field, element, and address-based mutation,
  while same-spelled symbols in separate scopes are handled independently.
- Switch selectors execute once; unselected arms and lazy enclosing branches do
  not execute; nested use and loop reevaluation behave correctly.
- Both selector-based and selectorless runtime/#switch forms work in statement
  and expression positions. Counter-based runtime cases verify first-match and
  once-only condition evaluation, including comma-separated conditions. Compile-
  time cases verify that later fn/#run conditions are not executed after a match.
  Expression default/exhaustiveness rules apply equally to both selector forms.
- Negative reads/writes/compound updates and slice endpoints work; out-of-range
  accesses still fail; negative map keys and unsigned indices remain correct.
- Quoted/raw interpolation, brace escaping, field evaluation order, grouping,
  padding, and owned-intermediate cleanup behave as specified.
- Pure checks reject mutation through input aliases/subslices, mutable-global
  reads, and indirect/impure calls; allow local mutation, recursion, and approved
  allocation. Test the selected result subset and all scratch-exit paths.
- #run and automatic evaluation produce genuine constants usable as array sizes
  and branch conditions. Unknown required arguments fail rather than run later.
- Known sequence arguments are compared/cached by contents; unselected compile-
  time paths do not execute; native helpers serialize values faithfully.
- Warm-cache reuse and invalidation after transitive code/constant/config changes
  are observable. Failed/timed-out helpers never become successful cache entries.
- If allocated results are supported, folded and runtime calls have equivalent
  value/cleanup behavior and do not unexpectedly share writable backing storage.


15. IMPLEMENTATION TASKS: EASIEST TO HARDEST

This is an estimated difficulty ranking, not a strict execution schedule. Shared
infrastructure may be a prerequisite for an easier feature. In particular, the
parser/emitter, project driver, symbol resolution, and expression lowering must
exist before their dependent surface features can be completed. Keep each task's
fixtures with the task rather than postponing validation until the end.

T01. Establish the CLI skeleton and a small compile/run/rejection-fixture runner.
     Add baseline ordinary-Odin cases and explicit output-directory handling.

T02. Implement selective-import list parsing and file-private alias emission.
     Depends on parser/emitter and basic scope/conflict information.

T03. Implement multiline ternary termination and valid Odin emission.
     Depends on tokenizer/parser support; test nested and commented forms.

T04. Implement #if / #else if / #else lowering with Odin when scope behavior.
     Pure-call conditions integrate after compile-time execution exists.

T05. Implement fixed-array literal parsing, contextual/first-element inference,
     and empty/ambiguous-literal rejection without duplicating evaluations.

T06. Implement generic sequence membership helpers and in / not_in lowering.
     Preserve existing map/bit-set behavior and eager literal construction.
     Add constant-expression lowering for type-list and constant-value membership
     in generic constraints, including exact type identity and not_in.

T07. Implement integer grouping and the supported padding helpers independently
     of interpolation syntax. Cover signed minima, Unicode fill, and ownership.

T08. Implement the map-backed Set support type, operations, iteration, and cleanup
     helpers. Verify mathematical results and self/aliased operation behavior.

T09. Adapt the Odin tokenizer/parser and source emitter into a Thor-owned frontend.
     Preserve baseline grammar, comments/spans, and contextual Thor syntax. This
     is foundational work even though its breadth exceeds simple feature rewrites.

T10. Complete project discovery, mixed-source/import handling, generated layout,
     resource paths, and Odin command/configuration forwarding for all CLI modes.

T11. Implement scope-aware symbol and required type/procedure resolution. Retain
     logical Thor types, expected types, and resolved generic/overloaded targets;
     avoid recreating Odin's ordinary type checker where delegation is possible.

T12. Implement immutable-storage declarations and all direct/subobject/address
     mutation checks, integrating generated operations as well as user syntax.

T13. Implement quoted/raw interpolation parsing and formatter lowering, including
     nested expressions, escaping, evaluation order, and intermediate cleanup.

T14. Implement runtime switch expressions and statement/expression #switch forms,
     each with an optional selector. Preserve result typing, case validation,
     once-only selector/condition evaluation, first-match short-circuiting, and
     required exhaustiveness.

T15. Integrate Set types/literals/operators, len/iteration/cleanup, and membership
     into the frontend without changing ordinary Odin operations or copy rules.

T16. Implement type-aware negative indexing/slicing, signed/unsigned distinctions,
     map-key preservation, and writable/addressable accesses.

T17. Complete shared expression lowering for generated temporaries and helper
     calls in lazy branches, nested arguments, loop conditions, and assignments.
     This infrastructure is required to finish several earlier feature tasks.

T18. Implement pure-function eligibility, the explicit built-in/helper whitelist,
     transitive read-only alias tracking, and recursive/generic call-graph checks.
     Reuse the verifier for eligible ordinary proc graphs under #run.

T19. Implement typed compile-time value serialization and reconstruction for the
     supported scalar/sequence/aggregate subset, including exact floats and bytes.

T20. Implement dependency extraction and native Odin helper generation/execution
     for explicit #run, with compilation/runtime failure and timeout handling.

T21. Implement persistent cache keys, transitive invalidation, atomic publication,
     and clear/bypass controls. Validate against code, data, and config changes.

T22. Implement pure-function scratch allocation and cleanup. If feasible, implement
     independent caller-owned sequence/aggregate results and recursive copying;
     otherwise apply the authorized result-type fallback consistently.

T23. Implement automatic known-argument fn evaluation, dependent evaluation rounds,
     constant-context substitution, and selected/lazy-path scheduling. Complete
     runtime materialization of folded owned results if those results are enabled.
     Finish the cross-feature acceptance cases from section 14.
*/


main :: proc() {

    
    fmt.println("Hello, World!")
    
    x := 1
    
}
