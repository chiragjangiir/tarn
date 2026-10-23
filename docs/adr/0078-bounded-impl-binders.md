# ADR 0078: Bounds on `impl` binders (conditional implementations)

Status: accepted. Extends ADR 0015 and ADR 0016 without changing coherence.

## Evidence

Three interfaces are blocked by the same limitation: an implementation for
a generic type cannot require anything of its type arguments.

- **`Eq`.** ADR 0054 records that `Option<T>` and `Result<T, E>` cannot
  implement `Eq`, because the impl would need `T: Eq`. Code compares them
  with `match` or `unwrap_or`.
- **`Display`** (ADR 0077). `impl Display for Pair<A, B>` cannot display
  its fields, because nothing says `A` implements `Display`.
- **`Hash`.** A generic container cannot become a `collections.Map` key for
  the same reason.

Generic functions already accept bounds (`fn show<T: Display>(...)`), so
the gap is specific to implementations. It is not speculative: the stdlib's
own core types are affected.

## Decision

### Syntax

Binders in an `impl` target may carry bounds, using the syntax that type
and function declarations already use:

```tarn
impl Eq for Option<T: Eq> {
    fn eq(&self, other &Self) bool { ... }
}

impl Display for Pair<A: Display, B: Display> {
    fn display(&self, out &mut Builder) {
        out.push(&f"({self.first}, {self.second})")
    }
}
```

Bounds on method owners (`fn Pair<A: Eq, B>.m`, E2021) stay rejected:
conditional inherent methods are a separate question with no evidence yet.

### Meaning

`impl I for T<A: B>` declares that `T<X>` implements `I` **exactly when**
`X` satisfies `B`. Inside the impl, `A` has bound `B`, so its methods and
its use in `f"..."`, `==` and calls to generic functions type-check like
any bounded type parameter.

A type's own declaration bounds (`struct Map<K: Hash, V>`) still hold
inside the impl without being repeated.

### Coherence is unchanged

ADR 0016 guarantees at most one `impl I for T` per program: it lives in the
module of `I` or of `T`, and E2020 rejects a second one. Bounds do not add
impls; they narrow which instances of the single impl apply. There is still
no specialization (E2022), no blanket `impl<T: A> B for T`, no negative
bounds, and no overlap analysis. Every program valid today stays valid and
keeps its meaning.

### Checking

- **Where an implementation is required** (a bound on a call, `==`, `f"{x}"`,
  a coercion to `&any I`), the checker proves `T<X>: I` by finding the impl
  and then proving each binder bound for the actual argument.
- The proof recurses on the arguments. Types are finite, so recursion is
  bounded by type size; the existing depth budget stays as a backstop and
  stays conservative when exhausted (ADR 0033).
- Inside generic code, an argument that is a type parameter satisfies a
  bound only through its own declared bounds, as today.
- **A new diagnostic** (code assigned at implementation) reports the failure;
  its shape is specified under Diagnostics.

### Diagnostics

A failed proof must point at the cause, not only at the outer type.

- **The proof returns why it failed.** `implements` becomes a query that, on
  failure, returns the chain of steps: at each level, the impl used, the
  bound it requires and the argument that did not satisfy it. Every consumer
  (`==`, `f"..."`, bounded calls, coercion to `&any I`) reports from that
  chain; none rebuilds it.
- **Root cause first.** The headline names the innermost type and interface
  that failed (`` `Point` does not implement `Eq` ``). The primary label is
  the user's code that required the interface; a secondary label points at
  the exact impl bound, even in another module or in the stdlib.
- **Bounded chain.** One note lists the path, one step per level. Chains
  longer than three steps elide the middle (`... 2 more`), so the message
  stays a few lines long at any depth.
- **Help that coherence allows.** The suggestion depends on the root cause:
  for a type declared in the user's module, implement the interface; for a
  primitive or a type from another module, wrap it in a local struct (ADR
  0016); for a type parameter, add the bound to the enclosing generic
  function. Never suggest an impl that coherence would reject.
- **All distinct root causes.** When several arguments fail
  (`Pair<Point, Color>`), each distinct root cause is reported, up to three,
  so that fixes do not have to be discovered one recompilation at a time.
- **Machine-readable chain.** `tarn check --json` carries the chain as
  structured data (type, interface, impl, bound span per step), not only as
  note text, so an agent can go directly to the missing impl.

Example:

```text
error: `Point` does not implement `Eq`
  --> main.tarn:12:8
   |
12 |     if a == b {
   |        ^^^^^^ `==` on `Vec<Pair<Option<Point>, i32>>` requires `Eq`
  ::: core.tarn
   |
   | impl Eq for Option<T: Eq> {
   |                       -- required by this bound
   = note: `Vec<...>: Eq` needs `Pair<Option<Point>, i32>: Eq`
           needs `Option<Point>: Eq` needs `Point: Eq`
   = help: implement it: `impl Eq for Point { fn eq(&self, other &Self) bool { ... } }`
```

### Lowering, specialization and dynamic dispatch

No change. Specialization already resolves an interface call by the
receiver's concrete type and its type arguments, so a bounded impl
specializes like an unbounded one. Coercion to `&any I` happens on a type
already proven to implement `I`, and each vtable already belongs to one
concrete `(type, interface)` pair. The backend learns nothing new.

### Stdlib follow-up in the same phase

- `impl Eq for Option<T: Eq>` and `impl Eq for Result<T: Eq, E: Eq>`,
  removing the ADR 0054 limitation.
- `impl Hash for Option<T: Hash>`, for symmetry with `Eq`.
- No `Display` for `Option` or `Result`: how to render `None` or an error is
  an application decision, not a library one.

## Alternatives considered

- **Leave impls unbounded** and require wrapper types. This is ADR 0016's
  stated idiom, but the stdlib cannot wrap its own `Option`; the cost is
  already visible.
- **Bounds on the interface method** (`fn eq(&self, other &Self) bool
  where T: Eq`). It needs `where` clauses and per-method conditions. More
  syntax for the same expressiveness. Rejected.
- **Blanket impls** (`impl<T: Display> Debug for T`). Much more powerful,
  but it requires overlap checking and breaks the purely local uniqueness
  argument of ADR 0016. Out of scope.
- **Structural or derived equality.** Rejected by ADR 0022 and ADR 0054;
  this ADR does not revisit them.

## Risks and debt

- **Diagnostics.** A failure can sit several levels deep
  (`Vec<Pair<Option<Point>, i32>>`). Mitigated by the Diagnostics rules
  above; the golden tests below guard them against regressions.
- **Implicit requirements.** A bound unrelated to a field
  (`impl Eq for Pair<A: Eq, B: Hash>`) is legal and only narrows the impl.
  This is accepted; a lint could flag it later.
- **Capability bounds.** `Copy`, `Transfer` and `Share` remain capabilities
  computed by the compiler. They may appear as impl bounds, but this ADR
  does not make them implementable.

## Future evidence that would change this

- Repeated wrapper types written only to satisfy a bound on a foreign
  generic type: evaluate package-level coherence (ADR 0016) first.
- Repeated conditional inherent methods: revisit E2021 with its own ADR.

## Verification plan

- Parser: bounds in impl target arguments; bounds rejected in specialized
  positions (`Pair<i32, B: Eq>` stays E2022).
- Types: satisfied and failing bounds, nested failures that report the
  innermost cause, bounds proven from a generic function's own bounds, and
  struct bounds implied inside the impl.
- Diagnostics (golden): a chain of three or more levels with elision; a
  root cause in a primitive (wrap suggestion); a root cause in a type
  parameter (add-bound suggestion); two distinct root causes in one error;
  the JSON chain.
- Native: `==` on `Option<i32>` and on `Option<Point>`, `f"{pair}"` with
  user types, and a `&any Display` built from `&Pair<...>`.
- Regression: every existing impl and the E2020/E2022/E2023 suites unchanged.

## Implementation notes

- **Parsing.** `ImplDecl.binder_bounds` holds the bounds of each target type
  argument, in order; the target itself stays an ordinary path type, so
  resolution (E2022, E2024, E2025) is unchanged. Owner bounds stay E2021.
- **Declarations.** Each impl's bounds become the binder's bounds inside the
  impl and an entry in `Decls::impl_conditions`, keyed by
  `(interface, target)`: argument index, required interface, bound span.
- **Proof.** `implements` is now "has an impl, and every condition holds for
  the actual arguments, recursively". The checker's `unmet` returns one chain
  per failure; integer and float literals still being inferred are proven as
  their default types. The 64-level guard reports a failure, conservatively.
- **Reporting.** `==`, `f"..."`, bounded calls (previously E3022) and
  coercion to `&any I` report E3077 when an impl exists but its conditions
  fail; without any impl they keep their previous diagnostics. The chain is
  also emitted as `obligations` in `--json` output.
- **No backend change.** Specialization and vtables needed nothing new.
