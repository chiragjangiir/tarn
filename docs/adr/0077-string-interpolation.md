# ADR 0077: Checked string interpolation (`f"..."`) and `core.Display`

Status: accepted, implemented. Moves `string.Builder` to `core` (ADR 0042,
0048). No change to coherence (ADR 0016). Supersedes nothing.

## Evidence

Building a message from a number today takes:

```tarn
s := try string.format("n={}", &[1]string{string.from_i64(n)})
```

Every argument is converted by hand, packed into an array, and the call
returns `Result` because the template is only validated at run time. The
alternative is a `string.Builder` with one `push_*` call per piece.

The equivalent is one line in Go (`fmt.Sprintf("n=%d", n)`) and in Rust
(`format!("n={n}")`). AGENTS.md treats extra ceremony relative to Go in
common application code as a design problem. An external agent evaluating
Tarn ranked formatting among the two most painful daily gaps.

Measured use in this repository is still small (2 `string.format`, 10
`string.from_i64`/`from_u64`, 12 `Builder.new()` across stdlib, examples and
tests), because little application code exists yet. This ADR relies on the
cross-language comparison more than on measured pain; that is a known
weakness, recorded here deliberately.

## Decision

### Syntax: prefixed literals

`f"..."` is an interpolated string literal. Plain `"..."` literals are
unchanged; `{` and `}` keep no meaning in them, so existing code (notably
`stdlib/json`, which writes `"{"` literally) does not change behavior.

Inside `f"..."`:

- `{name}` and `{name.field.field}` interpolate a value;
- `{{` and `}}` are literal braces (the same escapes as `string.format`);
- anything else between braces is a parse error.

Arbitrary expressions (`{a + b}`, `{f(x)}`) are rejected. Rust's inline
format arguments make the same restriction. Calls inside text hide effects
and moves; widening later is compatible, narrowing is not.

### Formatting capability: `core.Display`

```tarn
pub interface Display {
    fn display(&self, out &mut Builder)
}
```

- `core` implements it for every integer type, `bool` and `string`. `f64`
  is not `Display` in this version (see Implementation notes).
- User types implement it by hand. There is no `derive` (consistent with
  `Eq`, ADR 0054) and no separate debug formatting.
- An interpolated value whose type does not implement `Display` is a new
  type error (code assigned at implementation), naming the type and
  suggesting `impl Display for T`.

### Writing into a builder, not returning a string

`display` appends to a builder instead of returning an owned `string`. A
nested value (a struct displaying its fields) then allocates once, not once
per field. This matches `json.Encode` (`fn encode(&self, out &mut Writer)`)
and Rust's `Display::fmt`.

### `Builder` moves to `core`

Only `core` may implement interfaces for primitives (ADR 0016, enforced in
resolution). `Display` must therefore live in `core`, and so must the type
its method writes into. `Builder` is plain Tarn over `Vec<u8>` and
`string_from_utf8`, both already in `core`.

Tarn has no re-exports, so `string.Builder` becomes `Builder` (core prelude).
The 12 in-repository uses are migrated mechanically in the same change.

### Lowering and ownership

`f"a{x}b"` is resolved and type checked as an expression of type `string`,
then lowered in typed IR to:

```text
b := Builder.new()
b.push("a")
Display.display(&x, &mut b)
b.push("b")
result := b.finish()
```

- Interpolated values are only **borrowed**, never moved; ordinary loan and
  move checking applies to the generated borrows, with spans pointing at
  each `{...}`.
- The result is a new owned `string`.
- `Display` is located through `core` declarations, like `Eq` for `==`; no
  module name is hardcoded in later phases.
- A literal with no interpolation (`f"abc"`) is accepted and is equivalent to
  `"abc"`.

### Out of scope

Width, precision, alignment, radix (hex) and padding directives. Runtime
templates keep using `string.format`, which is unchanged.

## Alternatives considered

- **Interpolate every literal.** Simplest to type, but silently changes the
  meaning of existing literals containing `{`. Rejected.
- **Checked `format("n={}", n)` function.** Needs variadic functions or
  macros, neither of which Tarn has. Inventing either is a larger change than
  a literal prefix. Rejected.
- **`fn display(&self) string`.** Simpler to implement, but allocates per
  value and per nested field. Rejected in favor of the builder sink.
- **Let the module declaring an interface implement it for primitives**
  (Rust's orphan rule), keeping `Display` and `Builder` in `string`. Sound,
  but loosens ADR 0016, and the lowering would then depend on an ordinary
  stdlib module being loaded. Rejected for now; it remains the alternative if
  moving `Builder` proves costly.
- **Compiler-known `push_i64`/`push_bool` calls for primitives.** Grows the
  list of magic names the compiler knows, which AGENTS.md forbids. Rejected.

## Risks and debt

- `Builder` enters the global prelude; a user type named `Builder` shadows
  it locally like any other prelude name.
- The prefix letter `f` is a lexer special case before `"`; an identifier
  `f` followed by a string literal without a space was previously a parse
  error, so no valid program changes meaning.
- Without directives, hex dumps and aligned tables still need manual code.

## Future evidence that would change this

- Real code repeatedly needing `{f(x)}` or `{a + b}`: widen the grammar.
- Repeated padding/radix code in applications: add directives.
- Hand-written `Display` impls that only print fields in order: consider an
  explicit per-type opt-in helper, as ADR 0054 does for `Eq`.

## Verification plan

- Parser: valid forms, `{{`/`}}`, rejected expressions, unclosed braces.
- Types: missing `Display` diagnostic; primitives, `string`, user impls.
- Moves/borrows: interpolating a moved value is E4001; interpolating
  while mutably borrowed conflicts; values remain usable afterwards.
- Native: output of every primitive, nested user `Display`, empty literal.
- Migration: all `string.Builder` uses compile as `Builder`.

## Implementation notes

- **Lexing.** `f"` lexes as one `FStr` token whose pieces are cooked text and
  dotted name paths with spans. Malformed holes are E0006; recovery balances
  nested braces so one bad hole yields one diagnostic. `f "x"` (with a space)
  is unchanged.
- **AST and tables.** `ExprKind::Interpolated` holds text pieces and ordinary
  `Ident`/`Field` expressions, so resolution, capture analysis and borrow
  checking treat them like any other borrowed operand. The checker records
  each value's `Display.display` method in `TypeTables::interpolations`;
  lowering consumes it and emits a `Callee::Virtual` call that
  specialization resolves, exactly as `==` does with `Eq`.
- **Integer literals** still being inferred are accepted: every integer type
  is `Display`, so interpolation never forces a default.
- **`f64` is not `Display`.** Float formatting needs the `tarn_format_f64`
  native bridge, which lives behind `ffi` in `string`; `core` cannot import
  `ffi`. E3076 suggests `string.from_f64`. Moving the bridge needs its own
  decision about native authority in `core`.
- **Builder API changes.** `push_f64` stays in `string` as the free function
  `string.push_f64(&mut builder, value)`. `push_range(text, range)` became
  `push_range(text, start, end)`, because `Range` belongs to `string`; it
  checks scalar boundaries itself and aborts on an invalid range.
- **Generic user types** cannot yet display type-parameter fields: impls
  take no bounds (ADR 0054), so `impl Display for Pair<A, B>` cannot call
  `display` on `A`. Generic functions with `<T: Display>` work.
- **Tooling.** The formatter prints `f"..."` tokens verbatim. The VS Code
  grammar does not highlight holes yet.
