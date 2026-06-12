"""Capture the byox cluster's TLS chain so Mode A (httpx) can verify it.

byox serves a self-signed chain, so the portal API call fails with
CERTIFICATE_VERIFY_FAILED. Run this on a machine on the byox network; it writes
the presented chain to certificates/byox-chain.pem and proves verification works
with it. Point SSL_CERT_FILE at that file (start-local-acc.ps1 does this for the
Byox target). The .pem is gitignored — never commit it; regenerate per machine.

    .venv\\Scripts\\python fetch_byox_cert.py [host] [port]
"""

import os
import socket
import ssl
import sys

HOST = sys.argv[1] if len(sys.argv) > 1 else "portal-api.edge.byox.demo"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 443
OUT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "certificates", "byox-chain.pem"
)


def fetch_chain(host: str, port: int) -> list[bytes]:
    """Return the PEM-encoded cert chain the server presents, without verifying it."""
    ctx = ssl._create_unverified_context()
    with socket.create_connection((host, port), timeout=15) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as tls:
            # get_unverified_chain() is public only in 3.13+; the laptop runs
            # 3.12, where it lives on the underlying _ssl object.
            chain = tls._sslobj.get_unverified_chain()
    return [cert.public_bytes(ssl._ssl.ENCODING_PEM) for cert in chain]


def verify(host: str, port: int, cafile: str) -> None:
    """Reconnect WITH verification against cafile; raises ssl.SSLError if it fails."""
    ctx = ssl.create_default_context(cafile=cafile)
    with socket.create_connection((host, port), timeout=15) as sock:
        with ctx.wrap_socket(sock, server_hostname=host):
            pass


def main() -> None:
    pems = fetch_chain(HOST, PORT)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        f.write("".join(p.decode() for p in pems))
    print(f"wrote {len(pems)} cert(s) -> {OUT}")

    try:
        verify(HOST, PORT, OUT)
    except ssl.SSLError as e:
        print(f"VERIFY FAILED: {e}")
        raise SystemExit(1)

    print(f"VERIFY OK — {HOST} trusts this chain.")
    print("\nSet this and Mode A connects (PowerShell):")
    print(f'  $env:SSL_CERT_FILE = "{OUT}"')


if __name__ == "__main__":
    main()
