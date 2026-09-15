"""Correlated request session for the ConnectBox protocol."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from .protobuf import ProtobufDecodeError
from .protocol import Frame, IncompleteFrame, Operation, ProtocolError
from .transport import ConnectBoxTransport, TransportError


class GatewayResponseError(ProtocolError):
    """Raised when a gateway rejects a request."""

    def __init__(self, result: int, description: str | None) -> None:
        self.result = result
        super().__init__(description or f"gateway returned result {result}")


@dataclass(frozen=True, slots=True)
class Response:
    """A correlated operation response."""

    operation: Operation
    body: bytes


class ConnectBoxSession:
    """A single blocking request session."""

    def __init__(
        self, transport: ConnectBoxTransport, gateway_uuid: UUID, app_uuid: UUID
    ) -> None:
        self._transport = transport
        self._gateway_uuid = gateway_uuid
        self._app_uuid = app_uuid
        self._reference = 0
        self._buffer = b""

    def request(
        self,
        request_type: int,
        confirmation_type: int,
        body: bytes = b"",
        *,
        timeout: float = 8.0,
    ) -> Response:
        """Send a request and wait for its matching confirmation."""
        self._reference = self._reference % 0xFFFFFFFF + 1
        reference = self._reference
        operation = Operation(type=request_type, reference=reference)
        frame = Frame(
            source=self._app_uuid,
            destination=self._gateway_uuid,
            operation=operation.encode(),
            body=body,
        )
        self._transport.set_timeout(timeout)
        self._transport.write(frame.encode())

        while True:
            received = self._read_frame()
            if received.source != self._gateway_uuid:
                raise ProtocolError("received a frame from an unexpected gateway")
            if received.destination != self._app_uuid:
                raise ProtocolError("received a frame for an unexpected application")
            try:
                response_operation = Operation.decode(received.operation)
            except ProtobufDecodeError as err:
                raise ProtocolError("invalid operation envelope") from err
            if response_operation.reference != reference:
                continue
            if response_operation.type != confirmation_type:
                raise ProtocolError("gateway returned an unexpected confirmation")
            if response_operation.result not in (None, 0):
                raise GatewayResponseError(
                    response_operation.result, response_operation.description
                )
            return Response(response_operation, received.body)

    def _read_frame(self) -> Frame:
        while True:
            try:
                frame, self._buffer = Frame.decode(self._buffer)
                return frame
            except IncompleteFrame:
                try:
                    received = self._transport.read()
                except TransportError as err:
                    raise ProtocolError(
                        "timed out or disconnected awaiting response"
                    ) from err
                if not received:
                    raise ProtocolError("gateway closed the connection")
                self._buffer += received
