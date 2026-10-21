# ADR 0070: Unix pairs, process handoff, stdio modes and bounded waits

Status: accepted; handoff ownership, stdio and layering amended by ADR 0071. Extends ADR 0045 (blocking processes) and ADR 0069
(`net.UnixStream`). Amends the ADR 0041 layer graph: process builds on io and
net. No syntax change.

## Evidence

A launcher that a coding agent was migrating from Python creates a connected
socket pair, gives one end to a child server as an inherited descriptor, keeps
the child's GTK/runtime output off the protocol channel (MCP), and must detect
a failed child and shut it down without blocking indefinitely. Checking the
code against those four needs showed four stdlib gaps, none in the language:

- no `socketpair`;
- spawn closed every descriptor from 3 up (`closefrom(3)`), so nothing could be
  inherited;
- stdio could only be inherited (`start`/`status`) or replaced as a whole
  (`output`: /dev/null stdin plus capture);
- `wait(self)` consumes the child and blocks, and `kill(&mut self)` needs it
  alive, so there was no bounded wait.

## Decision

### `net.UnixStream.pair() Result<UnixPair, io.Error>`

`UnixPair { pub first, pub second }` holds two owned ends. The ends are moved
out as fields, because Tarn has no tuples. The new intrinsic `net._pair_unix`
calls `socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC)`. `net` now has 29
intrinsics. Each end is the ordinary verified UnixStream owner.

### Layer change: process builds on net

`Command.pass` must take a `net.UnixStream` and read its private descriptor.
`STDLIB_BELOW` gains `process -> net`. The graph stays acyclic: net never sees
process. The cost is that importing `process` also loads and verifies `net`.

The alternative was a generic owned descriptor in `io` that both layers
convert to. That is a larger new resource kind, justified only when a second
handoff type (files, pipes) appears.

### `Command.stdin/stdout/stderr(Stdio)`

`pub copy enum Stdio { Inherit, Null }` applies to `start` and `status`, and
travels as a 3-bit mask. `output()` keeps its fixed /dev/null stdin and
capture. `Stdio.Piped` (streaming child pipes) is deferred until a real
program needs it.

### `Command.pass(self, stream net.UnixStream) Command`

The call consumes the stream into the Command. The k-th handoff becomes the
child's descriptor 3 + k (the LISTEN_FDS convention). The bridge first
duplicates every source above all targets (`F_DUPFD_CLOEXEC`, minimum 3 + n),
then `dup2`s each duplicate onto its target, so no `dup2` can overwrite a later
source. It then closes everything from 3 + n up. The temporaries close in the
parent after spawn. The Command keeps the parent's copy until it is destroyed:
building it in the spawning expression closes the copy at the end of the
statement, so the peer sees EOF when the child exits. The limit is 64
handoffs; more is InvalidInput.

Rejected alternative: caller-chosen child descriptor numbers. Arbitrary
targets cannot be combined safely with `closefrom` (gaps would leak inherited
descriptors) and need duplicate/stdio validation. Sequential numbering has
neither problem.

### `Process.try_wait(&mut self)` and `Process.wait_timeout(&mut self, millis)`

Both return `Result<Option<ExitStatus>, io.Error>`. The new intrinsic
`process._poll_exit(pid, timeout)` waits on a `pidfd_open` descriptor with
`poll`, resuming EINTR with the remaining time, then calls
`waitid(P_PID, WEXITED | WNOHANG | WNOWAIT)`.

`WNOWAIT` is the key choice: completion is **observed, not reaped**. The pid
stays owned by `Process`, so `wait()`/destruction is still the single reap
(ADR 0045), and no recycled pid can be reaped by mistake. A bounded shutdown is
`wait_timeout`, then `kill`, then `wait`. Destruction semantics do not change:
there is no implicit kill and no detach.

I first considered a consuming `wait_timeout(self) -> Waited { Exited, Pending(Process) }`.
`WNOWAIT` makes the borrowing form safe and simpler.

## Risks and limits

- Linux 5.3+ for pidfd (already far below the libc 2.34 spawn requirement).
- Only UnixStream can be handed over. Files and pipes would need the generic
  descriptor owner described above.
- The parent's copy of a passed end lives as long as the Command. A Command
  kept in a variable keeps the peer from seeing EOF until it is dropped. This
  is documented; consuming `start` would change an existing signature.
- Tarn's own buffered stdout is not flushed before spawning, so an inheriting
  child's output can appear before earlier Tarn prints (pre-existing).
