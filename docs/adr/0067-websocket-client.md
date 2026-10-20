# ADR 0067: RFC 6455 WebSocket client as ordinary Tarn over TlsStream

Status: accepted and implemented (Phase 35). Builds on ADR 0066.

## Evidence

The orvix voice agent talks to speech providers over `wss://` for the length
of a call: audio chunks out, audio and events in, both directions at once.
ADR 0066 supplied the persistent TLS byte stream; this phase adds the protocol.

## Decision

`stdlib/websocket` is an **ordinary** module (ADR 0041). Every protocol rule
lives in Tarn: URL parsing, the HTTP/1.1 upgrade, Sec-WebSocket-Accept
verification, frame encoding and masking, fragmentation, control frames,
limits and the closing handshake. It has no intrinsic authority and needs no
compiler or runtime change. Its single native call is libc `getrandom` through
`extern "C"`, the same mechanism `os` uses, for the 16-byte handshake key and
the per-frame masking keys RFC 6455 section 10.3 requires to be unpredictable.
Reads of at most 256 bytes are never interrupted or shortened on Linux, so no
errno handling is needed.

libcurl's own WebSocket API was rejected in ADR 0066: it would hide framing in
native state and contradict protocol logic living in Tarn (ADR 0046).

### Public surface

- `connect(url, &Options) Result<Client, Error>`: `wss://authority[/path][?query]`
  only; userinfo, fragments, spaces and non-ASCII are rejected. Blocks the
  current native task for TLS and the upgrade; the client starts blocking.
- `Options`: `tls` (ADR 0066 options), `message_limit` (default 1 MiB, at most
  64 MiB, fragments included), `send_limit` (bound on queued encoded bytes,
  default 1 MiB) and `header(name, value)` for extra handshake headers such as
  `Authorization`. CR/LF/NUL and the headers this module owns (Host, Upgrade,
  Connection, Sec-WebSocket-Key/Version/Extensions) are rejected.
- `Client.receive() Result<Message, Error>` with `Message.Text(string)`,
  `Binary(Vec<u8>)` and `Close(code, reason)`. Pings are answered and pongs
  consumed internally.
- `send_text`, `send_binary`, `ping`, `close(code, reason)`, `flush`,
  `pending`, `set_nonblocking`, and `stream()` (a shared borrow of the
  `TlsStream`, for `Poll.register_tls` only).

### Ownership

`Client` owns its `TlsStream`, the receive buffer, the queued output and any
fragmented message in progress. It is therefore Transfer and not Share
structurally; no capability is declared by hand. Dropping it drops the stream
and performs no I/O, as buffered writers do (ADR 0040): a client that wants the
closing handshake calls `close` and receives the peer's Close.

### Blocking and nonblocking with one implementation

Every partial state lives in the `Client`, never in a suspended stack: bytes of
an incomplete frame stay in the input buffer, and encoded frames stay in the
output queue with a written offset. One `receive` implementation therefore
serves both modes. In blocking mode it reads until a message completes and
sends flush completely. In nonblocking mode `receive` returns an Io error with
WouldBlock (`Error.would_block()`) when no complete message is buffered, and
sends queue the frame and write what the stream accepts; the caller drives
`flush` from writable readiness. `send_limit` makes the queue bounded: a send
that would exceed it fails with TooLarge instead of growing memory, which is
the backpressure signal for audio producers.

Full duplex is one task alternating `receive` and sends on `Poll` readiness.
Two tasks cannot share a `Client` (`&mut` exclusivity), and v0 has no `select`
over async operations, so async variants were deliberately not added in this
phase. A nonblocking `receive` always consumes buffered frames before reading,
so the TLS buffered-plaintext rule of ADR 0066 holds.

### Protocol policy

- The 101 response must have `Upgrade: websocket`, an `upgrade` token in
  `Connection`, and exactly one matching `Sec-WebSocket-Accept`. No extension
  is offered, so any `Sec-WebSocket-Extensions` fails the handshake; a
  `Sec-WebSocket-Protocol` is accepted only when the caller offered one.
  Response heads are bounded to 16 KiB. Bytes after the head are kept as frames.
- Received frames with RSV bits, a mask bit, an unknown opcode, a fragmented or
  oversized (>125) control frame, a 64-bit length with the top bit set, a
  continuation without a message, or a new message inside a fragmented one
  fail the connection with Protocol.
- Lengths are checked against `message_limit` from the frame header, before
  the payload is buffered.
- Text messages are validated as UTF-8 once complete (InvalidUtf8).
- Close codes must be sendable codes (1000-1003, 1007-1011, 3000-4999); a
  one-byte close payload is a protocol error; no payload reports 1005. The
  client echoes the peer's code once and refuses further sends and receives
  with Closed.
- Sent frames are unfragmented, always masked with a fresh random key, and use
  the minimal length encoding.
- A failed connection is final: every later operation returns Closed or the
  original error. The client does not send a close frame on failure; dropping
  it closes TLS.

### Private helpers

SHA-1 (needed only for the accept value, never as a security digest), base64
and ASCII case-insensitive comparison are private to the module. PostgreSQL
SCRAM will need base64 and SHA-256 next; they become public modules when that
second user exists (ADR 0063), not before.

## Language evidence from this port

- `match` is a statement, so `Option`/`Result` values are extracted with small
  generic `_need`/`_lift` helpers and `try`. Workable and readable.
- There is no way to move a value out of a field behind `&mut self` (no
  take/replace). Completing a fragmented message copies the buffer once. This
  is the first concrete pressure for a `take`-style operation; it is recorded
  here, not solved.
- A field and a method with the same name resolve to the field, so the field
  is named `transport` and the method `stream()`.
- Array literals need an explicit type and length (`[5]u32{...}`).

## Risks and debt

- The handshake blocks a native task (ADR 0066).
- No permessage-deflate, no subprotocol negotiation beyond pass-through, no
  redirects, no proxies.
- No async variants; one task drives the client through `Poll`.
- The fixture server is test code written with Python's hashlib/base64. It is
  independent of this implementation but is not a third-party conformance
  suite; Autobahn-style coverage remains future work.

## Acceptance (implemented)

`tests/integration/websocket_fixture.py` runs
`tests/native/pass/websocket_fixture.tarn` against a local TLS WebSocket server:

- RFC 6455 accept vector (also in the plain native suite);
- the server checks Host, version, key length, `Authorization` and that every
  client frame is masked with no RSV bits;
- a frame arriving in the same read as the 101 response;
- UTF-8 text, empty binary, 1000-byte (16-bit length) and 200000-byte (64-bit
  length) binary echoes;
- client ping, then a fragmented text message with an interleaved ping whose
  pong the server verifies byte for byte;
- client-initiated close (1000) and server-initiated close (1001 with a UTF-8
  reason, echoed code verified by the server); Closed afterwards;
- nonblocking duplex: 64 binary messages of 16 KiB through `Poll` with a
  bounded output queue, verified byte for byte;
- TooLarge, masked server frame, RSV bit, invalid UTF-8, wrong accept,
  unrequested extension, non-101 status, `ws://` and userinfo URLs rejected;
- reserved and CR/LF-injected handshake headers rejected.

## Future evidence that would change this

A provider that requires permessage-deflate, measured cost from copying
fragmented messages, or an async voice agent that needs `select`-style
waiting would each justify a separate phase.
