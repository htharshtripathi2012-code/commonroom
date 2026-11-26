"""Discover the private IPv4 address selected for LAN traffic."""

import ipaddress
import socket


_ROUTE_PROBE = ("192.0.2.1", 80)
_PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)


class LANAddressError(RuntimeError):
    """Raised when a usable private LAN IPv4 address cannot be discovered."""


def get_lan_ipv4() -> str:
    """Return the private IPv4 chosen for LAN traffic, or raise LANAddressError.

    The UDP socket is only connected to a documentation-only address to ask the
    local networking stack which interface it would use. No packet is sent.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(_ROUTE_PROBE)
            address = ipaddress.ip_address(probe.getsockname()[0])
    except OSError as exc:
        raise LANAddressError(
            "Could not determine a LAN IPv4 address; check that a network "
            "interface is connected and has a route."
        ) from exc

    if address.version != 4 or not any(
        address in network for network in _PRIVATE_NETWORKS
    ):
        raise LANAddressError(
            f"The selected interface address ({address}) is not an RFC 1918 "
            "private IPv4 address; connect to a private LAN and try again."
        )

    return str(address)


discover_lan_ipv4 = get_lan_ipv4


if __name__ == "__main__":
    print(get_lan_ipv4())
