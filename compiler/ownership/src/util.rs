//! Small shared helpers: a bit set and source-like names for places.

use std::collections::HashMap;
use tarn_ir::{Function, LocalKind, Place, Proj};
use tarn_types::{Ty, Typed};

/// The first 64 bits live inline: dataflow states clone many sets, and most
/// functions have fewer than 64 loans, so cloning never allocates. Sets of
/// different capacities interoperate; `EMPTY` is a valid empty set of any size.
#[derive(Clone, Debug, Default)]
pub struct BitSet {
    first: u64,
    rest: Vec<u64>,
}

impl PartialEq for BitSet {
    fn eq(&self, other: &BitSet) -> bool {
        let word = |s: &BitSet, i: usize| s.rest.get(i).copied().unwrap_or(0);
        self.first == other.first && (0..self.rest.len().max(other.rest.len())).all(|i| word(self, i) == word(other, i))
    }
}

impl Eq for BitSet {}

impl BitSet {
    pub const EMPTY: BitSet = BitSet { first: 0, rest: Vec::new() };

    pub fn new(n: usize) -> BitSet {
        BitSet { first: 0, rest: vec![0; n.div_ceil(64).saturating_sub(1)] }
    }

    fn word(&mut self, w: usize) -> &mut u64 {
        if w == 0 {
            return &mut self.first;
        }
        if self.rest.len() < w {
            self.rest.resize(w, 0);
        }
        &mut self.rest[w - 1]
    }

    pub fn insert(&mut self, i: usize) -> bool {
        let b = 1u64 << (i % 64);
        let word = self.word(i / 64);
        let was = *word & b != 0;
        *word |= b;
        !was
    }

    pub fn remove(&mut self, i: usize) {
        if i < 64 {
            self.first &= !(1u64 << i);
        } else if let Some(w) = self.rest.get_mut(i / 64 - 1) {
            *w &= !(1u64 << (i % 64));
        }
    }

    pub fn contains(&self, i: usize) -> bool {
        let w = if i < 64 { self.first } else { self.rest.get(i / 64 - 1).copied().unwrap_or(0) };
        w & (1u64 << (i % 64)) != 0
    }

    pub fn is_empty(&self) -> bool {
        self.first == 0 && self.rest.iter().all(|w| *w == 0)
    }

    /// `self |= other`; returns whether anything changed.
    pub fn union(&mut self, other: &BitSet) -> bool {
        let mut changed = self.first | other.first != self.first;
        self.first |= other.first;
        if self.rest.len() < other.rest.len() {
            self.rest.resize(other.rest.len(), 0);
        }
        for (a, b) in self.rest.iter_mut().zip(&other.rest) {
            let n = *a | *b;
            changed |= n != *a;
            *a = n;
        }
        changed
    }

    pub fn iter(&self) -> impl Iterator<Item = usize> + '_ {
        std::iter::once(&self.first).chain(&self.rest).enumerate().filter(|(_, w)| **w != 0).flat_map(|(wi, w)| (0..64).filter(move |b| w & (1 << b) != 0).map(move |b| wi * 64 + b))
    }
}

/// Source-like name of a place: `x`, `p.first`, `r.name`, `arr[..]`.
/// Dereferences are not shown: Tarn has no `*` operator, the user wrote
/// `r.name` and auto-deref did the rest.
pub fn place_name(f: &Function, t: &Typed, p: &Place) -> String {
    let l = f.local(p.local);
    let mut name = match l.kind {
        LocalKind::TaskScopeWitness => "the task scope".into(),
        LocalKind::User | LocalKind::Param => l.name.clone().unwrap_or_else(|| "value".into()),
        _ => "a temporary".into(),
    };
    let mut ty = l.ty.clone();
    let mut variant: Option<(Vec<Ty>, String)> = None;
    for proj in &p.proj {
        match proj {
            Proj::Deref => {
                ty = match ty {
                    Ty::Ref(_, inner) => *inner,
                    other => other,
                };
            }
            Proj::Index(_) => {
                name = format!("{name}[..]");
                ty = match ty {
                    Ty::Array(e, _) | Ty::Slice(e) => *e,
                    other => other,
                };
            }
            Proj::Downcast(v) => {
                if let Ty::Adt(e, args) = &ty
                    && let Some(def) = t.decls.enums.get(e)
                {
                    let map: HashMap<_, _> = def.generics.iter().copied().zip(args.iter().cloned()).collect();
                    if let Some(var) = def.variants.get(*v as usize) {
                        variant = Some((var.fields.iter().map(|f| tarn_types::subst(f, &map)).collect(), var.name.clone()));
                        name = format!("{name}.{}", var.name);
                    }
                }
            }
            Proj::Field(i) => {
                if let Some((fields, _)) = variant.take() {
                    name = format!("{name}.{i}");
                    ty = fields.get(*i as usize).cloned().unwrap_or(Ty::Error);
                } else if let Ty::Adt(s, args) = &ty
                    && let Some(def) = t.decls.structs.get(s)
                {
                    let map: HashMap<_, _> = def.generics.iter().copied().zip(args.iter().cloned()).collect();
                    let fld = &def.fields[*i as usize];
                    name = format!("{name}.{}", fld.name);
                    ty = tarn_types::subst(&fld.ty, &map);
                } else {
                    name = format!("{name}.{i}");
                }
            }
        }
    }
    name
}

/// Can a value of this type hold a reference (and therefore loans)?
/// Conservative for type parameters, functions/closures and opaque recovery
/// values. Unmodeled std APIs are rejected before IR (E3040).
pub fn may_hold_refs(t: &Ty) -> bool {
    match t {
        Ty::Ref(..) | Ty::Fn(..) | Ty::Param(_) | Ty::Any(_) | Ty::Opaque => true,
        Ty::Adt(_, args) => args.iter().any(may_hold_refs),
        Ty::Array(e, _) | Ty::Slice(e) => may_hold_refs(e),
        _ => false,
    }
}

/// Each block's reverse-postorder rank from the entry; unreached blocks last.
pub fn reverse_postorder(f: &Function) -> Vec<usize> {
    let n = f.blocks.len();
    let mut seen = vec![false; n];
    let mut postorder = Vec::with_capacity(n);
    let mut stack = vec![(0usize, 0usize)];
    seen[0] = true;
    while let Some((b, i)) = stack.pop() {
        let next = f.blocks[b].term.successors();
        if let Some(s) = next.get(i) {
            stack.push((b, i + 1));
            let s = s.0 as usize;
            if !seen[s] {
                seen[s] = true;
                stack.push((s, 0));
            }
        } else {
            postorder.push(b);
        }
    }
    let mut rank: Vec<usize> = (n..2 * n).collect();
    for (i, b) in postorder.iter().rev().enumerate() {
        rank[*b] = i;
    }
    rank
}

/// Map `items` on all cores, returning results in input order, so output is
/// identical to a sequential map. Small inputs stay on the calling thread.
pub fn par_map<T: Send, R: Send>(items: Vec<T>, f: impl Fn(T) -> R + Sync) -> Vec<R> {
    use std::sync::Mutex;
    use std::sync::atomic::{AtomicUsize, Ordering};
    let workers = std::thread::available_parallelism().map_or(1, |n| n.get()).min(items.len() / 16);
    if workers <= 1 {
        return items.into_iter().map(f).collect();
    }
    let inputs: Vec<Mutex<Option<T>>> = items.into_iter().map(|x| Mutex::new(Some(x))).collect();
    let outputs: Vec<Mutex<Option<R>>> = inputs.iter().map(|_| Mutex::new(None)).collect();
    let next = AtomicUsize::new(0);
    std::thread::scope(|scope| {
        for _ in 0..workers {
            scope.spawn(|| {
                loop {
                    let i = next.fetch_add(1, Ordering::Relaxed);
                    let Some(input) = inputs.get(i) else { break };
                    let item = input.lock().unwrap().take().unwrap();
                    let result = f(item);
                    *outputs[i].lock().unwrap() = Some(result);
                }
            });
        }
    });
    outputs.into_iter().map(|o| o.into_inner().unwrap().unwrap()).collect()
}
