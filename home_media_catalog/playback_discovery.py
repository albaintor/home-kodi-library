"""Best-effort LAN SSDP/UPnP discovery without probing arbitrary URLs."""

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit


def _ssdp_probe() -> list[dict]:
    request = (
        b"M-SEARCH * HTTP/1.1\r\n"
        b"HOST: 239.255.255.250:1900\r\n"
        b'MAN: "ssdp:discover"\r\n'
        b"MX: 1\r\n"
        b"ST: upnp:rootdevice\r\n\r\n"
    )
    found = {}
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.3)
            sock.sendto(request, ("239.255.255.250", 1900))
            import time

            until = time.monotonic() + 1.8
            while time.monotonic() < until and len(found) < 40:
                try:
                    data, address = sock.recvfrom(8192)
                except TimeoutError:
                    continue
                try:
                    ip = ipaddress.ip_address(address[0])
                    if not (ip.is_private or ip.is_link_local):
                        continue
                    content = data.decode("iso-8859-1", errors="replace")
                    headers = {}
                    for line in content.splitlines()[1:]:
                        if ":" in line:
                            key, value = line.split(":", 1)
                            headers[key.strip().lower()] = value.strip()
                    location = headers.get("location", "")
                    url = urlsplit(location)
                    if url.scheme not in {"http", "https"} or not url.hostname:
                        continue
                    if url.hostname != address[0] and not url.hostname.endswith(".local"):
                        continue
                    identifier = headers.get("usn") or location
                    found[identifier] = {
                        "name": "UPnP · " + address[0],
                        "kind": "upnp",
                        "host": address[0],
                        "port": url.port or 80,
                        "target": location,
                    }
                except (ValueError, OverflowError):
                    continue
    except OSError:
        return []
    return list(found.values())


async def discover_upnp() -> list[dict]:
    return await asyncio.to_thread(_ssdp_probe)
