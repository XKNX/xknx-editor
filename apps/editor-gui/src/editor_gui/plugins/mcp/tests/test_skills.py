"""The skills provider exposes the bundled skill directory as an MCP resource, and every tool call
the skill shows matches the tools: their names, argument names, required arguments, allowed values
and batch ops — so the examples cannot drift from the server."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fastmcp import Client

from editor_gui.plugins.mcp.tests.test_server import _build

_SKILL = "skill://knx-editing/SKILL.md"
_CALL = re.compile(r"^\s*(\w+)\(", re.MULTILINE)
_OP = re.compile(r'"op":\s*"(\w+)"')


def _calls(text: str) -> list[tuple[str, str]]:
    """Every ``name(...)`` call inside the fenced code blocks, with its argument text."""
    calls: list[tuple[str, str]] = []
    for block in re.findall(r"```[^\n]*\n(.*?)```", text, re.DOTALL):
        for match in _CALL.finditer(block):
            depth, start = 0, match.end() - 1
            for end in range(start, len(block)):
                depth += {"(": 1, ")": -1}.get(block[end], 0)
                if depth == 0:
                    calls.append((match.group(1), block[start + 1 : end]))
                    break
    return calls


def _arguments(text: str) -> dict[str, str]:
    """The top-level ``name=value`` arguments of a call (not those inside lists or dicts)."""
    arguments: dict[str, str] = {}
    depth, quoted, start = 0, False, 0
    for i, char in enumerate(text + ","):
        if char == '"':
            quoted = not quoted
        elif not quoted and char in "([{":
            depth += 1
        elif not quoted and char in ")]}":
            depth -= 1
        elif not quoted and depth == 0 and char == ",":
            name, sep, value = text[start:i].partition("=")
            if sep:
                arguments[name.strip()] = value.strip()
            start = i + 1
    return arguments


def _allowed(schema: dict[str, Any]) -> set[str] | None:
    """The values an argument's schema allows, if it lists them."""
    if "enum" in schema:
        return {str(v) for v in schema["enum"]}
    for option in schema.get("anyOf", []):
        if "enum" in option:
            return {str(v) for v in option["enum"]}
    return None


async def test_knx_skill_exposed_as_resource(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        uris = {str(r.uri) for r in await client.list_resources()}
        assert _SKILL in uris, uris

        content = await client.read_resource(_SKILL)
        assert content[0].text.strip()  # type: ignore[union-attr]  # non-empty


async def test_the_skill_calls_the_tools_as_they_are(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
        text = (await client.read_resource(_SKILL))[0].text  # type: ignore[union-attr]
    calls = _calls(text)
    assert len(calls) > 20
    for name, argument_text in calls:
        assert name in tools, f"{name} is no tool"
        schema = tools[name].input_schema
        properties = schema.get("properties", {})
        arguments = _arguments(argument_text)
        for argument, value in arguments.items():
            assert argument in properties, f"{name} has no argument {argument!r}"
            allowed = _allowed(properties[argument])
            if allowed is not None:
                assert json.loads(value) in allowed, f"{name}({argument}={value})"
        missing = set(schema.get("required", [])) - set(arguments)
        assert not missing, f"{name} misses {sorted(missing)}"
        for op in _OP.findall(argument_text):
            assert f"- {op}:" in (tools["project_batch"].description or ""), op
