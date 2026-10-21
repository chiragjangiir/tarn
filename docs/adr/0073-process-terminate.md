# ADR 0073: `Process.terminate()` (SIGTERM)

Status: accepted. Extends ADR 0045 and ADR 0070. No syntax change.

## Evidence

While porting the launcher, Codex found that the Python version calls
`terminate()` (SIGTERM) and escalates to SIGKILL after three seconds. Tarn
only had `kill()` (SIGKILL). Going straight to SIGKILL would deny the GTK/MCP
server its clean shutdown, so behaviour would not be preserved.

## Decision

Add `Process.terminate(&mut self) Result<void, io.Error>`, backed by the new
intrinsic `process._terminate(pid)` (`kill(pid, SIGTERM)`). It mirrors
`kill()`: it borrows mutably, keeps ownership, and the child must still be
waited.

Because bounded waits never reap (WNOWAIT, ADR 0070), the pid stays owned by
the Process until `wait()`. Signalling an exited but unreaped child is
harmless and can never reach a recycled process. Destruction still waits and
never signals (ADR 0045).

Graceful bounded shutdown:

```tarn
try child.terminate()
match try child.wait_timeout(3000) {
    Some(status) => {}
    None => { try child.kill() }
}
status := try child.wait()
```

## Alternatives

A generic `Process.signal(number i32)` would expose raw signal numbers with no
evidence that anything beyond TERM and KILL is needed. Named methods are
added only when a real program needs one (for example SIGINT or SIGHUP).

## Bug found: SIGPIPE on a partial pipe write

The full test suite under load exposed an intermittent parent death (exit 141,
SIGPIPE) in `Child.communicate`. When a blocked write to a pipe loses its
reader midway, Linux returns a **partial** count and still raises SIGPIPE. The
shared console/pipe helper consumed SIGPIPE only when `write` returned
-1/EPIPE, so the pending signal was delivered when the mask was restored. This
affected console writes as well as child stdin.

The helper now consumes any SIGPIPE that became pending during its own call,
whatever the result. The regression test
`reader_vanishing_mid_write_is_broken_pipe_not_sigpipe` fails with the old
code. Residual edge: a process-directed SIGPIPE sent by another process during
that exact call is also absorbed. This is accepted, because SIGPIPE has no
other meaning in Tarn programs.
