"""WebSocket client integration (ADR 0067): a minimal RFC 6455 server over
local TLS, written independently with hashlib/base64. No external services."""
import base64
import hashlib
import os
import pathlib
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import threading

GUID = b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
failures = []


def read_exact(connection, count):
    data = b''
    while len(data) < count:
        chunk = connection.recv(count - len(data))
        if not chunk:
            raise EOFError
        data += chunk
    return data


def read_frame(connection):
    b0, b1 = read_exact(connection, 2)
    assert b1 & 128, 'client frames must be masked'
    assert b0 & 112 == 0, 'client set RSV bits'
    length = b1 & 127
    if length == 126:
        length = struct.unpack('!H', read_exact(connection, 2))[0]
    elif length == 127:
        length = struct.unpack('!Q', read_exact(connection, 8))[0]
    mask = read_exact(connection, 4)
    payload = bytes(b ^ mask[i % 4] for i, b in enumerate(read_exact(connection, length)))
    return bool(b0 & 128), b0 & 15, payload


def frame(opcode, payload=b'', fin=True, rsv=0, mask=None):
    head = bytes([(128 if fin else 0) | rsv | opcode])
    flag = 128 if mask else 0
    if len(payload) < 126:
        head += bytes([flag | len(payload)])
    elif len(payload) < 65536:
        head += bytes([flag | 126]) + struct.pack('!H', len(payload))
    else:
        head += bytes([flag | 127]) + struct.pack('!Q', len(payload))
    if mask:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        head += mask
    return head + payload


def handshake(connection):
    request = b''
    while b'\r\n\r\n' not in request:
        chunk = connection.recv(4096)
        if not chunk:
            raise EOFError
        request += chunk
    lines = request.split(b'\r\n\r\n')[0].decode().split('\r\n')
    method, target, version = lines[0].split(' ')
    headers = {}
    for line in lines[1:]:
        name, value = line.split(':', 1)
        headers[name.strip().lower()] = value.strip()
    assert method == 'GET' and version == 'HTTP/1.1', lines[0]
    assert headers['upgrade'].lower() == 'websocket'
    assert 'upgrade' in headers['connection'].lower()
    assert headers['sec-websocket-version'] == '13'
    assert len(base64.b64decode(headers['sec-websocket-key'])) == 16
    assert headers['host'].startswith('localhost:'), headers['host']
    assert headers['authorization'] == 'Bearer fixture-token', headers
    accept = base64.b64encode(hashlib.sha1(headers['sec-websocket-key'].encode() + GUID).digest()).decode()
    path = target.split('?')[0]
    if path == '/status':
        connection.sendall(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n')
        return None
    if path == '/badaccept':
        accept = base64.b64encode(b'x' * 20).decode()
    response = f'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n'
    if path == '/extension':
        response += 'Sec-WebSocket-Extensions: permessage-deflate\r\n'
    response += '\r\n'
    # The first frame may share the segment with the handshake response.
    if target == '/echo?greet=1':
        connection.sendall(response.encode() + frame(1, b'hello'))
    else:
        connection.sendall(response.encode())
    return target


def scenario(connection, command):
    if command == b'fragments':
        # Fragmented text with a ping between fragments; the pong must echo it.
        connection.sendall(frame(1, 'frag'.encode(), fin=False) + frame(9, b'p1') +
                           frame(0, 'ment'.encode(), fin=False) + frame(0, 'ado ✓'.encode()))
        fin, opcode, payload = read_frame(connection)
        assert (fin, opcode, payload) == (True, 10, b'p1'), (opcode, payload)
        connection.sendall(frame(1, b'pong-ok'))
    elif command == b'big':
        connection.sendall(frame(2, b'x' * (1048576 + 1)))
    elif command == b'masked':
        connection.sendall(frame(1, b'bad', mask=b'abcd'))
    elif command == b'utf8':
        connection.sendall(frame(1, b'\xff\xfe'))
    elif command == b'rsv':
        connection.sendall(frame(1, b'bad', rsv=64))
    elif command == b'goodbye':
        connection.sendall(frame(8, struct.pack('!H', 1001) + 'adiós'.encode()))
        fin, opcode, payload = read_frame(connection)
        assert opcode == 8 and payload == struct.pack('!H', 1001), payload
        return False
    else:
        return None
    return True


def serve_one(raw, context):
    try:
        connection = context.wrap_socket(raw, server_side=True)
    except (ssl.SSLError, OSError):
        raw.close()
        return
    try:
        assert connection.selected_alpn_protocol() == 'http/1.1', connection.selected_alpn_protocol()
        target = handshake(connection)
        if target is None or target in ('/badaccept', '/extension'):
            return
        while True:
            fin, opcode, payload = read_frame(connection)
            assert fin, 'client data frames are unfragmented'
            if opcode == 8:
                connection.sendall(frame(8, payload[:2]))
                return
            if opcode == 9:
                connection.sendall(frame(10, payload))
                continue
            if opcode == 1:
                handled = scenario(connection, payload)
                if handled is False:
                    return
                if handled:
                    continue
            connection.sendall(frame(opcode, payload))
    except (EOFError, ConnectionResetError, BrokenPipeError, ssl.SSLError, OSError):
        pass
    except AssertionError as error:
        failures.append(repr(error))
    finally:
        connection.close()


def serve(listener, context):
    while True:
        try:
            raw, _ = listener.accept()
        except OSError:
            return
        threading.Thread(target=serve_one, args=(raw, context), daemon=True).start()


with tempfile.TemporaryDirectory(prefix='tarn-ws-') as directory:
    root = pathlib.Path(directory)
    key, cert = root / 'key.pem', root / 'cert.pem'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                    '-keyout', str(key), '-out', str(cert), '-days', '1', '-subj', '/CN=localhost',
                    '-addext', 'subjectAltName=DNS:localhost'], check=True, capture_output=True)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    # Prefer h2 when offered: HTTP/1.1 upgrades must constrain client ALPN.
    context.set_alpn_protocols(['h2', 'http/1.1'])
    listener = socket.create_server(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    threading.Thread(target=serve, args=(listener, context), daemon=True).start()
    try:
        result = subprocess.run([sys.argv[1], f'wss://localhost:{port}', str(cert)],
                                capture_output=True, timeout=60)
        assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
        assert result.stdout == b'WebSocket fixture passed\n', result.stdout
        assert not failures, failures
        print('WebSocket handshake, framing, fragments, ping/pong, limits, close, duplex: passed')
    finally:
        listener.close()
