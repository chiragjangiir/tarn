# ADR 0072: communicate, async process I/O and Launch delegation

Status: accepted. Extends ADR 0071 and ADR 0037 (source async). No syntax
change.

## Evidence

ADR 0071 left three gaps:

- an unread, full stdout/stderr pipe blocks the child;
- process I/O blocked the executor;
- `Launch` duplicated the stdio configuration logic.

## Decisions

### `Child.communicate(self, input Vec<u8>) Result<Output, io.Error>`

The call moves the three pipe ends into three native tasks: one writer and
two readers. It then waits on the Process and joins the tasks. It is the
`output()` pattern extended with stdin.

The input is owned (`Vec<u8>`) because it crosses into a task; a borrowed
slice would need scoped tasks. It is not a convenience shortcut: concurrency
is the only correct way to avoid the pipe-capacity deadlock.

BrokenPipe on stdin (the child exited before reading everything) is not an
error. Streams that are not piped return empty buffers. There is no timeout:
compose with `wait_timeout` and `kill` beforehand.

### Async pipes

`read_async`/`write_async`/`write_all_async` reuse the socket poll helpers'
shape (`net._require_nonblocking`, `io._arm`, `io._with_waker`). Piped ends
already have readiness identities (ADR 0071), so there is no new wake
mechanism (ADR 0037).

### `Process.wait_async(self) Result<ExitStatus, io.Error>`

The async frame owns a pidfd from the new intrinsic `process._pidfd`. The
pidfd has a readiness identity and is held by the existing private `_Pipe`
descriptor owner, so it closes exactly once, including on frame abandonment.
There is no new resource ABI.

Each poll observes completion with WNOWAIT (ADR 0070), arms the waker on the
pidfd, and re-checks. After completion it closes the pidfd and reaps through
the ordinary consuming `wait()`.

The method is consuming, like `wait`. If the operation is abandoned (timeout
or task drop), the frame destroys the Process, which waits as ADR 0045
requires; there is no implicit kill. Deadlines come from the existing
`runtime.run_timeout` and `AsyncTask.join_timeout`. No new timeout primitive
is added.

### Launch delegation

`Launch.stdin/stdout/stderr` forward to the Command's own methods. There is a
single implementation of the stdio encoding.

## Limits

- `communicate` buffers both outputs without a bound, like `output()`.
- An abandoned `wait_async` on a child that never exits blocks in destruction.
  This is intentional (no implicit kill) and documented.
