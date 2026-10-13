//! Expanding-instantiation check (E3074), run on generic typed IR before
//! monomorphization.
//!
//! Every statically resolved instantiation `callee<…, Uj = A, …>` inside a
//! generic function adds an edge `Ti -> Uj` for each caller parameter `Ti`
//! that occurs in `A`, weighted by how deeply `Ti` is nested in `A`
//! (`Ti` itself is 0, `Wrap<Ti>` is 1). A strongly connected component with
//! a positive internal edge describes a cycle whose type arguments grow on
//! every turn, so specialization would never reach a fixed point. Ordinary
//! and mutually recursive generic functions form zero-weight cycles and are
//! accepted. The design follows Go's `go/types` monomorphization check.
//!
//! Interface calls on a type parameter receiver add no edge: their concrete
//! implementation is unknown until specialization, and approximating it
//! could reject valid programs. Backend type-size limits still bound those.
use crate::*;
use std::collections::HashMap;
use tarn_diagnostics::Diagnostic;
use tarn_resolve::Resolved;
use tarn_types::{ParamId, Typed};

struct Edge {
    from: ParamId,
    to: ParamId,
    weight: u32,
    span: Span,
    caller: FunctionId,
    callee: FunctionId,
    arg: Ty,
}

pub fn check_instantiation_cycles(p: &Program, t: &Typed, r: &Resolved) -> Vec<Diagnostic> {
    let mut edges = Vec::new();
    for f in p.functions.iter().filter(|f| !f.generics.is_empty()) {
        let mut add = |callee: FunctionId, args: &[Ty], span: Span| {
            let Some(target) = p.functions.get(callee.0 as usize) else { return };
            for (to, arg) in target.generics.iter().zip(args) {
                for &from in &f.generics {
                    if let Some(weight) = depth(arg, from) {
                        edges.push(Edge { from, to: *to, weight, span, caller: f.id, callee, arg: arg.clone() });
                    }
                }
            }
        };
        for b in &f.blocks {
            for s in &b.stmts {
                let StatementKind::Assign(_, rv) = &s.kind else { continue };
                rvalue_operands(rv, &mut |o| constant(o, s.span, &mut add));
                match rv {
                    Rvalue::Aggregate(Aggregate::AsyncFrame(id, ts), _) => add(*id, ts, s.span),
                    Rvalue::Aggregate(Aggregate::Closure(id, _), _) => {
                        let ts: Vec<Ty> = p.function(*id).generics.iter().map(|g| Ty::Param(*g)).collect();
                        add(*id, &ts, s.span);
                    }
                    Rvalue::Coerce(CoerceKind::ToDyn(interface), Operand::Copy(place) | Operand::Move(place), _) => {
                        if let Some(Ty::Ref(_, concrete)) = post_drop::place_ty(f, t, place) {
                            for (id, args) in dyn_methods(&concrete, *interface, p, t) {
                                add(id, &args, s.span);
                            }
                        }
                    }
                    _ => {}
                }
            }
            if let Terminator::Call { callee, args, .. } = &b.term {
                let span = b.term_span;
                match callee {
                    Callee::Fn(id, ts) => add(*id, ts, span),
                    Callee::TaskSpawn { worker, drop_result, type_args, .. } => {
                        add(*worker, type_args, span);
                        add(*drop_result, type_args, span);
                    }
                    Callee::Virtual { method, type_args } => {
                        if let Some((id, args)) = args.first().and_then(|o| static_method(f, o, *method, type_args, p, t)) {
                            add(id, &args, span);
                        }
                    }
                    Callee::Value(o) => constant(o, span, &mut add),
                    _ => {}
                }
                for o in args {
                    constant(o, span, &mut add);
                }
            }
            if let Terminator::Switch { discr, .. } = &b.term {
                constant(discr, b.term_span, &mut add);
            }
        }
    }
    report(p, t, r, &edges)
}

fn constant(o: &Operand, span: Span, add: &mut impl FnMut(FunctionId, &[Ty], Span)) {
    if let Operand::Const(Const::Fn(id, ts)) = o {
        add(*id, ts, span);
    }
}

fn rvalue_operands(rv: &Rvalue, visit: &mut impl FnMut(&Operand)) {
    match rv {
        Rvalue::Use(o) | Rvalue::Unary(_, o) | Rvalue::Cast(o, _) | Rvalue::Coerce(_, o, _) => visit(o),
        Rvalue::Binary(_, a, b) => {
            visit(a);
            visit(b);
        }
        Rvalue::Aggregate(_, os) => os.iter().for_each(visit),
        Rvalue::SliceRef { start, end, .. } => start.iter().chain(end).for_each(visit),
        Rvalue::Ref(..) | Rvalue::Discriminant(_) | Rvalue::Len(_) => {}
    }
}

/// An interface call whose receiver already has a concrete implementation
/// target: specialization resolves it exactly as this does.
fn static_method(f: &Function, receiver: &Operand, method: SymbolId, type_args: &[Ty], p: &Program, t: &Typed) -> Option<(FunctionId, Vec<Ty>)> {
    let (Operand::Copy(place) | Operand::Move(place)) = receiver else { return None };
    let ty = match post_drop::place_ty(f, t, place)? {
        Ty::Ref(_, inner) => *inner,
        ty => ty,
    };
    let target = t.decls.impl_target(&ty)?;
    let (interface, index) = t.decls.interface_methods.get(&method).copied()?;
    let symbol = t.decls.implementations.get(&(interface, target))?.get(index)?;
    let mut args = match ty {
        Ty::Adt(_, args) => args,
        _ => Vec::new(),
    };
    args.extend(type_args.iter().cloned());
    Some((*p.by_symbol.get(symbol)?, args))
}

/// The method table a dynamic coercion of a concrete type instantiates.
fn dyn_methods(concrete: &Ty, interface: SymbolId, p: &Program, t: &Typed) -> Vec<(FunctionId, Vec<Ty>)> {
    let Some(target) = t.decls.impl_target(concrete) else { return Vec::new() };
    let args = match concrete {
        Ty::Adt(_, args) => args.clone(),
        _ => Vec::new(),
    };
    let methods = t.decls.implementations.get(&(interface, target)).map(Vec::as_slice).unwrap_or_default();
    methods.iter().filter_map(|s| p.by_symbol.get(s)).map(|id| (*id, args.clone())).collect()
}

/// Deepest nesting of `param` inside `ty`, or `None` if it does not occur.
fn depth(ty: &Ty, param: ParamId) -> Option<u32> {
    let inner = |tys: &mut dyn Iterator<Item = &Ty>| tys.filter_map(|x| depth(x, param)).max().map(|d| d + 1);
    match ty {
        Ty::Param(q) => (*q == param).then_some(0),
        Ty::Adt(_, args) => inner(&mut args.iter()),
        Ty::Ref(_, x) | Ty::Ptr(_, x) | Ty::Array(x, _) | Ty::Slice(x) | Ty::Async(x) => inner(&mut std::iter::once(x.as_ref())),
        Ty::Fn(_, args, ret) => inner(&mut args.iter().chain(std::iter::once(ret.as_ref()))),
        _ => None,
    }
}

/// Tarjan's strongly connected components over parameter nodes.
fn components(edges: &[Edge]) -> HashMap<ParamId, usize> {
    let mut succ: HashMap<ParamId, Vec<ParamId>> = HashMap::new();
    let mut nodes = Vec::new();
    for e in edges {
        for n in [e.from, e.to] {
            if !succ.contains_key(&n) {
                succ.insert(n, Vec::new());
                nodes.push(n);
            }
        }
        succ.get_mut(&e.from).unwrap().push(e.to);
    }
    let mut index: HashMap<ParamId, usize> = HashMap::new();
    let mut low: HashMap<ParamId, usize> = HashMap::new();
    let mut on_stack: HashMap<ParamId, bool> = HashMap::new();
    let mut stack = Vec::new();
    let mut component = HashMap::new();
    let mut next = 0;
    let mut count = 0;
    for &root in &nodes {
        if index.contains_key(&root) {
            continue;
        }
        // Iterative DFS: (node, next successor position).
        let mut work = vec![(root, 0usize)];
        while let Some(&mut (n, ref mut i)) = work.last_mut() {
            if *i == 0 && !index.contains_key(&n) {
                index.insert(n, next);
                low.insert(n, next);
                next += 1;
                stack.push(n);
                on_stack.insert(n, true);
            }
            if let Some(&m) = succ[&n].get(*i) {
                *i += 1;
                if !index.contains_key(&m) {
                    work.push((m, 0));
                } else if on_stack.get(&m) == Some(&true) {
                    let l = low[&n].min(index[&m]);
                    low.insert(n, l);
                }
                continue;
            }
            work.pop();
            if let Some(&(parent, _)) = work.last() {
                let l = low[&parent].min(low[&n]);
                low.insert(parent, l);
            }
            if low[&n] == index[&n] {
                while let Some(m) = stack.pop() {
                    on_stack.insert(m, false);
                    component.insert(m, count);
                    if m == n {
                        break;
                    }
                }
                count += 1;
            }
        }
    }
    component
}

fn report(p: &Program, t: &Typed, r: &Resolved, edges: &[Edge]) -> Vec<Diagnostic> {
    let component = components(edges);
    let mut reported = Vec::new();
    let mut out = Vec::new();
    for (k, e) in edges.iter().enumerate() {
        let c = component[&e.from];
        if e.weight == 0 || component[&e.to] != c || reported.contains(&c) {
            continue;
        }
        reported.push(c);
        // Shortest path back from the growing edge to its start closes the cycle.
        let mut previous: HashMap<ParamId, usize> = HashMap::new();
        let mut queue = std::collections::VecDeque::from([e.to]);
        while let Some(n) = queue.pop_front() {
            if n == e.from {
                break;
            }
            for (j, x) in edges.iter().enumerate() {
                if x.from == n && component[&x.to] == c && x.to != e.to && !previous.contains_key(&x.to) {
                    previous.insert(x.to, j);
                    queue.push_back(x.to);
                }
            }
        }
        let mut cycle = vec![k];
        let mut n = e.from;
        while n != e.to {
            let Some(&j) = previous.get(&n) else { break };
            cycle.push(j);
            n = edges[j].from;
        }
        cycle[1..].reverse();
        let name = |id: FunctionId| p.function(id).name.clone();
        let param = |id: ParamId| t.decls.param_names.get(&id).cloned().unwrap_or_else(|| "_".into());
        let mut d = Diagnostic::error("E3074", "expanding_instantiation", format!("recursive generic calls through `{}` grow a type argument without bound", name(e.caller)))
            .primary(e.span, format!("`{}` becomes `{}` here", param(e.from), t.display(&e.arg, r)));
        for &j in &cycle[1..] {
            let x = &edges[j];
            d = d.secondary(x.span, format!("`{}` passes `{}` to `{}`", name(x.caller), t.display(&x.arg, r), name(x.callee)));
        }
        out.push(
            d.note(format!("each turn of the recursion wraps `{}` again, so compiling it would need infinitely many copies of `{}`", param(e.from), name(e.callee)))
                .help("pass the growing value behind a non-generic type such as `any Interface`, or keep the type argument unchanged"),
        );
    }
    out
}
