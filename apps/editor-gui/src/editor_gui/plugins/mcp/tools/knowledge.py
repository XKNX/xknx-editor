# The @mcp.tool decorator registers each tool via its side effect, not a direct call.
# pyright: reportUnusedFunction=false
"""Knowledge base tools for querying Open-KNX-KB.

Exposes domain guidelines, KNX Vol. 7 standard functional blocks, addressing patterns (3-level
standard), device archetypes (DALI, heating, push buttons), and DPT lookups to AI agents.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastmcp.exceptions import ToolError

from editor_gui.plugins.mcp.context import McpContext, make_tool
from editor_gui.plugins.mcp.knowledge import get_knowledge_base

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register(mcp: FastMCP, ctx: McpContext) -> None:
    tool = make_tool(mcp, ctx)
    kb = get_knowledge_base()

    @tool(read_only=True)
    def kb_search(query: str, category: str | None = None) -> dict[str, Any]:
        """Search the vendor-neutral KNX Knowledge Base (Open-KNX-KB).

        Finds functional blocks (KNX Vol. 7), domain standards (lighting, shading, HVAC, HEMS,
        safety), device archetypes (DALI gateways, heating actuators, push-button panels),
        addressing patterns (3-level standard, GA allocations), and communication objects.

        Category filter options:
        - 'domains' (lighting, shading, hvac, energy_hems, safety_diagnostics)
        - 'functional_blocks' (fb_switch_actuator, fb_dimming_actuator, fb_shutter_blind, etc.)
        - 'device_archetypes' (dali_gateways, heating_actuators, push_button_panels)
        - 'addressing_patterns' (3_level_standard, address_allocation, migration_rules)
        """
        return kb.search(query, category=category)

    @tool(read_only=True)
    def kb_get_entry(entry_id: str) -> dict[str, Any]:
        """Retrieve full specification, metadata, and instructions for a knowledge base document.

        Accepts entry ID (e.g. 'fb_switch_actuator', 'domain_lighting', 'dev_dali_gateway',
        '3_level_standard') or relative file path (e.g. '01_domains/lighting.md').

        Returns parsed YAML frontmatter metadata (including communication_objects, flags, and GA
        recommendations) as well as the complete markdown text.
        """
        entry = kb.get_entry(entry_id)
        if not entry:
            raise ToolError(
                f"Knowledge entry '{entry_id}' not found. Call kb_list_entries() or kb_search() to find valid IDs."
            )
        return entry

    @tool(read_only=True)
    def kb_list_entries(category: str | None = None) -> dict[str, Any]:
        """List all available Open-KNX-KB knowledge entries, grouped by category.

        Pass optional category ('domains', 'functional_blocks', 'device_archetypes',
        'addressing_patterns') to filter.
        """
        return kb.list_entries(category=category)

    @tool(read_only=True)
    def kb_lookup_dpt(dpt: str) -> dict[str, Any]:
        """Look up standard KNX communication objects, functional blocks, directions, and flags for a DPT.

        Returns all standard functional blocks that use this Datapoint Type (e.g. '1.001', '5.001',
        '9.001', '20.102'), specifying:
        - Functional block ID (e.g. 'fb_switch_actuator', 'fb_shutter_blind')
        - Direction ('write' for command inputs, 'transmit' for status outputs)
        - Standard flags: ['C', 'W'] for commands vs. ['C', 'R', 'T'] for status feedbacks
        """
        return kb.lookup_dpt(dpt)

    @tool(read_only=True)
    def kb_get_manifest() -> dict[str, Any]:
        """Get the Open-KNX-KB manifest overview with version, domain list, and counts."""
        return kb.get_manifest()

    # Compatibility aliases for cross-tool interoperability
    @tool(read_only=True)
    def find_knowledge_entries(
        query: str, category: str | None = None
    ) -> dict[str, Any]:
        """Alias for kb_search: search the KNX Knowledge Base."""
        return kb.search(query, category=category)

    @tool(read_only=True)
    def get_knowledge_entry(entry_id: str) -> dict[str, Any]:
        """Alias for kb_get_entry: retrieve full specification for a knowledge base entry."""
        entry = kb.get_entry(entry_id)
        if not entry:
            raise ToolError(
                f"Knowledge entry '{entry_id}' not found. Call kb_list_entries() or kb_search() to find valid IDs."
            )
        return entry
