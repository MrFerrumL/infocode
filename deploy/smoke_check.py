"""Check public routes and HEAD framing through the local HTTPS proxy after release."""
import argparse
from pathlib import Path
import socket
import ssl

from watch_service import domain_from_env


def probe(domain, path, method='GET'):
    with socket.create_connection(('127.0.0.1', 443), timeout=8) as raw:
        with ssl.create_default_context().wrap_socket(raw, server_hostname=domain) as stream:
            stream.sendall(f'{method} {path} HTTP/1.1\r\nHost: {domain}\r\nConnection: close\r\n\r\n'.encode('ascii'))
            data = b''
            while True:
                block = stream.recv(65536)
                if not block:
                    break
                data += block
                if len(data) > 2_000_000:
                    raise RuntimeError('Unexpectedly large smoke response')
    headers, separator, body = data.partition(b'\r\n\r\n')
    status = headers.split(b'\r\n', 1)[0].split()
    if not separator or len(status) < 2 or status[1] != b'200':
        raise RuntimeError(f'Unexpected response for {method} {path}')
    if method == 'HEAD' and body:
        raise RuntimeError(f'HEAD returned a body for {path}')
    if method == 'GET' and not body:
        raise RuntimeError(f'Empty response for {path}')
    print(f'OK {method} {path}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env', type=Path, default=Path('/opt/infocode/.env.production'))
    args = parser.parse_args()
    domain = domain_from_env(args.env)
    for path in ('/', '/login/', '/health/', '/static/app.css'):
        probe(domain, path)
    for path in ('/', '/login/'):
        probe(domain, path, 'HEAD')


if __name__ == '__main__':
    main()
