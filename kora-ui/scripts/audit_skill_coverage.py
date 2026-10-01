#!/usr/bin/env python3
"""Audit installed Hermes/Kora skill guides against ADA's registered tool adapters.

Read-only: checks SKILL.md metadata, sibling resource directories, and whether
explicit prerequisite commands are on PATH. Does not invoke skill commands.
"""
from __future__ import annotations

import argparse
import csv
import io
from pathlib import Path
import re
import shutil
import sys

ADAPTERS = {
    "taskwarrior": ["add_task", "list_tasks", "complete_task"],
    "codex": ["run_codex_task"],
}
RESOURCE_DIRS = ("references", "scripts", "templates", "assets")


def _metadata(text: str):
    front = re.match(r"\A---\s*\n(.*?)\n---\s*", text, re.S)
    block = front.group(1) if front else ""
    name = re.search(r"(?m)^name:\s*[\"']?([^\"'\n]+)", block)
    description = re.search(r"(?m)^description:\s*[\"']?([^\"'\n]+)", block)
    commands = []
    lines = block.splitlines()
    in_prerequisites = False
    command_indent = None
    for line in lines:
        if line.strip() and not line[0].isspace():
            if line.startswith("prerequisites:"):
                in_prerequisites = True
                continue
            if in_prerequisites:
                break
        if not in_prerequisites:
            continue
        command_key = re.match(r"^(\s+)commands:\s*(.*)$", line)
        if command_key:
            command_indent = len(command_key.group(1))
            inline = re.search(r"\[([^]]*)\]", command_key.group(2))
            if inline:
                commands.extend(re.findall(r"[A-Za-z0-9_.+-]+", inline.group(1)))
            continue
        if command_indent is not None:
            item = re.match(r"^(\s+)-\s*([A-Za-z0-9_.+-]+)\s*$", line)
            if item and len(item.group(1)) > command_indent:
                commands.append(item.group(2))
            elif line.strip() and (len(line) - len(line.lstrip())) <= command_indent:
                command_indent = None
    return (name.group(1).strip() if name else "",
            description.group(1).strip() if description else "",
            list(dict.fromkeys(commands)))


def audit(root: str | Path):
    root = Path(root).expanduser().resolve()
    rows = []
    for path in sorted(root.rglob("SKILL.md"), key=lambda p: str(p).casefold()):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        name, description, commands = _metadata(text)
        if not name:
            name = path.parent.name
        adapter_tools = ADAPTERS.get(name, [])
        rows.append({
            "name": name,
            "description": description,
            "coverage": "adapter" if adapter_tools else "guidance-only",
            "ada_tools": ";".join(adapter_tools),
            "resource_dirs": ";".join(d for d in RESOURCE_DIRS if (path.parent / d).is_dir()),
            "required_commands": ";".join(commands),
            "missing_commands": ";".join(c for c in commands if not shutil.which(c)),
        })
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(Path.home() / ".local/share/kora/hermes/skills"))
    parser.add_argument("--output", help="CSV destination; stdout if omitted")
    args = parser.parse_args()
    rows = audit(args.root)
    fields = ["name", "description", "coverage", "ada_tools", "resource_dirs",
              "required_commands", "missing_commands"]
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    if args.output:
        Path(args.output).expanduser().write_text(buffer.getvalue(), encoding="utf-8")
    else:
        sys.stdout.write(buffer.getvalue())
    print(f"Audited {len(rows)} skill guides; {sum(bool(r['ada_tools']) for r in rows)} have registered ADA adapters.", file=sys.stderr)


if __name__ == "__main__":
    main()
