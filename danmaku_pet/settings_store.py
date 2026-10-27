"""Safe, local-only updates to the project's .env file."""

from __future__ import annotations

from pathlib import Path


def update_env_file(path: Path, updates: dict[str, str]) -> None:
    """Update/add keys while preserving comments and unrelated lines.

    Values are intentionally written as single-line values. The caller is
    responsible for validating them. The file lives inside the workspace and
    is excluded from Git; the API never returns its secret values.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if path.is_file():
        lines = path.read_text(encoding="utf-8").splitlines()
    seen: set[str] = set()
    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            output.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in updates:
            output.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            output.append(line)
    missing = [key for key in updates if key not in seen]
    if missing and output and output[-1].strip():
        output.append("")
    for key in missing:
        output.append(f"{key}={updates[key]}")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    temporary.replace(path)
