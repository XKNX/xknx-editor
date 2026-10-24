"""Open-KNX-KB (vendor-neutral KNX knowledge base) loader and query service.

Loads the structured knowledge base containing:
- 01_domains: Domain rules and paradigms (lighting, shading, HVAC, HEMS, safety)
- 02_functional_blocks: Vendor-neutral functional blocks following KNX Vol. 7
- 03_addressing_patterns: Recommended GA structures (3-level standard, allocation matrices)
- 04_device_archetypes: Common hardware configurations (DALI-2 gateways, heating actuators, push buttons)
- manifest.json: Machine-readable index and DPT catalog

Exposes fast lookup, multi-field search, DPT indexing, and entry retrieval for AI agents using MCP.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml


@dataclass(slots=True, frozen=True)
class KnowledgeEntry:
    """A single document in the knowledge base."""

    id: str
    title: str
    category: str
    file: str  # relative path, e.g. "01_domains/lighting.md"
    metadata: dict[str, Any]
    body: str
    content: str
    summary: str


def resolve_kb_dir() -> Path:
    """Resolve the root directory of the Open-KNX-KB knowledge base.

    Priority:
    1. XKNX_KB_DIR environment variable
    2. Local network path H:\\HeimNetzwerk\\KNX\\ETS_MCP\\open-knx-kb (if reachable)
    3. Bundled package data in editor_gui/plugins/mcp/knowledge_base
    """
    env_dir = os.environ.get("XKNX_KB_DIR")
    if env_dir:
        p = Path(env_dir)
        if p.is_dir() and (p / "manifest.json").is_file():
            return p

    local_path = Path(r"H:\HeimNetzwerk\KNX\ETS_MCP\open-knx-kb")
    if local_path.is_dir() and (local_path / "manifest.json").is_file():
        return local_path

    bundled = Path(__file__).parent / "knowledge_base"
    return bundled


def extract_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Extract YAML frontmatter and markdown body from raw document text."""
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            try:
                parsed: Any = yaml.safe_load(parts[1])
                if isinstance(parsed, dict):
                    return cast(dict[str, Any], parsed), parts[2].strip()
            except Exception:
                pass
    return {}, content.strip()


def extract_title_from_markdown(content: str, default: str) -> str:
    """Extract first top-level # heading from markdown body."""
    for line in content.splitlines():
        line_clean = line.strip()
        if line_clean.startswith("# "):
            return line_clean[2:].strip()
    return default


def normalize_dpt(dpt: str) -> str:
    """Normalize DPT string variants like 'DPT 1.001', 'dpt-1.001', '1.001' -> '1.001'."""
    clean = re.sub(r"^(?i:dpt[-_ ]*)", "", dpt.strip())
    return clean


def _fold_text(s: str) -> str:
    """Fold case and common German umlaut/eszett variations."""
    return (
        s.lower()
        .replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("ß", "ss")
    )


class KnowledgeBaseService:
    """In-memory index and search engine for the Open-KNX-KB knowledge base."""

    def __init__(self, root_dir: Path | None = None) -> None:
        self.root_dir = root_dir or resolve_kb_dir()
        self._manifest: dict[str, Any] = {}
        self._entries: dict[
            str, KnowledgeEntry
        ] = {}  # keyed by ID and by relative file path
        self._dpt_index: dict[str, list[dict[str, Any]]] = {}
        self._loaded = False
        self._load()

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def entries_count(self) -> int:
        # unique entries
        return len({e.id for e in self._entries.values()})

    @property
    def manifest(self) -> dict[str, Any]:
        return self._manifest

    def reload(self) -> None:
        """Force reloading knowledge base files from disk."""
        self._loaded = False
        self._entries.clear()
        self._dpt_index.clear()
        self._manifest.clear()
        self._load()

    def _load(self) -> None:
        if not self.root_dir.is_dir():
            return

        manifest_path = self.root_dir / "manifest.json"
        if manifest_path.is_file():
            try:
                with open(manifest_path, encoding="utf-8") as f:
                    self._manifest = json.load(f)
            except Exception:
                self._manifest = {}

        self._dpt_index = self._manifest.get("dpt_index", {})

        # Load all markdown files from the standard folders
        folders = [
            ("01_domains", "domains"),
            ("02_functional_blocks", "functional_blocks"),
            ("03_addressing_patterns", "addressing_patterns"),
            ("04_device_archetypes", "device_archetypes"),
        ]

        for folder_name, category in folders:
            folder_path = self.root_dir / folder_name
            if not folder_path.is_dir():
                continue
            for file_path in folder_path.glob("*.md"):
                rel_file = f"{folder_name}/{file_path.name}"
                try:
                    with open(file_path, encoding="utf-8") as f:
                        raw_content = f.read()
                except Exception:
                    continue

                meta, body = extract_frontmatter(raw_content)

                # Derive ID
                entry_id = str(meta.get("id") or file_path.stem)

                # Derive Title
                default_title = file_path.stem.replace("_", " ").title()
                title = str(
                    meta.get("title")
                    or extract_title_from_markdown(body, default_title)
                )

                # Derive Summary
                summary = str(meta.get("scope") or meta.get("description") or "")
                if not summary:
                    for line in body.splitlines():
                        trimmed = line.strip()
                        if (
                            trimmed
                            and not trimmed.startswith("#")
                            and not trimmed.startswith("---")
                        ):
                            summary = trimmed
                            break

                entry = KnowledgeEntry(
                    id=entry_id,
                    title=title,
                    category=category,
                    file=rel_file,
                    metadata=meta,
                    body=body,
                    content=raw_content,
                    summary=summary,
                )

                self._entries[entry_id] = entry
                self._entries[rel_file] = entry
                self._entries[rel_file.replace("\\", "/")] = entry
                self._entries[file_path.name] = entry

        self._loaded = True

    def get_manifest(self) -> dict[str, Any]:
        """Return the manifest overview."""
        return {
            "name": self._manifest.get("name", "Open-KNX-KB"),
            "version": self._manifest.get("version", "1.0.0"),
            "description": self._manifest.get(
                "description",
                "Vendor-neutral KNX knowledge base for building automation (Standard vor Willkür)",
            ),
            "domains_count": len(self._manifest.get("domains", [])),
            "functional_blocks_count": len(self._manifest.get("functional_blocks", [])),
            "device_archetypes_count": len(self._manifest.get("device_archetypes", [])),
            "addressing_patterns_count": len(
                self._manifest.get("addressing_patterns", [])
            ),
            "indexed_dpts_count": len(self._dpt_index),
            "root_dir": str(self.root_dir),
        }

    def list_entries(self, category: str | None = None) -> dict[str, Any]:
        """List entries grouped by category, optionally filtered."""
        cat_filter = category.lower().strip() if category else None
        # Normalize category shorthands
        if cat_filter in ("domain", "gewerk", "gewerke"):
            cat_filter = "domains"
        elif cat_filter in ("fb", "functional_block", "functional_blocke", "baustein"):
            cat_filter = "functional_blocks"
        elif cat_filter in ("archetype", "device", "device_archetype", "geraet"):
            cat_filter = "device_archetypes"
        elif cat_filter in ("pattern", "addressing", "addressing_pattern", "muster"):
            cat_filter = "addressing_patterns"

        grouped: dict[str, list[dict[str, Any]]] = {
            "domains": [],
            "functional_blocks": [],
            "device_archetypes": [],
            "addressing_patterns": [],
        }

        seen_ids: set[str] = set()
        for entry in self._entries.values():
            if entry.id in seen_ids:
                continue
            seen_ids.add(entry.id)

            if cat_filter and entry.category != cat_filter:
                continue

            entry_dict: dict[str, Any] = {
                "id": entry.id,
                "title": entry.title,
                "category": entry.category,
                "file": entry.file,
                "summary": entry.summary,
            }
            if "standard_reference" in entry.metadata:
                entry_dict["standard_reference"] = entry.metadata["standard_reference"]
            if "communication_objects" in entry.metadata:
                entry_dict["com_objects_count"] = len(
                    entry.metadata["communication_objects"]
                )
            if "device_class" in entry.metadata:
                entry_dict["device_class"] = entry.metadata["device_class"]

            if entry.category in grouped:
                grouped[entry.category].append(entry_dict)

        # Sort each group by title
        for cat in grouped:
            grouped[cat].sort(key=lambda x: str(x.get("title", "")))

        total = sum(len(items) for items in grouped.values())
        return {
            "total_count": total,
            "filter": category,
            "categories": {k: v for k, v in grouped.items() if v},
        }

    def get_entry(self, entry_id_or_path: str) -> dict[str, Any] | None:
        """Retrieve an entry by ID or file path."""
        target = entry_id_or_path.strip().replace("\\", "/")
        entry = self._entries.get(target)
        if not entry and not target.endswith(".md"):
            entry = self._entries.get(f"{target}.md")
        if not entry:
            return None

        return {
            "id": entry.id,
            "title": entry.title,
            "category": entry.category,
            "file": entry.file,
            "summary": entry.summary,
            "metadata": entry.metadata,
            "content": entry.content,
        }

    def search(
        self, query: str, category: str | None = None, limit: int = 15
    ) -> dict[str, Any]:
        """Search entries by text match in title, metadata, KO names, DPTs, and markdown content."""
        clean_query = query.strip().lower()
        if not clean_query:
            return {"query": query, "count": 0, "results": []}

        query_folded = _fold_text(clean_query)

        cat_filter = category.lower().strip() if category else None
        if cat_filter in ("domain", "gewerk"):
            cat_filter = "domains"
        elif cat_filter in ("fb", "functional_block"):
            cat_filter = "functional_blocks"
        elif cat_filter in ("device", "archetype"):
            cat_filter = "device_archetypes"
        elif cat_filter in ("pattern", "addressing"):
            cat_filter = "addressing_patterns"

        terms = [t for t in re.split(r"\s+", clean_query) if t]
        terms_folded = [_fold_text(t) for t in terms]

        matches: list[tuple[int, KnowledgeEntry, str]] = []
        seen_ids: set[str] = set()

        for entry in self._entries.values():
            if entry.id in seen_ids:
                continue
            seen_ids.add(entry.id)

            if cat_filter and entry.category != cat_filter:
                continue

            score = 0
            id_lower = entry.id.lower()
            title_lower = entry.title.lower()
            body_lower = entry.body.lower()
            meta_str = json.dumps(entry.metadata, ensure_ascii=False).lower()

            id_folded = _fold_text(id_lower)
            title_folded = _fold_text(title_lower)
            body_folded = _fold_text(body_lower)
            meta_folded = _fold_text(meta_str)

            # Exact phrase matches
            if clean_query in id_lower or query_folded in id_folded:
                score += 50
            if clean_query in title_lower or query_folded in title_folded:
                score += 40
            if clean_query in meta_str or query_folded in meta_folded:
                score += 20
            if clean_query in body_lower or query_folded in body_folded:
                score += 10

            # Term matches
            all_terms_matched = True
            for t_raw, t_fold in zip(terms, terms_folded, strict=False):
                term_in_id = t_raw in id_lower or t_fold in id_folded
                term_in_title = t_raw in title_lower or t_fold in title_folded
                term_in_meta = t_raw in meta_str or t_fold in meta_folded
                term_in_body = t_raw in body_lower or t_fold in body_folded

                if not (term_in_id or term_in_title or term_in_meta or term_in_body):
                    all_terms_matched = False
                    break

                if term_in_id:
                    score += 15
                if term_in_title:
                    score += 10
                if term_in_meta:
                    score += 5
                if term_in_body:
                    score += 2

            if not all_terms_matched and score < 30:
                continue

            # Extract snippet from body or summary
            snippet = entry.summary
            for term in terms:
                idx = body_lower.find(term)
                if idx != -1:
                    start = max(0, idx - 60)
                    end = min(len(entry.body), idx + 140)
                    snippet = f"...{entry.body[start:end].replace(chr(10), ' ')}..."
                    break

            matches.append((score, entry, snippet))

        matches.sort(key=lambda x: x[0], reverse=True)
        results: list[dict[str, Any]] = []
        for score, entry, snippet in matches[:limit]:
            results.append(
                {
                    "id": entry.id,
                    "title": entry.title,
                    "category": entry.category,
                    "file": entry.file,
                    "summary": entry.summary,
                    "snippet": snippet,
                    "score": score,
                }
            )

        return {"query": query, "count": len(results), "results": results}

    def lookup_dpt(self, dpt: str) -> dict[str, Any]:
        """Look up standard communication objects, functional blocks, and required flags for a DPT."""
        normalized = normalize_dpt(dpt)
        usages = self._dpt_index.get(normalized, [])

        # If not found directly, check for prefix matches (e.g. "1" -> matches "1.001", "1.002", etc.)
        if not usages and "." not in normalized:
            prefix = f"{normalized}."
            combined: list[dict[str, Any]] = []
            for k, v in self._dpt_index.items():
                if k.startswith(prefix):
                    for item in v:
                        combined.append({**item, "dpt": k})
            if combined:
                return {
                    "dpt_query": dpt,
                    "normalized": normalized,
                    "matched_count": len(combined),
                    "is_prefix_match": True,
                    "usages": combined,
                }

        return {
            "dpt_query": dpt,
            "normalized": normalized,
            "matched_count": len(usages),
            "is_prefix_match": False,
            "usages": usages,
        }


# Global default instance
_kb_singleton: KnowledgeBaseService | None = None


def get_knowledge_base() -> KnowledgeBaseService:
    """Return the global cached KnowledgeBaseService singleton."""
    global _kb_singleton
    if _kb_singleton is None:
        _kb_singleton = KnowledgeBaseService()
    return _kb_singleton
