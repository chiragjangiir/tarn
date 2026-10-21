# ADR 0074: containment queries are inductive on recursive types

Status: accepted. Amends ADR 0033 (structural capability queries). Fixes a
false rejection; no syntax change.

## Evidence

While porting the launcher, Codex hit this reduction: a callback returning
`Result<void, Failure>`, where `Failure { details Details }` and
`enum Details { Empty, Nested(Vec<Details>) }`. It was rejected with E4201
(returning a reference to `body`) and E4102 (the caller's owner still
borrowed), although the type holds no reference. Replacing `Details` with a
non-recursive type (`i32`, `Vec<i32>`, a non-recursive enum, `Vec<Vec<i32>>`)
compiled. A launcher that keeps an owned `json.Value` error from a callback
was blocked by the same thing.

## Cause

`Decls::may_contain_references` and `contains_resource` (which also backs the
guard, task and waker queries) answered **true** on revisiting a type already
on the query path ("cycles conservatively retain loans"). Every recursive type
therefore counted as possibly holding references, and the borrow checker
retained the callback's call loans in its result.

## Decision

Containment is existential: "is a reference, guard, task or waker reachable
from this type?". Revisiting the identical substituted type on the current
path reaches nothing new, so the least fixed point answers **false** there.
A reference that is really present is still found through a non-recursive
branch: `Tree<&Owner>` finds `&Owner` in `Leaf(T)`.

This is the exact dual of ADR 0033's coinductive Transfer/Share (a repeated
universal query holds). Exhausted depth (64) or budget (4096) still answers
true, so growing generic instantiations that never repeat exactly stay
conservative.

## Tests

- `tests/borrows/pass/recursive_owned_callback_result.tarn`: Codex's shape,
  which now compiles.
- `tests/borrows/fail/E4102_recursive_result_keeps_reference.tarn`: a
  recursive type instantiated with a reference still keeps its loan.
