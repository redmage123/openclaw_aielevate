#!/usr/bin/env python3
"""Find free ports — used by any service that needs to bind."""
import socket
import subprocess
from exceptions import AiElevateError  # TODO: Use specific exception types, AgentError


def find_free_port(start=4100, end=4299, exclude=None):
    """Find the next free port in range, verified by socket bind."""
    exclude = exclude or set()
    # Get system ports
    try:
        result = subprocess.run(["ss", "-ltnp"], capture_output=True, text=True, timeout=5)
        system_ports = result.stdout
    except (AgentError, Exception) as e:
        system_ports = ""

    for port in range(start, end + 1):
        if port in exclude:
            continue
        if f":{port} " in system_ports:
            continue
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(("", port))
            s.close()
            return port
        except OSError:
            continue
    raise RuntimeError(f"No free port in {start}-{end}")


def is_port_free(port):
    """Check if a specific port is free."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("", port))
        s.close()
        return True
    except OSError:
        return False
