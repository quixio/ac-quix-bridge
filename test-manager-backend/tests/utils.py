import socket


def find_free_port() -> str:
    """Find a free port to use for the mock portal API."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        s.listen(1)
        port = s.getsockname()[1]
    return str(port)
