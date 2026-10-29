"""Turn docs/schema.yaml into compact plain text for an LLM prompt.

Output is deterministic (same YAML -> byte-identical text), which keeps prompts reproducible
across eval runs and makes them cacheable later.
"""

from typing import Any


def _render_column(name: str, spec: dict[str, Any]) -> str:
    parts = [f"- {name} {spec['type']}"]
    if spec.get("nullable"):
        parts.append("NULL")
    line = " ".join(parts) + f": {spec['description']}"
    if allowed := spec.get("allowed_values"):
        line += f" Values: {', '.join(str(v) for v in allowed)}."
    examples = ", ".join(str(e) for e in spec.get("examples", []))
    if examples:
        line += f" e.g. {examples}"
    return line


def _render_table(name: str, spec: dict[str, Any]) -> str:
    lines = [
        f"### {name}",
        spec["description"].strip(),
        f"Grain: {spec['grain']}. Primary key: ({', '.join(spec['primary_key'])}). "
        f"Rows: {spec['row_count']:,}.",
        "Columns:",
    ]
    lines += [_render_column(c, s) for c, s in spec["columns"].items()]
    if spec["relationships"]:
        lines.append("Joins:")
        for rel in spec["relationships"]:
            line = f"- {name}.{rel['column']} -> {rel['references']} ({rel['cardinality']})"
            if note := rel.get("note"):
                line += f". {note}"
            lines.append(line)
    if notes := spec.get("notes"):
        lines.append(f"Notes: {notes.strip()}")
    return "\n".join(lines)


def render_bare_schema(doc: dict[str, Any]) -> str:
    """Only what a database catalog would tell you: tables, column types, keys and joins.

    Used for the documentation ablation (baseline-raw): no descriptions, examples, business
    rules or conventions, roughly what an undocumented company warehouse offers.
    """
    lines = [f"## Tables (PostgreSQL schema `{doc['schema']}`)"]
    for name, spec in doc["tables"].items():
        cols = ", ".join(f"{c} {s['type']}" for c, s in spec["columns"].items())
        lines.append(f"\n### {name}\nPrimary key: ({', '.join(spec['primary_key'])})")
        lines.append(f"Columns: {cols}")
        for rel in spec["relationships"]:
            lines.append(f"Join: {name}.{rel['column']} -> {rel['references']}")
    return "\n".join(lines)


def render_schema(doc: dict[str, Any]) -> str:
    """Render the full schema doc: dataset summary, all tables, business rules, conventions."""
    sections = [
        f"## Dataset\n{doc['dataset'].strip()}",
        f"## Tables (PostgreSQL schema `{doc['schema']}`)",
        *(_render_table(name, spec) for name, spec in doc["tables"].items()),
        "## Business rules",
        "\n".join(f"- {n['topic']}: {n['note'].strip()}" for n in doc["business_notes"]),
        "## Conventions for ambiguous terms",
        "\n".join(f"- {a['question']} {a['assumption'].strip()}" for a in doc["ambiguities"]),
    ]
    return "\n\n".join(sections)
