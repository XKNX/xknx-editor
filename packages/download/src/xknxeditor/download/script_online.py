"""The ``online`` object passed to online Button handlers."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from typing import TYPE_CHECKING, Any, cast

from xknxeditor.prod.script.errors import (
    COR_E_EXCEPTION,
    COR_E_INVALIDOPERATION,
    E_NOINTERFACE,
    E_NOTIMPL,
    E_POINTER,
    AbortRequested,
    HostError,
)
from xknxeditor.prod.script.texts import COAP_NOT_SUPPORTED, NOT_CONNECTED, dotnet_text

from .programmer import (
    DEFAULT_MAX_APDU_LENGTH,
    MAX_COMMUNICATION_APDU_LENGTH,
    PID_OBJECT_TYPE,
    DeviceProgrammer,
)
from .session import apdu_overhead, management_session

if TYPE_CHECKING:
    from xknx import XKNX
    from xknx.telegram import IndividualAddress

    from xknxeditor.prod.script.sandbox import AbortToken, HostFunction

    from .data_secure import DeviceSecurity
    from .programmer import ConnectionManager

# APDU length reported before connect(): the length every KNX device supports.
INTERFACE_MAX_APDU_LENGTH = DEFAULT_MAX_APDU_LENGTH
DEVICE_MISMATCH = "The device does not match: mask version {0:04X} instead of {1:04X}."


class OnlineSession:
    """Management session of one handler run; ``connect()`` will open the transport
    (with Data Secure sync for a Tool Key), identify the device and negotiate the APDU length
    as the minimum of the device's, the interface's and the communication limit."""

    def __init__(
        self,
        xknx: XKNX,
        address: IndividualAddress,
        *,
        security: DeviceSecurity | None = None,
        connectionless: bool = False,
        mask_version: int | None = None,
        interface_max_apdu_length: int | None = None,
        locale: str | None = None,
        object_types: dict[int, int] | None = None,
        memory_services: tuple[bool, bool] = (False, False),
    ) -> None:
        """``memory_services``: whether the device takes the extended memory services, and
        whether a failed one falls back to the standard ones (see
        ``procedure.memory_services``)."""
        self._xknx = xknx
        self._memory_services = memory_services
        self._address = address
        self._security = security
        self._connectionless = connectionless
        self._mask_version = mask_version
        self._interface_max = interface_max_apdu_length
        self._locale = locale
        self._object_types = dict(object_types or {})
        self._descriptor: int | None = None
        self._manager: ConnectionManager | None = None
        self._programmer: DeviceProgrammer | None = None

    @property
    def connected(self) -> bool:
        return self._programmer is not None

    @property
    def programmer(self) -> DeviceProgrammer:
        if self._programmer is None:
            raise HostError(NOT_CONNECTED, number=COR_E_INVALIDOPERATION)
        return self._programmer

    def device_descriptor(self) -> int:
        """The device descriptor read by ``connect()``."""
        if self._descriptor is None:
            raise HostError(
                dotnet_text("null_reference", self._locale), number=E_POINTER
            )
        return self._descriptor

    async def connect(self) -> None:
        if self._programmer is not None:
            return
        manager = management_session(
            self._xknx,
            self._address,
            self._security,
            connectionless=self._connectionless,
        )
        self._manager = manager
        try:
            connection = await manager.open()
            programmer = DeviceProgrammer(
                connection, apdu_overhead=apdu_overhead(self._security)
            )
            programmer.extended_memory, programmer.extended_memory_fallback = (
                self._memory_services
            )
            descriptor = await programmer.read_device_descriptor()
            if self._mask_version is not None and descriptor != self._mask_version:
                raise HostError(DEVICE_MISMATCH.format(descriptor, self._mask_version))
            limit = MAX_COMMUNICATION_APDU_LENGTH
            if self._interface_max is not None:
                limit = min(limit, self._interface_max)
            device_max = await programmer.read_max_apdu_length()
            programmer.max_apdu_length = max(
                DEFAULT_MAX_APDU_LENGTH, min(device_max, limit)
            )
        except BaseException:
            await self.disconnect()
            raise
        self._programmer = programmer
        self._descriptor = descriptor

    async def disconnect(self) -> None:
        manager, self._manager, self._programmer = self._manager, None, None
        self._descriptor = None
        if manager is not None:
            await manager.close()

    def max_apdu_length(self) -> int:
        if self._programmer is not None:
            return self._programmer.max_apdu_length
        if self._interface_max is not None:
            return self._interface_max
        return INTERFACE_MAX_APDU_LENGTH

    @property
    def locale(self) -> str | None:
        return self._locale

    async def locate_interface_object(self, object_type: int, instance: int) -> int:
        """Index of the ``instance``-th (from 1) interface object of a type: the objects the
        device type declares first, then the device's further objects."""
        programmer = self.programmer
        seen = 0
        for index in sorted(self._object_types):
            if self._object_types[index] == object_type:
                seen += 1
                if seen == instance:
                    return index
        index = max(self._object_types, default=-1) + 1
        while index <= 0xFF:
            data = await programmer.read_property(index, PID_OBJECT_TYPE)
            if len(data) < 2:
                break
            if int.from_bytes(data[:2], "big") == object_type:
                seen += 1
                if seen == instance:
                    return index
            index += 1
        raise HostError(
            dotnet_text("resource_not_available", self._locale), number=COR_E_EXCEPTION
        )

    async def restart(self) -> None:
        await self.programmer.restart()
        await self.disconnect()


def _dotnet_type(value: Any) -> str:
    if isinstance(value, bool):
        return "System.Boolean"
    if isinstance(value, int):
        return "System.Int32"
    if isinstance(value, float):
        return "System.Double"
    if isinstance(value, str):
        return "System.String"
    return "System.Object"


class OnlineHost:
    """Host functions of the ``online`` object. ``run`` schedules a coroutine on the xknx event
    loop; calls block the script until it completes and stay abortable."""

    def __init__(
        self,
        session: OnlineSession,
        run: Callable[[Coroutine[Any, Any, Any]], Future[Any]],
        abort: AbortToken,
    ) -> None:
        self._session = session
        self._run = run
        self._abort = abort

    def _wait[T](self, coro: Coroutine[Any, Any, T]) -> T:
        future: Future[T] = self._run(coro)
        while True:
            try:
                return future.result(timeout=0.1)
            except FutureTimeout:
                if self._abort.requested:
                    future.cancel()
                    raise AbortRequested() from None

    def _bytes(self, data: Any) -> bytes:
        if data is None:
            return b""
        if not isinstance(data, list):
            text = dotnet_text("invalid_cast", self._session.locale)
            raise HostError(
                text.format(_dotnet_type(data), "System.Byte[]"), number=E_NOINTERFACE
            )
        values = cast("list[Any]", data)
        return bytes(int(v) & 0xFF for v in values)

    def connect(self) -> None:
        self._wait(self._session.connect())

    def disconnect(self) -> None:
        self._wait(self._session.disconnect())

    def read_device_descriptor0(self) -> int:
        return self._session.device_descriptor()

    def get_max_apdu_length(self) -> int:
        return self._session.max_apdu_length()

    def locate_interface_object(self, object_type: Any, instance: Any) -> int:
        return self._wait(
            self._session.locate_interface_object(int(object_type), int(instance))
        )

    def read_function_property(self, obj: Any, pid: Any, data: Any) -> list[int]:
        payload = self._bytes(data)
        programmer = self._session.programmer
        return list(
            self._wait(
                programmer.function_property_raw(
                    int(obj), int(pid), payload, command=False
                )
            )
        )

    def invoke_function_property(self, obj: Any, pid: Any, data: Any) -> list[int]:
        payload = self._bytes(data)
        programmer = self._session.programmer
        return list(
            self._wait(
                programmer.function_property_raw(
                    int(obj), int(pid), payload, command=True
                )
            )
        )

    def read_property(
        self, obj: Any, pid: Any, _type: Any, start: Any, count: Any
    ) -> list[int]:
        programmer = self._session.programmer
        data = self._wait(
            programmer.read_property(
                int(obj), int(pid), count=int(count), start_index=int(start)
            )
        )
        if not data:
            text = dotnet_text("property_empty", self._session.locale)
            raise HostError(
                text.format(int(obj), int(pid), int(start), int(count)),
                number=COR_E_EXCEPTION,
            )
        return list(data)

    def write_property(
        self,
        obj: Any,
        pid: Any,
        _type: Any,
        start: Any,
        count: Any,
        data: Any,
        verify: Any,
    ) -> list[int]:
        payload = self._bytes(data)
        programmer = self._session.programmer
        return list(
            self._wait(
                programmer.write_property(
                    int(obj),
                    int(pid),
                    payload,
                    count=int(count),
                    start_index=int(start),
                    verify=bool(verify),
                )
            )
        )

    def read_memory(self, address: Any, size: Any) -> list[int]:
        programmer = self._session.programmer
        return list(self._wait(programmer.read_memory(int(address), int(size))))

    def write_memory(self, address: Any, data: Any, verify: Any) -> None:
        payload = self._bytes(data)
        programmer = self._session.programmer
        self._wait(programmer.write_memory(int(address), payload, verify=bool(verify)))

    def read_user_memory(self, address: Any, size: Any) -> list[int]:
        programmer = self._session.programmer
        return list(self._wait(programmer.read_user_memory(int(address), int(size))))

    def write_user_memory(self, address: Any, data: Any, verify: Any) -> None:
        payload = self._bytes(data)
        programmer = self._session.programmer
        self._wait(
            programmer.write_user_memory(int(address), payload, verify=bool(verify))
        )

    def restart(self) -> None:
        self._wait(self._session.restart())

    def coap(self, *_args: Any) -> None:
        raise HostError(COAP_NOT_SUPPORTED, number=E_NOTIMPL)

    def functions(self) -> dict[str, HostFunction]:
        return {
            "o.connect": self.connect,
            "o.disconnect": self.disconnect,
            "o.readDeviceDescriptor0": self.read_device_descriptor0,
            "o.getMaxApduLength": self.get_max_apdu_length,
            "o.locateInterfaceObject": self.locate_interface_object,
            "o.readFunctionProperty": self.read_function_property,
            "o.invokeFunctionProperty": self.invoke_function_property,
            "o.readProperty": self.read_property,
            "o.writeProperty": self.write_property,
            "o.readMemory": self.read_memory,
            "o.writeMemory": self.write_memory,
            "o.readUserMemory": self.read_user_memory,
            "o.writeUserMemory": self.write_user_memory,
            "o.restart": self.restart,
            "o.coapReadCollection": self.coap,
            "o.coapGet": self.coap,
            "o.coapPut": self.coap,
            "o.coapPost": self.coap,
        }
