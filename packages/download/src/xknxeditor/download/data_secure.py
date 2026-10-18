"""KNX Data Secure for point-to-point programming (Tool Key access).

Secures the management telegrams of a download run following KNX Standard
v3.0.0, 3/3/7 section 5 (the Secure Application Layer): S-A_Data in 5.3.1,
S-A_Sync in 5.3.2. Tool Key access (SCF ``Tool Access`` bit set) is the mode
used for device programming: the device accepts frames from any tool
address secured with the Tool Key, so no key table has to be provisioned.

The CCM building blocks (B0, Ctr0, the AES-CBC-MAC and AES-CTR steps) are laid
out here per KNX 3/3/7 sections 5.1.3.2 and Annex A, and the AES steps
themselves are taken from xknx's ``security_primitives``. The construction is
verified byte-for-byte against the worked examples in 3/3/7 Annex C
(C.1.1-C.1.4). It is deliberately *not* delegated to xknx's
``SecureData.init_from_plain_apdu``: that path only handles secure group
communication and gets two things wrong for point-to-point programming - it
puts only the SCF into the additional data ``A`` (the S-A_Sync request needs
``SCF | KNX Serial Number``) and its B0 TPCI/APCI octet is only correct for a
zero TPCI, so it cannot encode the connection-oriented frames the download uses.

Each B0/Ctr0 is built from a ``CEMILData`` derived from the telegram, so the
source/destination address, the address-type octet and the TPCI octet are
exactly what the frame carries on the wire. Both sides recompute the same
blocks from the frame's own fields.

Because the S-AL state on the device advances with the Sequence Number of an
accepted frame, the session's Sequence Numbers are first synchronised with an
S-A_Sync request before the first S-A_Data frame - the step the programmer performs
(DM_SecureSync, 3/5/2).
"""

from __future__ import annotations

import logging
import secrets
import time
from collections.abc import Sequence
from copy import copy
from datetime import datetime
from typing import Final, Protocol

from xknx.cemi.cemi_frame import CEMILData
from xknx.exceptions import ConversionError, DataSecureError, UnsupportedAPCIService
from xknx.secure.data_secure_asdu import (
    SecureData,
    SecurityAlgorithmIdentifier,
    SecurityALService,
    SecurityControlField,
)
from xknx.secure.security_primitives import (
    calculate_message_authentication_code_cbc,
    decrypt_ctr,
    encrypt_data_ctr,
)
from xknx.telegram import Telegram
from xknx.telegram.address import IndividualAddress
from xknx.telegram.apci import APCI, SecureAPDU

from .errors import DownloadError

logger = logging.getLogger(__name__)

# Secure APCI is 0x3F1 (3/3/7 Figure 101). In B0 its low 8 bits are one octet
# and its top 2 bits sit in the low 2 bits of the TPCI octet.
_SECURE_APCI_HIGH_BITS: Final = 0x03
_SECURE_APCI_LOW: Final = 0xF1
# The MAC is the 32 most significant bits of the CBC result (3/3/7 5.1.3.4.2).
_MAC_LENGTH: Final = 4
# Octets a Secure APDU adds around the plain APDU (3/3/7 5.1.3.5.1, Figure 104):
# 2 Secure APCI + 1 SCF + 6 SeqNr + 4 MAC. The plaintext APDU must be this much
# smaller than the device's wire APDU length so the secured frame still fits.
SECURE_APDU_OVERHEAD: Final = 13

# Security Control Fields of the session (3/3/7 5.1.3, Figure 106): Tool Access
# set, CCM with authentication and confidentiality.
DATA_SCF: Final = SecurityControlField(
    tool_access=True,
    algorithm=SecurityAlgorithmIdentifier.CCM_ENCRYPTION,
    system_broadcast=False,
    service=SecurityALService.S_A_DATA,
)
SYNC_REQ_SCF: Final = SecurityControlField(
    tool_access=True,
    algorithm=SecurityAlgorithmIdentifier.CCM_ENCRYPTION,
    system_broadcast=False,
    service=SecurityALService.S_A_SYNC_REQ,
)
SYNC_RES_SCF: Final = SecurityControlField(
    tool_access=True,
    algorithm=SecurityAlgorithmIdentifier.CCM_ENCRYPTION,
    system_broadcast=False,
    service=SecurityALService.S_A_SYNC_RES,
)

# Upper bound for a Sequence Number Sending (48-bit counter).
_SEQUENCE_NUMBER_MAX: Final = 0xFFFFFFFFFFFF
# Base timestamp for the initial (request) sequence number: milliseconds since
# 2018-01-05, the same base xknx uses for Data Secure - it keeps the tool's
# Sequence Number Sending in the same range as the devices' counters.
_SEQUENCE_NUMBER_INIT_TIMESTAMP: Final = datetime.fromisoformat(
    "2018-01-05T00:00:00+00:00"
).timestamp()


class SecureProgrammingError(DownloadError):
    """The secure programming session could not be established or verified."""


class CemiSecurer(Protocol):
    """The ``xknx.cemi_handler.data_secure`` hook interface.

    Both xknx's group ``DataSecure`` and :class:`ToolKeyCemiSecure` implement it,
    so a Tool-Key session can delegate the traffic it does not handle to a
    securer that was already installed.
    """

    def outgoing_cemi(self, cemi_data: CEMILData) -> CEMILData:
        """Secure or pass through an outgoing frame."""
        ...

    def received_cemi(self, cemi_data: CEMILData) -> CEMILData:
        """Unwrap or pass through an incoming frame."""
        ...


class DeviceSecurity:
    """Tool Key security material for programming one device.

    ``tool_key`` is the device's Tool Key (the FDSK for a device in factory
    state), as 16 raw octets or 32 hex digits (the form the keyring stores it
    in). ``alternative_keys`` are tried in order when the device does not answer
    the S-A_Sync with ``tool_key``: the FDSK is active again after a reset to
    default, and a Tool Key write may have reached the device without its
    response reaching the tool (3/5/2 4.1, Note 18).
    """

    __slots__ = ("address", "alternative_keys", "tool_key")

    def __init__(
        self,
        address: IndividualAddress,
        tool_key: bytes | str,
        alternative_keys: Sequence[bytes | str] = (),
    ) -> None:
        """Initialize with the target address and the Tool Key."""
        self.address = address
        self.tool_key = self._normalize_key(tool_key)
        keys = [self._normalize_key(key) for key in alternative_keys]
        self.alternative_keys = tuple(
            key
            for i, key in enumerate(keys)
            if key != self.tool_key and key not in keys[:i]
        )

    @property
    def keys(self) -> tuple[bytes, ...]:
        """The Tool Key followed by its alternatives."""
        return (self.tool_key, *self.alternative_keys)

    @staticmethod
    def _normalize_key(tool_key: bytes | str) -> bytes:
        if isinstance(tool_key, str):
            try:
                tool_key = bytes.fromhex(tool_key)
            except ValueError as exc:
                raise SecureProgrammingError(
                    f"tool key hex string cannot be decoded: {exc}"
                ) from exc
        if len(tool_key) != 16:
            raise SecureProgrammingError(
                "tool key must be 16 octets (32 hex digits), "
                f"got {len(tool_key)} octets"
            )
        return bytes(tool_key)


class _BlockFields:
    """The frame fields that go into a B0/Ctr0 block for one telegram.

    These are exactly the fields the receiver reconstructs from the frame it
    receives, so both sides compute the same blocks (3/3/7 5.1.3.2).
    """

    __slots__ = ("address_type", "destination", "source", "tpci_octet")

    def __init__(self, cemi: CEMILData) -> None:
        """Derive the block fields from a link-layer frame."""
        self.source = cemi.src_addr.to_knx()
        self.destination = cemi.dst_addr.to_knx()
        # AT = A000EEEEb: address type bit and extended-frame nibble only.
        self.address_type = cemi.address_type.to_knx() | cemi.flags.frame_format
        # The full TPCI octet as carried on the wire; its low 2 bits are 0 for a
        # data frame and become the top 2 bits of the Secure APCI in B0.
        self.tpci_octet = cemi.tpci.to_knx()

    @classmethod
    def from_telegram(
        cls, telegram: Telegram, *, source: IndividualAddress
    ) -> _BlockFields:
        """Derive the block fields from ``telegram`` as sent by ``source``."""
        return cls(CEMILData.init_from_telegram(telegram=telegram, src_addr=source))

    def block_0(self, nonce: bytes, payload_length: int) -> bytes:
        """Return B0 (3/3/7 Figure 100). ``nonce`` is SeqNr, or Random for S-A_Sync.res."""
        return (
            nonce
            + self.source
            + self.destination
            + bytes(
                (
                    0x00,
                    self.address_type,
                    self.tpci_octet | _SECURE_APCI_HIGH_BITS,
                    _SECURE_APCI_LOW,
                    0x00,
                    payload_length,
                )
            )
        )

    def counter_0(self, nonce: bytes) -> bytes:
        """Return Ctr0 (3/3/7 Figure 102). Octet 14 is 01h, [j] for Ctr0 is 00h."""
        return nonce + self.source + self.destination + b"\x00\x00\x00\x00\x01\x00"


def _encrypt(
    key: bytes,
    fields: _BlockFields,
    nonce: bytes,
    additional_data: bytes,
    payload: bytes,
) -> tuple[bytes, bytes]:
    """CCM-encrypt ``payload`` and return ``(cipher, mac)`` (3/3/7 Annex A)."""
    mac_cbc = calculate_message_authentication_code_cbc(
        key=key,
        additional_data=additional_data,
        payload=payload,
        block_0=fields.block_0(nonce, len(payload)),
    )[:_MAC_LENGTH]
    return encrypt_data_ctr(
        key=key,
        counter_0=fields.counter_0(nonce),
        mac_cbc=mac_cbc,
        payload=payload,
    )


def _decrypt(
    key: bytes,
    fields: _BlockFields,
    nonce: bytes,
    additional_data: bytes,
    cipher: bytes,
    mac: bytes,
) -> bytes:
    """CCM-decrypt ``cipher`` and verify the MAC, or raise (3/3/7 Annex A)."""
    plain, mac_tr = decrypt_ctr(
        key=key, counter_0=fields.counter_0(nonce), mac=mac, payload=cipher
    )
    mac_cbc = calculate_message_authentication_code_cbc(
        key=key,
        additional_data=additional_data,
        payload=plain,
        block_0=fields.block_0(nonce, len(plain)),
    )[:_MAC_LENGTH]
    if mac_cbc != mac_tr:
        raise SecureProgrammingError(
            "MAC verification failed - wrong Tool Key or tampered frame"
        )
    return plain


class SecureManagement:
    """Tool-Key state and CCM operations for one programming session.

    Secures outgoing S-A_Data frames and unwraps incoming ones, and drives the
    S-A_Sync exchange that establishes the Sequence Numbers before the first
    S-A_Data frame: the response's SeqNrRemote becomes the last valid value for
    the device and its SeqNrLocal the next Sequence Number Sending for the tool
    (3/3/7 5.3.2, Figure 109/110).

    It works on ``CEMILData`` frames, not telegrams, because in the
    connection-oriented mode used for programming the B0 block includes the TPCI octet
    (with the transport-layer sequence number), which is only final once the
    transport layer has built the frame. :class:`ToolKeyCemiSecure` installs
    this on the frame path where that is the case.
    """

    def __init__(
        self,
        *,
        device: DeviceSecurity,
        tool_address: IndividualAddress,
    ) -> None:
        """Initialize for the device's Tool Key.

        ``tool_address`` is the tool's own source individual address on the bus
        (e.g. ``xknx.current_address``); it is bound into the secured data like
        every other frame field, so it must be the address the frames actually
        carry.
        """
        self._device = device
        self._key = device.tool_key
        # The new Tool Key while its write is on its way: the device answers that write
        # already with it (3/5/1 6.3.10.4), and every later frame uses it (6.3.10.5).
        self._pending_key: bytes | None = None
        self._tool_address = tool_address
        self._next_sequence_number: int | None = None
        self._last_valid_remote: int | None = None
        self._sync_armed = False
        self._sync_challenge: bytes | None = None
        self._sync_sequence: int | None = None
        # The exact secured S-A_Sync request, cached so a transport-layer
        # retransmission of the trigger frame replays identical bytes instead of
        # being turned into an S-A_Data frame (3/3/7 5.3.2, Note 38).
        self._sync_request: SecureAPDU | None = None

    @property
    def active_key(self) -> bytes:
        """The key this session secures its frames with."""
        return self._key

    @property
    def device_sequence_number(self) -> int | None:
        """The Sequence Number the device sends its next secured frame with, as far as this
        session saw (``None`` before the S-A_Sync)."""
        if self._last_valid_remote is None:
            return None
        return self._last_valid_remote + 1

    def use_key(self, key: bytes) -> None:
        """Secure the session with ``key`` from the next S-A_Sync on."""
        self._key = key
        self._pending_key = None

    def change_tool_key(self, key: bytes) -> None:
        """Expect the device to switch to the Tool Key ``key`` with the next request.

        That request (the Tool Key write) is still secured with the current key;
        the device's response and all later frames use ``key``.
        """
        self._pending_key = key

    @property
    def synchronized(self) -> bool:
        """Whether the session's sequence numbers are synchronized."""
        return self._next_sequence_number is not None

    @property
    def sync_armed(self) -> bool:
        """Whether the next outgoing frame is to be turned into an S-A_Sync request."""
        return self._sync_armed

    # ------------------------------------------------------------------ sync

    def arm_sync(self) -> None:
        """Prepare a fresh challenge for the next S-A_Sync request.

        Clears any previous session state, so a re-sync (e.g. after a device
        restart) starts from a clean slate. The next outgoing frame to the
        device is turned into the S-A_Sync request by
        :meth:`build_sync_request_cemi`, and stays armed (replaying the same
        request for retransmissions) until the response is decoded.
        """
        self._next_sequence_number = None
        self._last_valid_remote = None
        self._sync_armed = True
        self._sync_request = None
        self._sync_challenge = secrets.token_bytes(6)
        # The request's SeqNrLocal is the sequence number the tool *assumes* it
        # will send next (3/3/7 5.3.2); the response corrects it.
        self._sync_sequence = _initial_sequence_number()

    def build_sync_request_cemi(self, cemi: CEMILData) -> CEMILData:
        """Replace ``cemi``'s payload with the S-A_Sync request (3/3/7 Figure 109).

        A = SCF | KNX Serial Number (0 for point-to-point), P = Challenge. The
        KNX Serial Number travels in plain text on the wire between the
        Sequence Number and the encrypted Challenge. The request is built once
        and cached; a retransmission of the same trigger frame replays the
        identical Secure APDU (a fresh MAC over a new SeqNr would be rejected by
        the device), matching that an S-A_Sync request does not advance the
        Sequence Number Sending (3/3/7 5.3.2, Note 38).
        """
        if self._sync_challenge is None or self._sync_sequence is None:
            raise SecureProgrammingError("S-A_Sync is not armed")
        secured = copy(cemi)
        if self._sync_request is not None:
            secured.payload = self._sync_request
            return secured
        serial_number = b"\x00" * 6
        nonce = self._sync_sequence.to_bytes(6, "big")
        cipher, mac = _encrypt(
            key=self._key,
            fields=_BlockFields(secured),
            nonce=nonce,
            additional_data=SYNC_REQ_SCF.to_knx() + serial_number,
            payload=self._sync_challenge,
        )
        self._sync_request = SecureAPDU(
            scf=SYNC_REQ_SCF,
            secured_data=SecureData(
                sequence_number_bytes=nonce,
                secured_apdu=serial_number + cipher,
                message_authentication_code=mac,
            ),
        )
        secured.payload = self._sync_request
        return secured

    def decode_sync_response_cemi(self, cemi: CEMILData) -> bool:
        """Consume an S-A_Sync response and store the synchronized state.

        Returns whether the frame was a valid S-A_Sync response for this
        session. The response's nonce is the device's Random value, recovered
        by XOR-ing the plain-text ``Challenge XOR Random`` field with our own
        challenge (3/3/7 5.3.2).
        """
        payload = cemi.payload
        if not isinstance(payload, SecureAPDU):
            return False
        if not _is_tool_scheme(payload.scf, SecurityALService.S_A_SYNC_RES):
            return False
        if self._sync_challenge is None:
            # Already synchronized (e.g. a duplicated response); ignore.
            return self._next_sequence_number is not None
        challenge_xor_random = payload.secured_data.sequence_number_bytes
        random = bytes(
            a ^ b
            for a, b in zip(challenge_xor_random, self._sync_challenge, strict=True)
        )
        try:
            plain = _decrypt(
                key=self._key,
                fields=_BlockFields(cemi),
                nonce=random,
                additional_data=payload.scf.to_knx(),
                cipher=payload.secured_data.secured_apdu,
                mac=payload.secured_data.message_authentication_code,
            )
        except SecureProgrammingError:
            logger.warning("S-A_Sync response failed MAC verification; ignoring")
            return False
        if len(plain) != 12:
            return False
        sequence_number_remote = int.from_bytes(plain[:6], "big")
        sequence_number_local = int.from_bytes(plain[6:12], "big")
        # Both are "next valid" Sequence Numbers and are never 0 (3/3/7 5.3.1;
        # a device rejects them, and SeqNrRemote 0 would make last_valid_remote
        # negative and admit a replayed frame with SeqNr 0). SeqNrLocal is what
        # the device expects from the tool next (Note 43); SeqNrRemote is the
        # device's own next Sequence Number Sending, so its last valid value is
        # one less (Note 45).
        if sequence_number_local == 0 or sequence_number_remote == 0:
            raise SecureProgrammingError(
                "S-A_Sync response returned sequence number 0, which is invalid"
            )
        self._next_sequence_number = sequence_number_local
        self._last_valid_remote = sequence_number_remote - 1
        self._sync_armed = False
        self._sync_request = None
        self._sync_challenge = None
        self._sync_sequence = None
        logger.info(
            "data secure sync complete: tool sends from %#014x, "
            "device answers from %#014x",
            sequence_number_local,
            sequence_number_remote,
        )
        return True

    # ------------------------------------------------------------- wrapping

    def wrap_cemi(self, cemi: CEMILData) -> CEMILData:
        """Return a copy of ``cemi`` with its APDU secured as an S-A_Data frame.

        Must be called after synchronization. Only the APDU is replaced by a
        Secure APDU; the frame's addresses and TPCI drive the B0/Ctr0 blocks.
        """
        if self._next_sequence_number is None:
            raise SecureProgrammingError(
                "data secure session not synchronized - run S-A_Sync first"
            )
        if not isinstance(cemi.payload, APCI):
            raise SecureProgrammingError(
                "cannot secure a control frame (no APDU to protect)"
            )
        sequence_number = self._next_sequence_number
        if sequence_number > _SEQUENCE_NUMBER_MAX:
            raise SecureProgrammingError(
                "Sequence Number Sending exhausted (48-bit counter overflow); "
                "re-synchronize the session"
            )
        self._next_sequence_number += 1
        secured = copy(cemi)
        nonce = sequence_number.to_bytes(6, "big")
        cipher, mac = _encrypt(
            key=self._key,
            fields=_BlockFields(secured),
            nonce=nonce,
            additional_data=DATA_SCF.to_knx(),
            payload=bytes(cemi.payload.to_knx()),
        )
        secured.payload = SecureAPDU(
            scf=DATA_SCF,
            secured_data=SecureData(
                sequence_number_bytes=nonce,
                secured_apdu=cipher,
                message_authentication_code=mac,
            ),
        )
        return secured

    def unwrap_cemi(self, cemi: CEMILData) -> CEMILData:
        """Return a copy of ``cemi`` with its S-A_Data APDU decrypted and verified.

        Raises :class:`SecureProgrammingError` if the frame is not a Tool Key
        secured S-A_Data frame, the MAC does not verify, or the sequence number
        is not higher than the last valid value for the device.
        """
        if self._last_valid_remote is None:
            raise SecureProgrammingError(
                "data secure session not synchronized - cannot decrypt response"
            )
        payload = cemi.payload
        if not isinstance(payload, SecureAPDU):
            raise SecureProgrammingError(
                f"expected a secure APDU in the response, got {payload!r} - the "
                "device is not using KNX Data Secure (Tool Key access)"
            )
        if not _is_tool_scheme(payload.scf, SecurityALService.S_A_DATA):
            raise SecureProgrammingError(
                "response secured with an unexpected scheme: "
                f"service {payload.scf.service!r}, "
                f"algorithm {payload.scf.algorithm!r}, "
                f"tool access {payload.scf.tool_access}"
            )
        sequence_number = int.from_bytes(
            payload.secured_data.sequence_number_bytes, "big"
        )
        if sequence_number <= self._last_valid_remote:
            raise SecureProgrammingError(
                f"device sequence number too low: {sequence_number:#014x} "
                f"(last valid {self._last_valid_remote:#014x})"
            )
        plain = self._decrypt_data(cemi, payload)
        # The plain APDU starts with the 000000b prefix before the 10-bit APCI
        # (3/3/7 Figure 103); its top 6 bits must be zero. APCI.from_knx masks
        # them off, so check here to reject an authenticated but malformed APDU.
        if not plain or plain[0] & 0xFC:
            raise SecureProgrammingError(
                "decrypted APDU has a non-zero reserved prefix"
            )
        try:
            decoded = APCI.from_knx(plain)
        except (ConversionError, UnsupportedAPCIService) as exc:
            raise SecureProgrammingError(
                f"decrypted APDU could not be parsed: {exc}"
            ) from exc
        self._last_valid_remote = sequence_number
        plain_cemi = copy(cemi)
        plain_cemi.payload = decoded
        return plain_cemi

    def _decrypt_data(self, cemi: CEMILData, payload: SecureAPDU) -> bytes:
        """Decrypt an S-A_Data frame with the session key, or with the pending new Tool Key,
        which then becomes the session key."""

        def decrypt(key: bytes) -> bytes:
            return _decrypt(
                key=key,
                fields=_BlockFields(cemi),
                nonce=payload.secured_data.sequence_number_bytes,
                additional_data=payload.scf.to_knx(),
                cipher=payload.secured_data.secured_apdu,
                mac=payload.secured_data.message_authentication_code,
            )

        pending = self._pending_key
        if pending is not None:
            try:
                plain = decrypt(pending)
            except SecureProgrammingError:
                pass
            else:
                logger.info("data secure: the device uses the new Tool Key")
                self.use_key(pending)
                return plain
        return decrypt(self._key)


class ToolKeyCemiSecure:
    """CEMI-layer securer for one device, for ``xknx.cemi_handler.data_secure``.

    Installed for the duration of a secure programming session. It secures the
    frames to the target device with the Tool Key and unwraps the device's
    secured answers, and passes every other frame through unchanged. It mirrors
    how xknx installs its own group Data Secure on the same hook, so the B0/Ctr0
    blocks see the final transport-layer sequence number of each frame.
    """

    def __init__(
        self,
        management: SecureManagement,
        device: IndividualAddress,
        previous: CemiSecurer | None = None,
    ) -> None:
        """Initialize for ``management``'s Tool Key session and ``device``.

        ``previous`` is the securer that was installed before this one (e.g. a
        group Data Secure); traffic not addressed to ``device`` is delegated to
        it so concurrent secure group communication keeps working.
        """
        self._management = management
        self._device = device
        self._previous = previous

    def outgoing_cemi(self, cemi_data: CEMILData) -> CEMILData:
        """Secure an outgoing frame to the device; delegate everything else."""
        if cemi_data.dst_addr != self._device:
            return (
                self._previous.outgoing_cemi(cemi_data)
                if self._previous is not None
                else cemi_data
            )
        payload = cemi_data.payload
        # Control frames (T_Connect/T_Ack/T_Disconnect) carry no APDU, and an
        # already-secure APDU must not be wrapped again.
        if not isinstance(payload, APCI) or isinstance(payload, SecureAPDU):
            return cemi_data
        if self._management.sync_armed:
            return self._management.build_sync_request_cemi(cemi_data)
        return self._management.wrap_cemi(cemi_data)

    def received_cemi(self, cemi_data: CEMILData) -> CEMILData:
        """Unwrap a secured frame from the device; delegate everything else.

        A frame that fails verification is reported as :class:`DataSecureError`,
        the contract xknx's CEMI handler expects on this hook: it logs the
        failure and drops the frame instead of letting it crash the receive
        loop. The pending request then times out, surfacing the failure.
        """
        if cemi_data.src_addr != self._device:
            return (
                self._previous.received_cemi(cemi_data)
                if self._previous is not None
                else cemi_data
            )
        payload = cemi_data.payload
        if isinstance(payload, SecureAPDU):
            try:
                if payload.scf.service is SecurityALService.S_A_SYNC_RES:
                    # Consume the sync state; leave the frame as-is so the request
                    # that triggered the sync still gets a (secure) response telegram.
                    self._management.decode_sync_response_cemi(cemi_data)
                    return cemi_data
                return self._management.unwrap_cemi(cemi_data)
            except SecureProgrammingError as exc:
                raise DataSecureError(str(exc)) from exc
        # In an established Tool-Key session every application APDU from the device must be secured.
        # A plaintext (non-secure) APDU from the target is either a fault or a frame a bus attacker
        # injected to satisfy a pending request - e.g. a forged A_Memory read-back that falsifies a
        # preflight, or a forged table-reference response that then steers a subsequent genuinely
        # authenticated write to an attacker-chosen address. Reject it (xknx drops the frame and the
        # request times out) instead of trusting unauthenticated data. Control frames
        # (T_Connect/T_Ack/T_Disconnect) carry no APDU and pass through unchanged.
        if isinstance(payload, APCI):
            raise DataSecureError(
                f"unsecured APDU from {self._device} during a secure session; dropping it "
                f"(possible spoofed response)"
            )
        return cemi_data


def _is_tool_scheme(scf: SecurityControlField, service: SecurityALService) -> bool:
    """Whether ``scf`` is Tool Key access with CCM for the given S-AL service."""
    return (
        scf.service is service
        and scf.algorithm is SecurityAlgorithmIdentifier.CCM_ENCRYPTION
        and scf.tool_access
    )


def sequence_number_timestamp() -> int:
    """The current timestamp of the KNX Data Security specifications: milliseconds since their
    publication on 2018-01-05 (3/5/3 1.5.4.1)."""
    return _initial_sequence_number()


def _initial_sequence_number() -> int:
    """Return the initial (request) sequence number for S-A_Sync."""
    return min(
        int((time.time() - _SEQUENCE_NUMBER_INIT_TIMESTAMP) * 1000),
        _SEQUENCE_NUMBER_MAX,
    )
