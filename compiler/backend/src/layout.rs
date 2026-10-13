//! Native layouts come from the shared authority in `tarn_ir::layout`.
use crate::{Error, Result};
pub use tarn_ir::layout::Layout;
use tarn_types::{Ty, Typed};

pub fn layout(t: &Typed, ty: &Ty) -> Result<Layout> {
    tarn_ir::layout::layout(t, ty).map_err(|e| Error::unsupported(e.message))
}
