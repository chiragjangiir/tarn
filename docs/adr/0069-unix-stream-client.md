# ADR 0069: Unix-domain stream client (`net.UnixStream`)

Status: accepted. Extends ADR 0034 (blocking networking) and ADR 0035
(readiness). No syntax change.

## Evidence

A local tool talks to a daemon over a private Unix-domain control socket. Tarn
already had JSON, processes and files, so the only missing piece that kept the
tool in Python was connecting to an `AF_UNIX` stream. `net` supported only
IPv4/IPv6 TCP and UDP, and its address representation (`SocketAddr`, 24 bytes)
cannot carry a socket path of up to 107 bytes.

## Decision

- `pub struct UnixStream { stream TcpStream }`. The private field reuses the
  verified descriptor owner `TcpStream { fd i32 }`. Copy, Transfer/Share
  (Transfer, not Share), post-drop destruction and the native ABI catalogue all
  follow structurally. The backend, drop elaboration and `net_sockets` order do
  not change.
- One new trusted intrinsic, `net._connect_unix(path &string) io._Raw`
  (`net` goes from 27 to 28 intrinsics). The C bridge validates the path
  (non-empty, no NUL, shorter than `sun_path`), creates the socket with
  `SOCK_CLOEXEC`, and connects. On failure it closes the fresh descriptor
  before any Tarn owner exists. It returns EINTR unchanged, and Tarn retries
  with a new socket, as `TcpStream.connect_addr` does.
- Methods delegate to the wrapped stream: `read`, `write`, `write_all`,
  `shutdown`, `close`, `set_nonblocking`. Readiness uses `Poll.register_unix`,
  `modify_unix` and `deregister_unix`. Deadlines come from `Poll.wait`'s
  timeout. There is no separate socket-timeout API.
- Error mapping happens in `UnixStream.connect`: ENOENT is NotFound, and
  EPERM/EACCES is PermissionDenied (domain 0 would report these as OtherOs).
  Path validation failures use domain 2, which maps to InvalidAddress. Other
  errnos keep the networking mapping (ECONNREFUSED is ConnectionRefused).

## Alternatives considered

- **A fourth verified descriptor owner `{ fd i32 }`.** This is more explicit,
  but it changes the ordered `net_sockets` ABI, the backend drop and argument
  paths, and needs a `close_unix` intrinsic, all for identical runtime
  behaviour. Revisit it if UnixStream ever needs representation that differs
  from TcpStream.
- **`extern "C"` socket/connect in Tarn** (as `bind_shared` does with
  setsockopt). This gives no errno access, and it builds an owner from an
  unverified FFI integer.
- **Extending `SocketAddr`.** Its 24-byte layout is part of the verified ABI
  and cannot hold a path.

## Risks and limits

- `TcpStream`'s address queries cannot represent an `AF_UNIX` address, and
  they are unreachable from `UnixStream` because its field is private. As
  defence in depth, the bridge's address conversion no longer aborts on a
  non-IP family. `getsockname`/`getpeername`/`recvfrom` instead return
  `EAFNOSUPPORT` (97) as an ordinary `Err(io.Error)` (`OtherOs`), so a future
  net-internal mistake fails recoverably and the owner is still closed once.
  The regression test `foreign_address_family_is_an_error_result_not_an_abort`
  forces the family through the syscall fault shim.
- Not implemented yet: a listener/accept, the abstract namespace, datagram
  sockets, `SO_PEERCRED`/fd passing, and `read_async`/`write_async`. Add them
  when a real program needs them.
