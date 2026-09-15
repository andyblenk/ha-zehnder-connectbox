"""Bounded local UDP discovery for Zehnder ConnectBox gateways."""

from __future__ import annotations

import socket
import time
from uuid import UUID

from .const import DEFAULT_PORT, DISCOVERY_TIMEOUT
from .models import DiscoveredGateway
from .protobuf import ProtobufDecodeError, bytes_value, decode_fields, uint_value

DISCOVERY_REQUEST = b"\x0a\x00"
MAX_DISCOVERY_RESPONSE = 4096


def discover_gateways(
    targets: list[str], timeout: float = DISCOVERY_TIMEOUT
) -> dict[UUID, DiscoveredGateway]:
    """Discover gateways from one or more broadcast or unicast addresses."""
    if not targets:
        return {}

    gateways: dict[UUID, DiscoveredGateway] = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_socket:
        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        udp_socket.bind(("", 0))
        for target in set(targets):
            try:
                udp_socket.sendto(DISCOVERY_REQUEST, (target, DEFAULT_PORT))
            except OSError:
                continue

        deadline = time.monotonic() + timeout
        while (remaining := deadline - time.monotonic()) > 0:
            udp_socket.settimeout(remaining)
            try:
                payload, sender = udp_socket.recvfrom(MAX_DISCOVERY_RESPONSE)
            except TimeoutError:
                break
            except OSError:
                continue
            gateway = _decode_response(payload, sender[0])
            if gateway is not None:
                gateways[gateway.gateway_uuid] = gateway
    return gateways


def _decode_response(payload: bytes, host: str) -> DiscoveredGateway | None:
    """Validate and decode the public identity fields in a discovery response."""
    try:
        outer = decode_fields(payload)
        nested = bytes_value(outer, 2)
        if nested is None:
            return None
        fields = decode_fields(nested)
        raw_uuid = bytes_value(fields, 2)
        if raw_uuid is None or len(raw_uuid) != 16:
            return None
        raw_name = bytes_value(fields, 4)
        raw_serial = bytes_value(fields, 5)
        return DiscoveredGateway(
            host=host,
            gateway_uuid=UUID(bytes=raw_uuid),
            name=raw_name.decode(errors="replace") if raw_name else None,
            serial_number=(raw_serial.decode(errors="replace") if raw_serial else None),
            gateway_type=uint_value(fields, 6),
            protocol_version=uint_value(fields, 3),
        )
    except (ProtobufDecodeError, ValueError):
        return None
