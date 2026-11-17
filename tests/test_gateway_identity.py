"""Identity probing without UDP, using real session envelope validation."""

import unittest
from unittest.mock import Mock, patch
from uuid import UUID

from custom_components.zehnder_connectbox.client import ConnectBoxClient
from custom_components.zehnder_connectbox.protocol import (
    Frame,
    Operation,
    ProtocolError,
)


class GatewayIdentityTests(unittest.TestCase):
    def make_transport(self, source, result):
        transport = Mock()
        replies = []

        def reply_to(request):
            frame, remaining = Frame.decode(request)
            self.assertFalse(remaining)
            operation = Operation.decode(frame.operation)
            self.assertEqual(operation.type, 18)
            self.assertEqual(frame.body, b"")
            response = Frame(
                source=source,
                destination=frame.source,
                operation=Operation(
                    type=68, reference=operation.reference, result=result
                ).encode(),
                body=b"",
            )
            replies.append(response.encode())

        transport.write.side_effect = reply_to
        transport.read.side_effect = lambda: replies.pop(0)
        return transport

    def test_matching_gateway_identity_accepts_even_an_unpaired_request_refusal(self):
        for result in (0, 7):
            with self.subTest(result=result):
                transport = self.make_transport(UUID(int=1), result)
                with patch(
                    "custom_components.zehnder_connectbox.client.ConnectBoxTransport",
                    return_value=transport,
                ):
                    ConnectBoxClient.check_gateway_identity("test.invalid", UUID(int=1))
                transport.close.assert_called_once()
                transport.write.assert_called_once()

    def test_other_gateway_is_rejected_even_when_it_refuses_the_request(self):
        transport = self.make_transport(UUID(int=9), 7)
        with patch(
            "custom_components.zehnder_connectbox.client.ConnectBoxTransport",
            return_value=transport,
        ):
            with self.assertRaises(ProtocolError):
                ConnectBoxClient.check_gateway_identity("test.invalid", UUID(int=1))
        transport.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
