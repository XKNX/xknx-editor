from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from typing import TYPE_CHECKING, Any, cast

from dukpy.evaljs import JSInterpreter  # pyright: ignore[reportMissingTypeStubs]

from .errors import (
    E_FAIL,
    AbortRequested,
    HostError,
    ScriptAborted,
    ScriptCompileError,
    ScriptError,
)

if TYPE_CHECKING:
    from .compat.runtime import JScriptEnv

HostFunction = Callable[..., Any]


class AbortToken:
    """Thread-safe force-stop flag shared by a run and its nested contexts."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def request(self) -> None:
        self._event.set()

    @property
    def requested(self) -> bool:
        return self._event.is_set()


class _Interpreter(JSInterpreter):  # pyright: ignore[reportUntypedBaseClass]
    """dukpy interpreter without process, require or console globals."""

    def _init_process(self) -> None:
        pass

    def _init_require(self) -> None:
        pass

    def _init_console(self) -> None:
        pass


@cache
def _asset(name: str) -> str:
    return (
        resources.files("xknxeditor.prod.script")
        .joinpath(name)
        .read_text(encoding="utf-8")
    )


def host_prelude() -> str:
    return _asset("host.js")


@dataclass
class InvokeResult:
    value: Any
    readback: list[Any] = field(default_factory=list[Any])


def _decode(v: Any) -> Any:
    if isinstance(v, dict):
        d = cast(dict[str, Any], v)
        if "$num" in d and len(d) == 1:
            return float(d["$num"])
        return {k: _decode(x) for k, x in d.items()}
    if isinstance(v, list):
        return [_decode(x) for x in cast(list[Any], v)]
    return v


def _encode(v: Any) -> Any:
    if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
        return {"$num": "NaN" if v != v else ("Infinity" if v > 0 else "-Infinity")}
    if isinstance(v, dict):
        return {k: _encode(x) for k, x in cast(dict[str, Any], v).items()}
    if isinstance(v, (list, tuple)):
        return [_encode(x) for x in cast(Sequence[Any], v)]
    return v


class ScriptContext:
    """One isolated script engine instance; create and use it on a single thread."""

    def __init__(
        self,
        *,
        host: Mapping[str, HostFunction] | None = None,
        abort: AbortToken | None = None,
        preludes: Sequence[str] = (),
        script: str | None = None,
        get_message: bool = False,
        on_log: Callable[[str, str], None] | None = None,
        jscript: JScriptEnv | bool = True,
        deadline: float | None = None,
        values: Mapping[str, str] | None = None,
    ) -> None:
        """``values`` become global variables ahead of ``script`` (which may redefine them)."""
        from .compat import runtime

        self._abort = abort or AbortToken()
        self._on_log = on_log
        # Wall-clock limit enforced from the loop-abort hook. The hook is immutable (see host.js),
        # so a runaway loop in a calculation or validation (which the user cannot cancel) still stops.
        self._deadline = deadline
        self._start = time.monotonic()
        env = runtime.JScriptEnv() if jscript is True else jscript or None
        self._jscript = env is not None
        self._interp: Any = _Interpreter()
        self._interp.export_function("__tick", self._wrap(self._tick))
        self._interp.export_function("log", self._wrap(self._log))
        functions: dict[str, HostFunction] = {}
        if env is not None:
            functions.update(runtime.host_functions(env))
        functions.update(host or {})
        for name, fn in functions.items():
            self._interp.export_function(name, self._wrap(fn))
        self._eval(host_prelude())
        self._eval(
            f"__xknx__.install({{getMessage: {'true' if get_message else 'false'}}});"
        )
        if env is not None:
            self._eval(runtime.prelude() + "\n;void 0;")
        for prelude in preludes:
            self._eval(prelude)
        if values:
            self._eval(
                "".join(
                    f"this[{json.dumps(name)}] = {json.dumps(text)};"
                    for name, text in values.items()
                )
                + "void 0;"
            )
        if script:
            self.load(script)

    @property
    def abort_token(self) -> AbortToken:
        return self._abort

    def _tick(self) -> None:
        if (
            self._deadline is not None
            and time.monotonic() - self._start > self._deadline
        ):
            self._abort.request()
            raise AbortRequested()
        time.sleep(0.0005)

    def _log(self, level: str, message: str) -> None:
        if self._on_log is not None:
            self._on_log(level, message)

    def _wrap(self, fn: HostFunction) -> HostFunction:
        def call(*args: Any) -> dict[str, Any]:
            if self._abort.requested:
                return {"a": 1}
            try:
                return {"v": _encode(fn(*(_decode(a) for a in args)))}
            except AbortRequested:
                return {"a": 1}
            except HostError as exc:
                return {"e": {"message": exc.message, "number": exc.number}}
            except Exception as exc:
                return {"e": {"message": str(exc), "number": E_FAIL}}

        return call

    def _eval(self, code: str, **kwargs: Any) -> Any:
        return self._interp.evaljs(code, **kwargs)

    def load(self, script: str) -> None:
        """Evaluate script text in the global scope."""
        if self._jscript:
            from .compat.frontend import transform

            result = transform(script)
            if result.fnmap:
                self._eval("__xk.fnmap(dukpy.m)", m=result.fnmap)
            script = result.code
        try:
            self._eval(script + "\n;void 0;")
        except Exception as exc:
            message = str(exc)
            if message.startswith("SyntaxError"):
                raise ScriptCompileError(
                    _first_line(message), name="SyntaxError"
                ) from exc
            raise ScriptError(_first_line(message)) from exc

    def invoke(
        self, func: str, args: Sequence[Any], *, readback: Sequence[int] = ()
    ) -> InvokeResult:
        """Call a global function; ``readback`` returns arguments after the call."""
        if self._abort.requested:
            raise ScriptAborted()
        try:
            out = self._eval(
                "__xknx__.invoke(dukpy.f, dukpy.a, dukpy.b)",
                f=func,
                a=_encode(list(args)),
                b=list(readback),
            )
        except Exception as exc:
            raise ScriptError(_first_line(str(exc))) from exc
        result = cast(dict[str, Any], out)
        if result.get("a"):
            raise ScriptAborted()
        if "x" in result:
            x = cast(dict[str, Any], result["x"])
            raise ScriptError(
                str(x.get("message", "")),
                name=str(x.get("name", "Error")),
                number=cast(int | None, x.get("number")),
            )
        return InvokeResult(
            _decode(result.get("r")),
            [_decode(v) for v in cast(list[Any], result.get("b") or [])],
        )

    def eval(self, code: str) -> Any:
        """Evaluate an expression in the global scope and return its JSON value."""
        return _decode(self._eval(code))


def _first_line(message: str) -> str:
    return message.strip().splitlines()[0] if message.strip() else message
