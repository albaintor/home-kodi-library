"""Optional LAN Bonjour service publication for native iOS client discovery.

Docker bridge networking does not forward mDNS: use host networking for advertising.
Failures are non-fatal so catalogue availability never depends on multicast.
"""

import logging
import socket

from zeroconf import ServiceInfo, Zeroconf

LOGGER = logging.getLogger(__name__)
SERVICE_TYPE = "_homekodi._tcp.local."


class BonjourPublisher:
    def __init__(self, name: str, port: int):
        self.name = name
        self.port = port
        self.service = None
        self.zeroconf = None

    def start(self) -> None:
        try:
            # Route selection only; no datagram is transmitted.
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("192.0.2.1", 9))
                address = sock.getsockname()[0]
            if address.startswith("127.") or address.startswith("169.254."):
                LOGGER.warning("Bonjour skipped: no usable IPv4 LAN address.")
                return
            self.service = ServiceInfo(
                SERVICE_TYPE,
                f"{self.name}.{SERVICE_TYPE}",
                addresses=[socket.inet_aton(address)],
                port=self.port,
                properties={"api": "v1", "path": "/"},
                server=socket.gethostname().split(".")[0] + ".local.",
            )
            self.zeroconf = Zeroconf()
            self.zeroconf.register_service(self.service, allow_name_change=True)
            LOGGER.info("Bonjour advertised at %s:%s", address, self.port)
        except (OSError, ValueError) as exc:
            LOGGER.warning("Bonjour discovery disabled: %s", exc)
            self.stop()

    def stop(self) -> None:
        if self.zeroconf:
            try:
                if self.service:
                    self.zeroconf.unregister_service(self.service)
            finally:
                self.zeroconf.close()
                self.zeroconf = None
                self.service = None
