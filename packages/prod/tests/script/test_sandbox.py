from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from xknxeditor.prod.script import (
    AbortToken,
    HostError,
    ScriptAborted,
    ScriptCompileError,
    ScriptContext,
    ScriptError,
)
from xknxeditor.prod.script.errors import COR_E_KEYNOTFOUND, COR_E_TARGETINVOCATION


def _device_host(store: dict[str, Any]) -> dict[str, Any]:
    def by_name(scope: str | None, name: str) -> str:
        if name not in store:
            raise HostError("not found", number=COR_E_KEYNOTFOUND)
        return name

    return {
        "d.byName": by_name,
        "p.get": lambda ref: store[ref],
        "p.set": lambda ref, v: store.__setitem__(ref, v),
        "p.active": lambda ref: True,
        "p.ref": lambda ref: f"REF_{ref}",
        "p.name": lambda ref: ref,
    }


DEVICE = {"$host": "device", "scope": None}


def test_engine_globals_are_hidden() -> None:
    ctx = ScriptContext(
        script="function probe() { return [typeof process, typeof require, typeof module, typeof console, typeof call_python, typeof dukpy].join(','); }"
    )
    assert ctx.invoke("probe", []).value == ",".join(["undefined"] * 6)


def test_contexts_are_isolated() -> None:
    script = "var counter = 0; function bump() { counter++; leaked = counter; return counter; }"
    first = ScriptContext(script=script)
    assert first.invoke("bump", []).value == 1
    assert first.invoke("bump", []).value == 2
    second = ScriptContext(
        script=script + " function probe() { return typeof leaked; }"
    )
    assert second.invoke("probe", []).value == "undefined"
    assert second.invoke("bump", []).value == 1


def test_parameter_bridge() -> None:
    store: dict[str, Any] = {"A": 5, "T": "abc"}
    ctx = ScriptContext(
        host=_device_host(store),
        script="""
        function h(device, online, progress, context) {
            var a = device.getParameterByName("A");
            a.value = a.value + context.add;
            var t = device.getParameterByName("T");
            t.value = t.value + "def";
            return [typeof a.value, a.isActive, a.parameterRefId, a.name, typeof online, typeof progress].join("|");
        }""",
    )
    result = ctx.invoke("h", [DEVICE, None, None, {"add": 4}])
    assert result.value == "number|true|REF_A|A|object|object"
    assert store == {"A": 9, "T": "abcdef"}


def test_host_error_is_catchable() -> None:
    ctx = ScriptContext(
        host=_device_host({}),
        script="""
        function h(device) {
            try { device.getParameterByName("missing"); return "no"; }
            catch (e) { return e.message + "|" + e.number + "|" + e.description; }
        }""",
    )
    assert ctx.invoke("h", [DEVICE]).value == f"not found|{COR_E_KEYNOTFOUND}|not found"


def test_uncaught_errors() -> None:
    ctx = ScriptContext(
        script='function h() { throw new Error("boom"); } function s() { throw "plain"; }'
    )
    with pytest.raises(ScriptError) as err:
        ctx.invoke("h", [])
    assert (err.value.name, err.value.message) == ("Error", "boom")
    with pytest.raises(ScriptError) as err:
        ctx.invoke("s", [])
    assert err.value.message == "plain"
    with pytest.raises(ScriptError) as err:
        ctx.invoke("missing", [])
    assert err.value.message == "'missing' is undefined"


def test_compile_error() -> None:
    with pytest.raises(ScriptCompileError):
        ScriptContext(script="function (")


def test_readback_and_values() -> None:
    ctx = ScriptContext(
        script="function c(input, output, context) { output.r = input.a * context.k; output.s = 'x'; output.n = 0 / 0; return [1, 255, -1]; }"
    )
    result = ctx.invoke("c", [{"a": 3}, {"r": 0}, {"k": 2}], readback=[1])
    assert result.value == [1, 255, -1]
    out = result.readback[0]
    assert out["r"] == 6 and out["s"] == "x"
    assert out["n"] != out["n"]


def test_byte_arrays_from_host() -> None:
    ctx = ScriptContext(
        host={"o.invokeFunctionProperty": lambda o, p, d: [0, *d, 0x80]},
        script="""
        function h(device, online) {
            var r = online.invokeFunctionProperty(160, 3, [11, 2]);
            return [r.length, r[0], r[1], (r[3] << 24) >> 24, r.concat([9]).length].join(",");
        }""",
    )
    assert ctx.invoke("h", [DEVICE, {"$host": "online"}]).value == "4,0,11,-128,5"


def test_log_globals() -> None:
    logs: list[tuple[str, str]] = []
    ctx = ScriptContext(
        on_log=lambda level, msg: logs.append((level, msg)),
        script="function h() { info('a'); Log.info('b'); warn('c'); error('d'); Log.Debug('e'); return typeof getMessage + typeof Debug; }",
    )
    assert ctx.invoke("h", []).value == "undefinedobject"
    assert logs == [
        ("info", "a"),
        ("info", "b"),
        ("warn", "c"),
        ("error", "d"),
        ("error", "e"),
    ]


def test_get_message_global() -> None:
    ctx = ScriptContext(
        get_message=True,
        host={"a.message": lambda i: f"msg {i}"},
        script="function h() { return getMessage(7); }",
    )
    assert ctx.invoke("h", []).value == "msg 7"


def test_application_program_object() -> None:
    # The named object behind the global getMessage, called as ApplicationProgram.getMessage(n).
    ctx = ScriptContext(
        get_message=True,
        host={"a.message": lambda i: f"msg {i}"},
        script="function h() { return ApplicationProgram.getMessage(1) + '|' + ApplicationProgram.GetMessage(2); }",
    )
    assert ctx.invoke("h", []).value == "msg 1|msg 2"


def test_progress_and_with_undo() -> None:
    calls: list[str] = []

    def rollback() -> None:
        calls.append("rollback")
        raise HostError("wrapped", number=COR_E_TARGETINVOCATION)

    host: dict[str, Any] = {
        "g.text": lambda t: calls.append(f"text:{t}"),
        "g.progress": lambda v: calls.append(f"progress:{v}"),
        "g.canceled": lambda: False,
        "d.undoBegin": lambda d: calls.append(f"begin:{d}"),
        "d.undoCommit": lambda: calls.append("commit"),
        "d.undoRollback": rollback,
    }
    ctx = ScriptContext(
        host=host,
        script="""
        function h(device, online, progress) {
            progress.setText("x"); progress.setProgress(12.5);
            var r = [device.withUndo("ok", function () { return 7; }), device.withUndo(5, 5)];
            try { device.withUndo("bad", function () { throw new Error("no"); }); } catch (e) { r.push(e.message, e.number); }
            try { device.withUndo("x"); } catch (e) { r.push(e.number); }
            try { device.withUndo("x", function () {}, 1); } catch (e) { r.push(e.number); }
            r.push(progress.isCanceled());
            return r;
        }""",
    )
    assert ctx.invoke("h", [DEVICE, None, {"$host": "progress"}]).value == [
        None,
        None,
        "wrapped",
        COR_E_TARGETINVOCATION,
        -2146828283,
        -2146827838,
        False,
    ]
    assert calls == [
        "text:x",
        "progress:12.5",
        "begin:ok",
        "commit",
        "begin:bad",
        "rollback",
    ]


def test_abort_unwinds_loops() -> None:
    token = AbortToken()
    ctx = ScriptContext(
        abort=token, script="function h() { while (true) { __xknx__.tick(); } }"
    )
    threading.Timer(0.2, token.request).start()
    started = time.monotonic()
    with pytest.raises(ScriptAborted):
        ctx.invoke("h", [])
    assert time.monotonic() - started < 5


def test_deadline_stops_runaway_loop() -> None:
    ctx = ScriptContext(deadline=0.2, script="function h() { while (true) {} }")
    started = time.monotonic()
    with pytest.raises(ScriptAborted):
        ctx.invoke("h", [])
    assert time.monotonic() - started < 5


def test_deadline_holds_when_script_tries_to_disable_the_abort_hook() -> None:
    # The loop-abort hook is frozen, so neutering it is a no-op and the deadline still fires.
    ctx = ScriptContext(
        deadline=0.2,
        script=(
            "function h() {"
            "  __xknx__.tick = function () {};"
            "  __xk.tk = function () {};"
            "  while (true) {}"
            "}"
        ),
    )
    started = time.monotonic()
    with pytest.raises(ScriptAborted):
        ctx.invoke("h", [])
    assert time.monotonic() - started < 5


def test_nested_context_inside_host_callback() -> None:
    store: dict[str, Any] = {"A": 1, "B": 0}
    host = _device_host(store)

    def set_and_derive(ref: str, value: Any) -> None:
        store[ref] = value
        derived = ScriptContext(script="function twice(x) { return x * 2; }")
        store["B"] = derived.invoke("twice", [value]).value

    host["p.set"] = set_and_derive
    ctx = ScriptContext(
        host=host,
        script='function h(device) { var a = device.getParameterByName("A"); a.value = 21; return device.getParameterByName("B").value; }',
    )
    assert ctx.invoke("h", [DEVICE]).value == 42


def test_host_methods_take_exact_argument_counts() -> None:
    ctx = ScriptContext(
        host={
            "o.readProperty": lambda *a: list(a),
            "g.text": lambda t: None,
            "o.connect": lambda: None,
        },
        script="""
        function e(f) { try { f(); return "none"; } catch (x) { return x.name + x.number; } }
        function h(online, progress) {
            return [
                e(function () { online.readProperty(0, 56); }),
                e(function () { online.readProperty(0, 56, 0, 1, 1, 2); }),
                online.readProperty(0, 56, 0, 1, 1).length,
                e(function () { progress.setText(); }),
                typeof online.connect,
                typeof online.readProperty,
                typeof progress.setText
            ].join(",");
        }
        """,
    )
    assert ctx.invoke("h", [{"$host": "online"}, {"$host": "progress"}]).value == (
        "TypeError-2146828283,TypeError-2146827838,5,TypeError-2146828283,"
        "undefined,unknown,unknown"
    )


def test_parameterless_host_methods_run_on_read() -> None:
    calls: list[str] = []

    def not_connected() -> None:
        raise HostError("Not connected")

    ctx = ScriptContext(
        host={
            "o.connect": lambda: calls.append("connect"),
            "o.disconnect": lambda: calls.append("disconnect"),
            "o.getMaxApduLength": lambda: 55,
            "o.readDeviceDescriptor0": not_connected,
            "o.restart": not_connected,
            "g.canceled": lambda: False,
        },
        script="""
        function h(online, progress) {
            var r = [typeof online.connect, typeof online.getMaxApduLength, typeof online.readDeviceDescriptor0,
                typeof online.restart, typeof progress.isCanceled, typeof online.disconnect];
            online.connect();
            online["disconnect"]();
            return r.join(",");
        }
        """,
    )
    value = ctx.invoke("h", [{"$host": "online"}, {"$host": "progress"}]).value
    assert value == "undefined,number,unknown,unknown,boolean,undefined"
    assert calls == ["connect", "disconnect", "connect", "disconnect"]


def test_values_are_globals_the_script_may_redefine() -> None:
    # A product's message names are global variables holding the message text.
    ctx = ScriptContext(
        values={"outputSelectionError": "Output in Use", "taken": "host"},
        script="var taken = 'script'; function h() { return outputSelectionError + '|' + taken; }",
    )
    assert ctx.invoke("h", []).value == "Output in Use|script"
