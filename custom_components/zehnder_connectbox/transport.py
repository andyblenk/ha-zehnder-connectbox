"""TLS transport scoped to the legacy profile required by ConnectBox."""

from __future__ import annotations

import hashlib
import hmac
import socket
import string

from tlslite import HandshakeSettings, TLSConnection
from tlslite.errors import TLSError


class TransportError(ConnectionError):
    """Raised when the local ConnectBox transport fails."""


class CertificateMismatchError(TransportError):
    """Raised when a previously trusted gateway certificate changed."""


def normalize_fingerprint(value: str) -> str:
    """Normalize and validate a SHA-256 certificate fingerprint."""
    normalized = value.lower().replace(":", "").replace(" ", "")
    if len(normalized) != 64 or any(
        char not in string.hexdigits for char in normalized
    ):
        raise ValueError("invalid SHA-256 certificate fingerprint")
    return normalized


def _handshake_settings() -> HandshakeSettings:
    """Return a TLS 1.2 configuration limited to this gateway connection."""
    settings = HandshakeSettings()
    settings.minVersion = (3, 3)
    settings.maxVersion = (3, 3)
    settings.cipherNames = ["aes256gcm", "aes128gcm", "aes256", "aes128"]
    settings.macNames = ["aead", "sha384", "sha256", "sha"]
    settings.keyExchangeNames = ["ecdhe_ecdsa", "ecdhe_rsa", "rsa"]
    settings.eccCurves = ["secp256r1", "secp384r1", "secp521r1"]
    settings.keyShares = ["secp256r1"]
    settings.rsaSigHashes = ["sha256", "sha1", "sha384", "sha512"]
    settings.rsaSchemes = ["pkcs1"]
    settings.ecdsaSigHashes = ["sha256", "sha1", "sha384", "sha512"]
    settings.more_sig_schemes = []
    settings.useEncryptThenMAC = False
    settings.usePaddingExtension = False
    settings.use_heartbeat_extension = False
    settings.certificate_compression_send = []
    settings.certificate_compression_receive = []
    return settings


class ConnectBoxTransport:
    """A blocking TLS stream with trust-on-first-use fingerprint support."""

    def __init__(
        self,
        host: str,
        *,
        port: int,
        expected_fingerprint: str | None,
        connect_timeout: float = 5.0,
        io_timeout: float = 8.0,
    ) -> None:
        self._host = host
        self._port = port
        self._expected_fingerprint = (
            normalize_fingerprint(expected_fingerprint)
            if expected_fingerprint is not None
            else None
        )
        self._connect_timeout = connect_timeout
        self._io_timeout = io_timeout
        self._socket: socket.socket | None = None
        self._tls: TLSConnection | None = None
        self._fingerprint: str | None = None

    @property
    def certificate_fingerprint(self) -> str:
        """Return the certificate fingerprint for an established connection."""
        if self._fingerprint is None:
            raise TransportError("connection is not established")
        return self._fingerprint

    def connect(self) -> None:
        """Open and verify a local TLS connection."""
        if self._tls is not None:
            return
        raw_socket: socket.socket | None = None
        tls: TLSConnection | None = None
        try:
            raw_socket = socket.create_connection(
                (self._host, self._port), timeout=self._connect_timeout
            )
            raw_socket.settimeout(self._io_timeout)
            tls = TLSConnection(raw_socket)
            tls.handshakeClientCert(settings=_handshake_settings(), serverName=None)

            chain = tls.session.serverCertChain
            if chain is None or not chain.x509List:
                raise TransportError("gateway did not provide a certificate")
            certificate = bytes(chain.x509List[0].writeBytes())
            fingerprint = hashlib.sha256(certificate).hexdigest()
            if self._expected_fingerprint is not None and not hmac.compare_digest(
                fingerprint, self._expected_fingerprint
            ):
                raise CertificateMismatchError("gateway certificate changed")

            self._socket = raw_socket
            self._tls = tls
            self._fingerprint = fingerprint
        except Exception as err:
            if tls is not None:
                try:
                    tls.close()
                except Exception:  # noqa: BLE001
                    if raw_socket is not None:
                        raw_socket.close()
            elif raw_socket is not None:
                raw_socket.close()
            if isinstance(err, TransportError):
                raise
            if isinstance(err, (OSError, TLSError, TimeoutError)):
                raise TransportError(
                    "unable to establish a local gateway connection"
                ) from err
            raise

    def set_timeout(self, timeout: float) -> None:
        """Set a bounded timeout for subsequent I/O."""
        if self._socket is None:
            raise TransportError("connection is not established")
        self._socket.settimeout(timeout)

    def read(self) -> bytes:
        """Read at least one byte from the TLS stream."""
        if self._tls is None:
            raise TransportError("connection is not established")
        try:
            return bytes(self._tls.read(max=16384, min=1))
        except (OSError, TLSError, TimeoutError) as err:
            raise TransportError("gateway read failed") from err

    def write(self, data: bytes) -> None:
        """Write one complete frame."""
        if self._tls is None:
            raise TransportError("connection is not established")
        try:
            self._tls.write(data)
        except (OSError, TLSError, TimeoutError) as err:
            raise TransportError("gateway write failed") from err

    def close(self) -> None:
        """Close the connection without raising during cleanup."""
        tls, raw_socket = self._tls, self._socket
        self._tls = None
        self._socket = None
        self._fingerprint = None
        if tls is not None:
            try:
                tls.close()
            except Exception:  # noqa: BLE001
                if raw_socket is not None:
                    raw_socket.close()
        elif raw_socket is not None:
            raw_socket.close()
