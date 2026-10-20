"""Persistent TLS stream integration (ADR 0066); local echo server only."""
import collections
import os
import pathlib
import re
import socket
import ssl
import subprocess
import sys
import tempfile
import threading


def echo(connection):
    try:
        while True:
            data = connection.recv(65536)
            if not data:
                break
            if data == b'bye':
                # close_notify, so the client observes orderly EOF.
                connection = connection.unwrap()
                break
            connection.sendall(data)
    except (ConnectionResetError, BrokenPipeError, ssl.SSLError, OSError):
        pass
    finally:
        connection.close()


def serve(listener, context):
    while True:
        try:
            raw, _ = listener.accept()
        except OSError:
            return
        try:
            connection = context.wrap_socket(raw, server_side=True)
        except (ssl.SSLError, OSError):
            # Rejected clients abort the handshake.
            raw.close()
            continue
        threading.Thread(target=echo, args=(connection,), daemon=True).start()


with tempfile.TemporaryDirectory(prefix='tarn-tls-') as directory:
    root = pathlib.Path(directory)
    key, cert = root / 'key.pem', root / 'cert.pem'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                    '-keyout', str(key), '-out', str(cert), '-days', '1', '-subj', '/CN=localhost',
                    '-addext', 'subjectAltName=DNS:localhost'], check=True, capture_output=True)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    listener = socket.create_server(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    threading.Thread(target=serve, args=(listener, context), daemon=True).start()
    try:
        result = subprocess.run([sys.argv[1], f'https://localhost:{port}', str(cert),
                                 f'https://127.0.0.1:{port}'], capture_output=True, timeout=60,
                                env=dict(os.environ, TARN_TRACE_NET='1'))
        assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
        assert result.stdout == b'TLS stream fixture passed\n', result.stdout
        # Every descriptor a TLS owner opened is released exactly once.
        balance = collections.Counter()
        for event, fd in re.findall(rb'net:(open|close):(\d+)', result.stderr):
            balance[fd] += 1 if event == b'open' else -1
            assert balance[fd] in (0, 1), (fd, result.stderr)
        assert not +balance, (balance, result.stderr)
        assert result.stderr.count(b'net:open:') >= 7, result.stderr
        print('TLS stream echo, EOF, buffered plaintext, duplex, async, verification: passed')
    finally:
        listener.close()
