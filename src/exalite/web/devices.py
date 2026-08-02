"""機器一覧（インベントリ）の読み書き。

環境ごとの INI 形式インベントリを、機器(ホスト)単位の構造として扱う。
`[group]` 配下のホスト行を編集対象とし、`[group:vars]` ブロックはそのまま保持する。

機器の表現::

    {"group": "linux", "name": "web01",
     "vars": {"ansible_host": "10.0.0.11", "ansible_user": "ops"}}
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Tuple

_SECTION_RE = re.compile(r"^\[([^\]]+)\]\s*$")


def _split_host_line(line: str) -> Tuple[str, Dict[str, str]]:
    parts = line.split()
    name = parts[0]
    variables: Dict[str, str] = {}
    for tok in parts[1:]:
        if "=" in tok:
            k, v = tok.split("=", 1)
            variables[k] = v
    return name, variables


def parse(path: str) -> Dict[str, object]:
    """INI インベントリを {devices, group_vars_blocks} に分解する。"""
    devices: List[dict] = []
    group_vars_blocks: Dict[str, List[str]] = {}
    header_comments: List[str] = []

    if not os.path.isfile(path):
        return {"devices": devices, "group_vars_blocks": group_vars_blocks,
                "header": header_comments}

    current_group = None
    current_vars_group = None
    seen_section = False

    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            stripped = line.strip()
            m = _SECTION_RE.match(stripped)
            if m:
                seen_section = True
                section = m.group(1)
                if section.endswith(":vars"):
                    current_vars_group = section[: -len(":vars")]
                    current_group = None
                    group_vars_blocks.setdefault(current_vars_group, [])
                else:
                    current_group = section
                    current_vars_group = None
                continue

            if current_vars_group is not None:
                group_vars_blocks[current_vars_group].append(line)
                continue

            if not stripped or stripped.startswith("#") or stripped.startswith(";"):
                if not seen_section:
                    header_comments.append(line)
                continue

            if current_group is not None:
                name, variables = _split_host_line(stripped)
                devices.append({"group": current_group, "name": name, "vars": variables})

    return {"devices": devices, "group_vars_blocks": group_vars_blocks,
            "header": header_comments}


def write(path: str, devices: List[dict], group_vars_blocks: Dict[str, List[str]] | None = None,
          header: List[str] | None = None) -> None:
    """機器一覧を INI インベントリとして書き出す（group:vars は保持）。"""
    group_vars_blocks = group_vars_blocks or {}
    header = header or []

    # グループ順を安定させる（devices 出現順 + vars ブロックのみのグループ）
    groups: List[str] = []
    for d in devices:
        g = d.get("group") or "ungrouped"
        if g not in groups:
            groups.append(g)
    for g in group_vars_blocks:
        if g not in groups:
            groups.append(g)

    lines: List[str] = []
    for h in header:
        lines.append(h)
    if header:
        lines.append("")

    for g in groups:
        lines.append(f"[{g}]")
        for d in devices:
            if (d.get("group") or "ungrouped") != g:
                continue
            tokens = [d["name"]]
            for k, v in (d.get("vars") or {}).items():
                if v is None or v == "":
                    continue
                tokens.append(f"{k}={v}")
            lines.append(" ".join(tokens))
        block = group_vars_blocks.get(g)
        if block:
            lines.append("")
            lines.append(f"[{g}:vars]")
            # 末尾の空行を整理
            trimmed = [b for b in block]
            while trimmed and trimmed[-1].strip() == "":
                trimmed.pop()
            lines.extend(trimmed)
        lines.append("")

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines).rstrip("\n") + "\n")
