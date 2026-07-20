"""SessionStart hook: surface where the build stands so every session starts oriented.

Prints the latest shipped version and the next version to build (top of ROADMAP's
Planned list), plus a one-line reminder of the workflow. Cross-platform, no deps,
fails silent — a status line is never worth breaking a session over.
"""
import pathlib
import re
import sys


def _rows(section: str) -> list[str]:
    """Data rows of the first markdown table inside a `## <section>` block."""
    rows = []
    in_section = in_table = False
    for line in section.splitlines():
        if line.startswith("## "):
            in_section = True
            continue
        if in_section and line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):  # separator row
                in_table = True
                continue
            if in_table:
                rows.append(cells)
        elif in_table:
            break
    return rows


def main() -> None:
    # Roadmap text carries em-dashes/arrows; Windows stdout defaults to cp1252.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    root = pathlib.Path(__file__).resolve().parents[2]
    roadmap = root / "ROADMAP.md"
    if not roadmap.exists():
        return
    text = roadmap.read_text(encoding="utf-8")

    blocks = re.split(r"(?m)^## ", text)
    shipped = next((b for b in blocks if b.startswith("Shipped")), "")
    planned = next((b for b in blocks if b.startswith("Planned")), "")

    latest = _rows("## " + shipped)[-1:] if shipped else []
    nxt = _rows("## " + planned)[:1] if planned else []

    def clean(s: str) -> str:
        return s.replace("**", "").replace("`", "").replace("*", "").strip()

    lines = ["Nexus build status (from ROADMAP.md):"]
    if latest:
        lines.append(f"  Latest shipped: {clean(latest[0][0])} - {clean(latest[0][1])}")
    if nxt:
        cols = [clean(c) for c in nxt[0]]
        note = f"  ({cols[3]})" if len(cols) > 3 and cols[3] else ""
        lines.append(f"  Next to build:  {cols[0]} - {cols[1]}{note}")
    lines.append("  Workflow: /idea (route) -> /plan -> /build -> /document. Build order = version order.")
    print("\n".join(lines))


if __name__ == "__main__":
    try:
        main()
    except Exception:  # never break a session over a status line
        sys.exit(0)
