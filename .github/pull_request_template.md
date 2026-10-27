## What and why

<!-- Describe the behavior change and link the issue (e.g. Closes #123). -->

## Checklist

- [ ] `cargo build --locked --release` and `cargo test --locked --release --workspace` pass locally
- [ ] Every fix ships with a test (e.g. a `tests/native/pass` or `tests/native/fail` fixture)
- [ ] Language/semantics changes have an ADR in `docs/adr/`
- [ ] New diagnostics have a stable code documented in `docs/errors.md` (existing codes are never renumbered)
- [ ] If AI-assisted: I ran the tests and understand the whole diff
