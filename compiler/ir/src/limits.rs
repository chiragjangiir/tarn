//! Native value limits (E3075), reported at the source binding before code
//! generation. Uses the same layout authority as the backend, so a program
//! that checks cleanly never meets these limits later as a backend error.
//!
//! Only concrete local types are inspected: a generic local's size is known
//! after specialization, where the backend still enforces the same limits.
use crate::layout::{self, MAX_ARRAY_LEN, MAX_NESTING, MAX_VALUE_SIZE};
use crate::*;
use tarn_diagnostics::Diagnostic;
use tarn_resolve::Resolved;
use tarn_types::Typed;

pub fn check_value_limits(p: &Program, t: &Typed, r: &Resolved) -> Vec<Diagnostic> {
    let mut out = Vec::new();
    let mut reported: Vec<Ty> = Vec::new();
    // Named bindings first: they point at the declaration the user wrote.
    let locals = p.functions.iter().flat_map(|f| &f.locals);
    let (named, unnamed): (Vec<_>, Vec<_>) = locals.partition(|l| l.name.is_some());
    for local in named.into_iter().chain(unnamed) {
        if !concrete(&local.ty) || reported.contains(&local.ty) {
            continue;
        }
        let Err(e) = layout::layout(t, &local.ty) else { continue };
        if !e.limit {
            continue;
        }
        reported.push(local.ty.clone());
        let ty = t.display(&local.ty, r);
        let what = match &local.name {
            Some(name) => format!("`{name}` of type `{ty}`"),
            None => format!("a value of type `{ty}`"),
        };
        out.push(
            Diagnostic::error("E3075", "value_too_large", format!("{what} is too large to store as a native value"))
                .primary(local.span, e.message)
                .note(format!("values are limited to {} KiB, fixed arrays to {MAX_ARRAY_LEN} elements and nesting to {MAX_NESTING} levels", MAX_VALUE_SIZE / 1024))
                .help("keep large data on the heap, for example in a `Vec<T>`, and pass it by reference"),
        );
    }
    out
}

fn concrete(ty: &Ty) -> bool {
    match ty {
        Ty::Param(_) | Ty::Var(_) | Ty::Opaque | Ty::Error => false,
        Ty::Adt(_, args) => args.iter().all(concrete),
        Ty::Array(x, _) => concrete(x),
        // Behind a pointer only the pointer is stored.
        Ty::Ref(..) | Ty::Ptr(..) | Ty::Fn(..) | Ty::Async(_) => true,
        Ty::Slice(_) | Ty::Any(_) => false,
        _ => true,
    }
}
