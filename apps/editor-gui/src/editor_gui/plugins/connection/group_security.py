"""KNX Data Secure of the editor's own connection for the project's group communication.

A group address can be secured in one security domain and in the clear in another, where a
Security Proxy translates between the two, or where the project has no coupler for a line. A
connection can't tell from which side a telegram comes, so it takes the telegrams of such a group
address in the clear too, instead of discarding them as xknx does for every group address it
holds a key for. Secured telegrams are still decrypted and checked, and the connection still sends
secured.
"""

from __future__ import annotations

from xknx.cemi.cemi_frame import CEMILData
from xknx.secure.data_secure import DataSecure
from xknx.telegram import GroupAddress, IndividualAddress
from xknx.telegram.apci import SecureAPDU


class ConnectionDataSecure(DataSecure):
    """:class:`DataSecure` that also takes the telegrams of the group addresses in
    ``plain_too`` in the clear."""

    __slots__ = ("_plain_too",)

    def __init__(
        self,
        *,
        group_key_table: dict[GroupAddress, bytes],
        individual_address_table: dict[IndividualAddress, int],
        plain_too: set[GroupAddress] | None = None,
        last_sequence_number_sending: int | None = None,
    ) -> None:
        super().__init__(
            group_key_table=group_key_table,
            individual_address_table=individual_address_table,
            last_sequence_number_sending=last_sequence_number_sending,
        )
        self._plain_too = frozenset(plain_too or ())

    @property
    def plain_too(self) -> frozenset[GroupAddress]:
        """The secured group addresses whose telegrams are taken in the clear too."""
        return self._plain_too

    def received_cemi(self, cemi_data: CEMILData) -> CEMILData:
        """Handle a received frame: a plain one to a group address of ``plain_too`` as it is,
        everything else as :class:`DataSecure` does."""
        if (
            not isinstance(cemi_data.payload, SecureAPDU)
            and isinstance(cemi_data.dst_addr, GroupAddress)
            and cemi_data.dst_addr in self._plain_too
        ):
            return cemi_data
        return super().received_cemi(cemi_data)
