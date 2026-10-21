# ADR 0071: one-shot Launch, piped stdio, file handoff and spawn ordering

Status: accepted; extended by ADR 0072. Amends ADR 0070. Builds on ADR 0045 (blocking processes),
ADR 0035 (readiness) and ADR 0026 (drop order). No syntax change.

## Evidence

ADR 0070 left five limitations, found while porting a launcher from Python:

1. The Command kept the parent's copy of a handed-over stream for as long as
   the Command lived. A Command held in a variable stopped the peer from
   seeing end of stream.
2. Only `net.UnixStream` could be handed over.
3. There were no streaming child pipes (`Stdio.Piped`).
4. pidfd needs Linux 5.3. The target is a current kernel, so this only needs
   documenting.
5. An inheriting child could print before earlier Tarn `print`s, because the
   runtime's stdio buffer was not flushed before spawning.

## Decisions

### 1. `pass` returns a one-shot `Launch`

`Command.pass`/`pass_file` move the Command into
`Launch { command, passed Vec<_Handoff> }`. `Launch.start`, `status` and
`start_child` take `self`. The handoffs are destroyed when the launch call
returns, on success or failure, so the parent's copies close at launch by
construction. Reuse is an ordinary use-after-move (E4001). A Command without
handoffs keeps its reusable `&self` API.

Rejected alternatives:
- Consuming `Command.start`/`status`: this breaks the reusable Command, and
  there is no evidence against it.
- Closing the parent's copy in the native bridge: the Tarn owner would close
  it a second time (ADR 0045).

### 2. Explicit per-type handoff, no public descriptor interface

`pass(net.UnixStream)` and `pass_file(fs.File)` share one ordered list
(private `enum _Handoff`), so the k-th handoff of either kind is child
descriptor 3 + k. The process layer builds on io, net and fs; the graph stays
acyclic.

A public `interface Inheritable { fn fd(&self) i32 }` was rejected: any user
type could implement it and hand over a forged descriptor. A generic
descriptor owner in io waits until a third kind needs it.

### 3. `Stdio.Piped` and `Child`

The stdio mask now has two bits per stream (0 inherit, 1 null, 2 piped).
`start_child()` returns
`Child { pub stdin, pub stdout, pub stderr Option<...>, pub process Process }`.
`start`/`status` reject Piped with InvalidInput, because they would drop the
parent ends.

- **Pipe ends.** `ChildStdin`/`ChildStdout`/`ChildStderr` wrap the existing
  verified `_Pipe` owner, so there is no new resource ABI. The new intrinsic
  `process._write` uses the console SIGPIPE discipline: a dead reader is
  BrokenPipe, never a signal. EAGAIN is WouldBlock.
- **Readiness.** `set_nonblocking`, `register` and `deregister` take a
  `net.Poll`. Piped parent ends get a unique readiness identity, as timers do
  (sockets use SO_COOKIE). The identity is forgotten before close, so a
  recycled descriptor never matches an old token.
- **Destruction order.** Fields drop in declaration order (ADR 0026), so the
  pipes close before `process` waits. Destroying a Child cannot deadlock on a
  child that reads stdin until end of file. `Child.wait()` closes stdin first.
  An unread full stdout/stderr pipe still blocks a child; that is documented,
  as for `output()`.

### 4. Kernel

Documented minimum: Linux 5.3 (pidfd). A kernel without pidfd gets an
ordinary error, not an abort.

### 5. Flush before spawn

`tarn_rt_process_spawn` calls `fflush(stdout)` before `posix_spawnp`. This
flushes only the runtime's hidden `print` buffer, the same as `panic`'s
`fflush(NULL)`. Program-owned buffers (`console.BufferedOutput`, ADR 0040)
are never flushed implicitly.

## Risks and limits

- Destruction still waits and never kills (ADR 0045). A child that may never
  exit must be killed before an error path can destroy it. The tests do this
  explicitly.
- The communicate helper, async process I/O and Launch delegation follow in
  ADR 0072.
