# ADR 0065: reject expanding generic instantiation before specialization

Status: accepted. Replaces the backend's fixed instance cap.

## Problem

Backend specialization rejected every program reaching more than 256 function
instances. That counted ordinary functions, closures, generic instances and the
stdlib they use, so a small HTTP server already sat at 254/256. Applications
were reshaped (one dispatching middleware instead of one closure per route) to
avoid a compiler-internal number. The cap only existed to stop polymorphic
recursion such as `grow<T>` calling `grow<Wrap<T>>`, which never reaches a
fixed point.

## Decision

A check over generic typed IR, run in the driver right after lowering, builds
a graph whose nodes are generic parameters. Each statically resolved
instantiation (direct calls, function values, closures, async frames, task
workers, interface calls on concrete receivers and dynamic tables of concrete
types) adds an edge from every caller parameter occurring in a type argument
to the callee parameter, weighted by its nesting depth (`T` 0, `Wrap<T>` 1).
A strongly connected component containing a positive edge grows without bound
and is rejected as E3074 with the cycle's call sites. Zero-weight recursion,
including mutual recursion, is accepted.

Specialization no longer counts instances. It indexes them by hash instead of
linear search, keeps the existing per-argument type-size limit (64 levels or
4096 nodes), and keeps a 2^20-instance cap reported as a compiler bug, purely
as a memory guard.

## Alternatives

- Raise the cap: still arbitrary, still a late backend error with no source span.
- Rust's approach (type length/recursion limits only): detects late and reports
  the symptom, not the cycle.
- Go's approach (`go/types` mono check): static, precise, early. Chosen; Tarn
  only needs SCC detection because all weights are non-negative.

## Limits

Interface calls on a type-parameter receiver add no edge, because the concrete
implementation is unknown until specialization; approximating every impl could
reject valid programs. An expansion that passes only through such calls is
still stopped by the backend type-size limit, with a less precise message.
Future evidence of such cycles in real code could justify modelling them.
