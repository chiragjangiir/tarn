# ADR 0066: persistent TLS client streams through libcurl connect-only

Status: accepted and implemented (Phase 35). Extends ADR 0062.

## Evidence

The planned orvix voice agent must keep one encrypted connection to a speech
provider open for a whole call while audio flows in both directions at once.
`https.request` (ADR 0062) is a synchronous single exchange that buffers the
complete response; it cannot carry a long-lived duplex byte stream. The same
gap blocks WebSocket, server-sent events and TLS to PostgreSQL. A TLS client
byte stream is the smallest primitive under all of them, so it comes first;
WebSocket framing is a later ordinary-Tarn module on top of it.

## Decision

Reuse the libcurl already loaded by ADR 0062 in connect-only mode
(`CURLOPT_CONNECT_ONLY = 1`). `curl_easy_perform` then performs DNS, TCP, the
TLS handshake and certificate/hostname verification, and stops. Afterwards
`curl_easy_send`/`curl_easy_recv` move raw application bytes over the TLS
session, and `CURLINFO_ACTIVESOCKET` exposes the descriptor for readiness.
The bridge loads two more symbols, `curl_easy_send` and `curl_easy_recv`, as
optional: their absence makes only TLS streams unavailable (-1), never
one-shot HTTPS. No new native library, Cargo dependency or TLS policy is
introduced: verification stays always on, only `https://host:port` URLs are
accepted, redirects and proxies stay off, and the CA option keeps ADR 0062
semantics (empty uses system trust).

### Placement: trusted `net`, not ordinary `https`

The draft placed the owner in `https`. Implementation showed that is wrong:
`https` is an ordinary module (ADR 0041) and ordinary modules have no native
destruction authority. An owner whose destruction must release a native handle
needs the verified resource catalog, which only trusted layers have. So the
type is `net.TlsStream`, beside `TcpStream`, and its private intrinsics are
`net._tls_connect`, `_tls_read`, `_tls_write`, `_tls_fd` and `_close_tls`.
`network_abi` verifies their exact signatures, the owner's private shape
`{ native usize, nonblocking bool }` and its capabilities, as for every other
net resource. `https` keeps its one-shot API unchanged.

### Ownership

`TlsStream` is a non-Copy owner of a private native handle (curl handle,
descriptor, failed flag). It is Transfer and not Share, like `TcpStream` (ADR
0034): one task at a time drives the TLS state machine. Reads and writes take
`&mut self`; there are no independent read and write halves in v0. Full duplex
is one task alternating reads and writes on readiness.

The descriptor belongs to the curl handle, never to Tarn. Tarn never wraps it
in a `TcpStream`, never closes it and never exposes it publicly; `_tls_fd` is
private and used only for readiness registration. Destruction follows verified
post-drop IR (`tarn_rt_net_tls_drop`) and calls `curl_easy_cleanup` exactly
once; consuming `close` does the same. Destruction does not promise a TLS
close_notify exchange beyond what cleanup sends, and never waits for the peer.

### Operations and mode

- `TlsStream.connect(url &string, options &TlsOptions) Result<TlsStream, io.Error>`:
  blocking, bounded by `TlsOptions.timeout_millis` through
  `CURLOPT_CONNECTTIMEOUT_MS`. It blocks the current native task, as DNS does
  in ADR 0034. The bound does not apply to later transfers.
- `read`/`write`: single partial operations. Zero from `read` is orderly EOF.
  libcurl keeps the descriptor nonblocking, so mode is **stream state**, not
  descriptor state (unlike `TcpStream.set_nonblocking`, which changes
  O_NONBLOCK). In blocking mode the bridge waits with `poll(2)` for the needed
  direction and retries; in nonblocking mode `CURLE_AGAIN` returns WouldBlock.
- `write_all` loops in Tarn and rejects a nonblocking stream, as
  `TcpStream.write_all` does (ADR 0035).
- `poll_tls_read`/`poll_tls_write` and `read_async`/`write_async`/
  `write_all_async` follow the Phase 12C/13 leaf pattern and never block the
  executor, whatever the stream's mode.

Buffers are borrowed only for the duration of a call. Native code copies into
or out of them and retains nothing, as in ADR 0062.

### Readiness

`Poll.register_tls`, `modify_tls` and `deregister_tls` register the active
descriptor with the existing epoll owner and token scheme. Tokens and
SO_COOKIE checks are unchanged, so recycled descriptors cannot be confused
with earlier owners.

TLS adds one rule that plain sockets do not have: decrypted bytes may already
be buffered inside the TLS library while the descriptor is not readable. A
level-triggered wait could then sleep forever with data available. Contract:
after a readable event, call `read` until WouldBlock before waiting again. The
poll leaves enforce this by always attempting the transfer before arming
readiness. A TLS key update may make a read need writability; curl drives the
TLS state internally and the WouldBlock retry covers it.

### Errors

TLS errors use `io.Error` domain 5. Codes are libcurl's own (positive) plus
bridge codes: -1 unavailable, -3 invalid input, -5 an operation on a stream
that already failed. `io._error` maps them to existing kinds; no ErrorKind
variant was added:

| Code | Kind |
|---|---|
| 81 (`CURLE_AGAIN`) | WouldBlock |
| 6 | DnsFailure |
| 7 | ConnectionRefused |
| 28 | TimedOut |
| 55 / 56 | BrokenPipe / ConnectionReset |
| 35, 58, 60, 77 (handshake, certificate, CA) | InvalidData |
| 3, -3 | InvalidInput |
| other | OtherOs, code preserved |

After any error other than WouldBlock the stream is failed: further transfers
return -5 and only destruction remains. Nothing reconnects or retries
implicitly. `poll(2)` EINTR inside a blocking wait is retried, since it holds
no resource; close is never retried.

## Alternatives considered

**Direct OpenSSL binding.** Full control over the handshake, nonblocking
connect and ALPN. Rejected for now: a second native TLS authority, and Tarn
would own certificate and hostname verification code libcurl already gets
right. First fallback if connect-only proves insufficient.

**libcurl's native WebSocket API (`CONNECT_ONLY = 2`, `curl_ws_send/recv`).**
Rejected as the primitive: it hides framing and control frames behind native
state, contradicts protocol logic living in Tarn (ADR 0046), and leaves SSE and
PostgreSQL TLS unsolved. Fallback if the Tarn WebSocket module shows problems.

**libcurl multi interface for an async handshake.** It pulls curl's socket and
timer callbacks into the runtime, a second readiness model beside `Poll`.
Deferred until a blocking handshake is shown to matter.

**Pure Tarn TLS.** Out of scope for the reasons in ADR 0062.

## Risks and debt

- The handshake blocks a native task. Many concurrent calls must connect from
  a native `spawn` and transfer the stream, or wait for the multi interface.
- Connect-only send/recv depends on the system libcurl build. Verified with
  libcurl 8.23.0 and OpenSSL 3.6.5.
- The buffered-plaintext rule is easy to violate in hand-written pollers; only
  the stdlib leaves encode it.
- Blocking mode waits without a deadline after connect, like a blocking
  `TcpStream`. Callers needing one use the async helpers with ADR 0039 timeouts.
- No ALPN control, client certificates, proxy or session resumption.

## Acceptance (implemented)

`tests/integration/tls_stream_fixture.py` runs
`tests/native/pass/tls_stream_fixture.tarn` against a local Python TLS echo
server with a one-day fixture certificate; no public endpoint:

- blocking echo of UTF-8 text, then orderly EOF after the peer's close_notify;
- one record decrypted as a whole: the second read succeeds without a new
  readiness event (buffered-plaintext regression);
- 1 MiB full duplex on one task through `Poll` with byte-exact verification;
- async write_all/read through `runtime.Execution.block_on`;
- the owner moved into a native `spawn` task and dropped there;
- hostname mismatch, untrusted CA (system trust) and a non-`https` URL are
  rejected with InvalidData, InvalidData and InvalidInput;
- `TARN_TRACE_NET` shows every opened TLS descriptor released exactly once.

Golden tests: `tests/types/fail/E3022_tls_stream_share` (not Share) and
`tests/moves/fail/E4001_tls_stream_after_close` (consuming close).

## Future evidence that would change this

A measured need for async handshakes at scale, a protocol requiring ALPN
(HTTP/2) or client certificates, or connect-only send/recv bugs in the system
libcurl would justify the multi interface or a direct OpenSSL binding.
