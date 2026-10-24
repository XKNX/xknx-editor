"""Tests for the Open-KNX-KB knowledge base service and MCP tool/resource integration."""

from __future__ import annotations

import json
from pathlib import Path

from fastmcp import Client

from editor_gui.plugins.mcp.knowledge import (
    KnowledgeBaseService,
    extract_frontmatter,
    normalize_dpt,
)
from editor_gui.plugins.mcp.tests.test_server import _build


def test_frontmatter_extraction() -> None:
    text = "---\nid: test_entry\ntitle: Test Entry\n---\n# Content\nBody text"
    meta, body = extract_frontmatter(text)
    assert meta["id"] == "test_entry"
    assert meta["title"] == "Test Entry"
    assert body == "# Content\nBody text"

    no_fm = "# Direct Markdown\nWithout frontmatter"
    meta_empty, body_clean = extract_frontmatter(no_fm)
    assert meta_empty == {}
    assert body_clean == no_fm


def test_dpt_normalization() -> None:
    assert normalize_dpt("1.001") == "1.001"
    assert normalize_dpt("DPT 1.001") == "1.001"
    assert normalize_dpt("dpt-1.001") == "1.001"
    assert normalize_dpt("DPT_9.001") == "9.001"
    assert normalize_dpt(" 20.102 ") == "20.102"


def test_knowledge_base_service_loading() -> None:
    kb = KnowledgeBaseService()
    assert kb.is_loaded is True
    assert kb.entries_count >= 15

    manifest = kb.get_manifest()
    assert manifest["name"] == "Open-KNX-KB"
    assert manifest["domains_count"] >= 5
    assert manifest["functional_blocks_count"] >= 7
    assert manifest["device_archetypes_count"] >= 3
    assert manifest["addressing_patterns_count"] >= 3


def test_knowledge_base_entry_retrieval() -> None:
    kb = KnowledgeBaseService()

    # By ID
    fb = kb.get_entry("fb_switch_actuator")
    assert fb is not None
    assert fb["id"] == "fb_switch_actuator"
    assert "communication_objects" in fb["metadata"]
    assert len(fb["metadata"]["communication_objects"]) >= 2
    assert "Schaltaktor" in fb["title"]

    # By relative path
    domain = kb.get_entry("01_domains/lighting.md")
    assert domain is not None
    assert domain["id"] == "domain_lighting"
    assert "Beleuchtungssteuerung" in domain["title"]

    # Non-existent
    assert kb.get_entry("non_existent_key") is None


def test_knowledge_base_list_entries() -> None:
    kb = KnowledgeBaseService()

    # All entries
    all_entries = kb.list_entries()
    assert all_entries["total_count"] >= 15
    cats = all_entries["categories"]
    assert "domains" in cats
    assert "functional_blocks" in cats
    assert "device_archetypes" in cats
    assert "addressing_patterns" in cats

    # Filtered by category
    fb_only = kb.list_entries(category="functional_blocks")
    assert fb_only["total_count"] >= 7
    assert "functional_blocks" in fb_only["categories"]
    assert "domains" not in fb_only["categories"]


def test_knowledge_base_search() -> None:
    kb = KnowledgeBaseService()

    # Search for lighting / DALI
    dali_res = kb.search("DALI")
    assert dali_res["count"] > 0
    ids = [r["id"] for r in dali_res["results"]]
    assert any("dali" in i.lower() or "lighting" in i.lower() for i in ids)

    # Search for heating actuator PWM (test both ß and ss folding)
    heating_res = kb.search("PWM Fußbodenheizung", category="device_archetypes")
    assert heating_res["count"] > 0
    assert heating_res["results"][0]["id"] == "dev_heating_actuator"

    heating_res_ss = kb.search("PWM Fussbodenheizung", category="device_archetypes")
    assert heating_res_ss["count"] > 0
    assert heating_res_ss["results"][0]["id"] == "dev_heating_actuator"


def test_knowledge_base_lookup_dpt() -> None:
    kb = KnowledgeBaseService()

    # Exact DPT lookup
    res_1001 = kb.lookup_dpt("DPT 1.001")
    assert res_1001["matched_count"] > 0
    assert res_1001["is_prefix_match"] is False
    usages = res_1001["usages"]
    # Check that directions and flags are consistent
    switch_cmd = next(u for u in usages if u["ko_id"] == "switch_cmd")
    assert switch_cmd["direction"] == "write"
    assert "C" in switch_cmd["flags"] and "W" in switch_cmd["flags"]

    switch_state = next(u for u in usages if u["ko_id"] == "switch_state")
    assert switch_state["direction"] == "transmit"
    assert (
        "C" in switch_state["flags"]
        and "R" in switch_state["flags"]
        and "T" in switch_state["flags"]
    )

    # Prefix match
    res_prefix = kb.lookup_dpt("9")
    assert res_prefix["matched_count"] > 0
    assert res_prefix["is_prefix_match"] is True


async def test_mcp_knowledge_tools(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        # 1. status() includes knowledge_base
        status_res = await client.call_tool("status", {})
        data = status_res.data
        assert "knowledge_base" in data  # type: ignore[operator]
        assert data["knowledge_base"]["loaded"] is True  # type: ignore[index]
        assert data["knowledge_base"]["entries_count"] >= 15  # type: ignore[index]

        # 2. kb_get_manifest
        manifest_res = await client.call_tool("kb_get_manifest", {})
        assert manifest_res.data["name"] == "Open-KNX-KB"  # type: ignore[index]

        # 3. kb_search
        search_res = await client.call_tool("kb_search", {"query": "Schaltaktor"})
        assert search_res.data["count"] > 0  # type: ignore[index]
        assert search_res.data["results"][0]["id"] == "fb_switch_actuator"  # type: ignore[index]

        # 4. kb_get_entry
        entry_res = await client.call_tool(
            "kb_get_entry", {"entry_id": "fb_switch_actuator"}
        )
        assert entry_res.data["id"] == "fb_switch_actuator"  # type: ignore[index]
        assert "communication_objects" in entry_res.data["metadata"]  # type: ignore[index]

        # 5. kb_list_entries
        list_res = await client.call_tool("kb_list_entries", {"category": "domains"})
        assert list_res.data["total_count"] >= 5  # type: ignore[index]

        # 6. kb_lookup_dpt
        dpt_res = await client.call_tool("kb_lookup_dpt", {"dpt": "1.001"})
        assert dpt_res.data["matched_count"] > 0  # type: ignore[index]

        # 7. Compatibility aliases
        alias_search = await client.call_tool(
            "find_knowledge_entries", {"query": "Jalousie"}
        )
        assert alias_search.data["count"] > 0  # type: ignore[index]

        alias_get = await client.call_tool(
            "get_knowledge_entry", {"entry_id": "fb_shutter_blind"}
        )
        assert alias_get.data["id"] == "fb_shutter_blind"  # type: ignore[index]


async def test_mcp_knowledge_resources(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        # Check resource listing
        resources = await client.list_resources()
        uris = {str(r.uri) for r in resources}

        assert "kb://manifest" in uris
        assert "kb://02_functional_blocks/fb_switch_actuator.md" in uris
        assert "kb://01_domains/lighting.md" in uris

        # Read static manifest resource
        manifest_res = await client.read_resource("kb://manifest")
        parsed = json.loads(manifest_res[0].text)  # type: ignore[union-attr]
        assert parsed["name"] == "Open-KNX-KB"

        # Read document via exact resource URI
        doc_res = await client.read_resource(
            "kb://02_functional_blocks/fb_switch_actuator.md"
        )
        assert "fb_switch_actuator" in doc_res[0].text  # type: ignore[union-attr]

        # Read document via single template kb://{entry_id}
        tpl_single = await client.read_resource("kb://fb_switch_actuator")
        assert "fb_switch_actuator" in tpl_single[0].text  # type: ignore[union-attr]

        # Read document via double template kb://{category}/{name}
        tpl_double = await client.read_resource("kb://fb/fb_switch_actuator")
        assert "fb_switch_actuator" in tpl_double[0].text  # type: ignore[union-attr]
