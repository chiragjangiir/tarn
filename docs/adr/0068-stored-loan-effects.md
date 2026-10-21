# ADR 0068: stored-loan effects in function summaries

Status: accepted. Extends ADR 0025 (borrow checking) and ADR 0010 (inferred
provenance). Fixes a memory-safety hole; no syntax change.

## Evidence

A scoped-session probe and its reduction to ordinary safe Tarn (no FFI, no
`unsafe`) showed the borrow checker accepting programs that read through a
reference to a local whose frame has ended:

```tarn
fn store(slot &mut Option<&Session>, s &Session) { *slot = Some(s) }
fn scoped(slot &mut Option<&Session>) {
    local := Session{value: 42}
    store(slot, &local)            // accepted: `local` escapes into the caller
}
```

Writing `*slot = Some(&local)` directly in `scoped` was already E4207. The same
store through a helper, a nested helper, `out.push(&local)` on a `&mut Vec<&T>`
parameter, or a callback that saves its parameter into a captured variable
(`scoped(fn(s &Session) { saved = Some(s) })`) was accepted.

Cause: function summaries recorded only which parameters the **result** may
borrow (ADR 0010). Nothing recorded that a function **stores** one parameter's
loans behind another mutable parameter. `store_effects` modelled stores only
into the caller's own locals, and skipped parameter placeholder loans, so a
store into caller-owned memory was invisible at every call site. A closure body
is an ordinary function whose captures are its leading parameters, so a
callback saving its argument into a capture is the same missing effect.

## Decision

### Store summaries

Each function body gets a second inferred summary next to its provenance:

```text
stores: set of (src, dst) parameter positions
        "loans of argument src may be kept behind argument dst"
```

`dst` is a mutable reference parameter whose referent can hold references.
Pairs are found where a value flows into memory behind such a parameter:

- an assignment through a dereference of a place that holds `dst`'s
  placeholder loan (directly or through a reborrow), with `src`'s placeholder
  in the assigned value;
- a call whose callee may store one argument behind another (below), when the
  destination argument holds `dst`'s placeholder.

Summaries are computed after the provenance fixpoint, which they do not
change, and reach their own least fixpoint over the call graph (a function is
re-analysed when a callee's summary grows). Recursion converges because the
pair set is finite and only grows.

### Call sites

A callee's pairs come from its summary when it is a function with a body.
Callees without a body that can be inspected — intrinsics, interface dispatch,
callable values, extern and opaque declarations — are conservative: any
argument may be stored behind any other argument whose referent can hold
references. Builtins and task spawning store nothing, as before.

At each pair, the loans of argument `src` are examined against the storage
behind argument `dst`:

- a loan of a local of the calling function, stored behind a parameter of the
  calling function, escapes the frame: **E4207**, the same error as the direct
  `*slot = Some(&local)` assignment, now reported at the call;
- a parameter placeholder becomes a pair of the caller's own summary, so the
  effect propagates outward through any number of helpers.

Stores into the caller's own locals keep the existing conservative handling
(`store_effects` unions argument loans into mutably borrowed locals), so their
lifetime is still checked by ordinary liveness and E4105.

### Callable parameters are borrowed for one invocation

A parameter of a closure is valid only during that call: the invoking code
may pass a reference to its own local (`body(&local)`), and nothing at the
creation site can see that local. Therefore a closure body whose summary
stores a call parameter (position `≥ captures.len()`) behind a capture
(position `< captures.len()`) is rejected with **E4210**
`callable_parameter_escapes`. This applies to borrowed and `move` closures.

Reading, copying or cloning data from the parameter, and storing loans the
closure captured itself, remain allowed.

## Alternatives considered

- **Reject every `mut fn(&T)` invocation with a local loan.** Sound and local,
  but it rejects ordinary callbacks such as `each(items, fn(x &T) { total =
  total + x.value })`. The callable-parameter rule keeps them.
- **Track loans stored through dereferences in `write_holds`.** Writes through
  `*p` reach memory the function does not own; the loan sets of the caller's
  locals are what must change, which requires a cross-call summary anyway.
- **Explicit lifetime or `stores(...)` annotations.** Contradicts the v0
  direction of inferred provenance (AGENTS.md); inference suffices here.
- **Runtime liveness checks.** Rejected: safety stays a compile-time property.

## Consequences

- Programs that relied on the hole are now rejected; the reproductions are
  regression tests. The full compiler suite is the compatibility measure.
- Conservative pairs for unknown callees may reject a valid program that
  passes both a mutable container parameter and a local reference to an
  intrinsic or callable. The diagnostic names the call and the parameter; the
  fix is an owned value or a container that lives in the calling frame.
- Interface methods are conservative because their implementations are not
  known at the call. A future refinement could summarize declared interfaces.
- No syntax, backend or runtime change. The backend still never decides
  borrow semantics.

## Diagnostics

- E4207 `borrow_escapes_through_reference` (existing meaning): also reported at
  a call that may store a reference to a local behind a caller parameter.
- E4210 `callable_parameter_escapes` (new): a closure stores one of its call
  parameters into captured state.
