# WebSocket client

`import "websocket"` provides an RFC 6455 client over a verified TLS stream
(`wss://` only). It is ordinary Tarn; see
[ADR 0067](adr/0067-websocket-client.md) for the protocol policy and limits.

```tarn
import "websocket"

fn run() Result<void, websocket.Error> {
    var options = websocket.Options.new()
    try options.header(&"Authorization", &"Bearer token")
    var client = try websocket.connect(&"wss://example.com/v1/stream", &options)
    try client.send_text(&"hello")
    match try client.receive() {
        websocket.Message.Text(text) => { print(text) }
        websocket.Message.Binary(data) => { print(data.len()) }
        websocket.Message.Close(code, reason) => { print(code) }
    }
    try client.close(1000, &"done")
    return Ok(())
}

fn main() {
    match run() { Ok(value) => {}
        Err(error) => { panic("websocket failed") } }
}
```

`connect` blocks the current native task. Pings are answered automatically.
`message_limit` bounds each received message and `send_limit` bounds the
encoded output queue; a send that would exceed it fails with `TooLarge`.

For full duplex (for example streaming audio while receiving events), call
`client.set_nonblocking(true)`, register `client.stream()` with
`Poll.register_tls`, and on each readiness event:

- call `receive` until `error.would_block()`;
- queue sends while `client.pending()` stays under your bound;
- call `flush` when the stream is writable and `pending() > 0`.

Partial frames and queued output survive WouldBlock. Dropping a client closes
the TLS stream without a closing handshake; call `close` and wait for the
peer's `Close` message to close cleanly.
