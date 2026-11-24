"""KNX Data Secure of the editor's own connection for the group communication.

A connection that monitors and operates the bus takes every group telegram as it comes:

- Plain telegrams are never discarded, also not for a group address it holds a key for (one can
  be secured in one security domain and in the clear in another, and a connection can't tell
  from which side a telegram comes).
- Secured telegrams are decrypted with any key there is, also from a sender it does not know
  yet; the Sequence Numbers of a sender count from its first telegram.
- It sends secured only on the group addresses given as ``sent_secured`` (the project's that are
  secured where the connection is), in the clear on the others.

xknx's :class:`DataSecure` instead discards plain telegrams for every group address it holds a
key for, and secured ones of senders missing from its table, and sends secured on all of them.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from xknx.cemi.cemi_frame import CEMILData
from xknx.secure.data_secure import DataSecure
from xknx.telegram import GroupAddress, IndividualAddress
from xknx.telegram.apci import SecureAPDU


class ConnectionDataSecure(DataSecure):
    """:class:`DataSecure` for a connection monitoring and operating the bus (see the module
    docstring); ``sent_secured`` None sends secured on every group address with a key."""

    __slots__ = ("_sent_secured",)

    def __init__(
        self,
        *,
        group_key_table: dict[GroupAddress, bytes],
        individual_address_table: dict[IndividualAddress, int],
        sent_secured: set[GroupAddress] | None = None,
        last_sequence_number_sending: int | None = None,
    ) -> None:
        super().__init__(
            group_key_table=group_key_table,
            individual_address_table=individual_address_table,
            last_sequence_number_sending=last_sequence_number_sending,
        )
        self._sent_secured = (
            frozenset(group_key_table)
            if sent_secured is None
            else frozenset(sent_secured) & frozenset(group_key_table)
        )

    @property
    def sent_secured(self) -> frozenset[GroupAddress]:
        """The group addresses sent secured."""
        return self._sent_secured

    def received_cemi(self, cemi_data: CEMILData) -> CEMILData:
        """Handle a received frame: a plain one as it is, a secured one decrypted."""
        if not isinstance(cemi_data.payload, SecureAPDU):
            return cemi_data
        return super().received_cemi(cemi_data)

    @contextmanager
    def check_sequence_number(
        self, source_address: IndividualAddress, received_sequence_number: int
    ) -> Generator[None]:
        """Check the Sequence Number of a secured frame: a sender not known yet is taken and
        counts from it on, a known one must exceed its last valid Sequence Number."""
        if source_address not in self._individual_address_table:
            yield
            self._individual_address_table[source_address] = received_sequence_number
            return
        with super().check_sequence_number(source_address, received_sequence_number):
            yield

    def outgoing_cemi(self, cemi_data: CEMILData) -> CEMILData:
        """Handle a frame to send: secured to a group address of ``sent_secured``, else as it
        is."""
        if (
            isinstance(cemi_data.dst_addr, GroupAddress)
            and cemi_data.dst_addr not in self._sent_secured
        ):
            return cemi_data
        return super().outgoing_cemi(cemi_data)
