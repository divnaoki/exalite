"""パラメータシート（任意）の読み込み。

Exastro のパラメータシートに相当する CSV を扱う。ただし本ツールでは
パラメータシートは **任意** であり、無ければ Role の defaults/main.yml・
vars/main.yml がそのまま使われる。

CSV フォーマット（1 行目はヘッダ）::

    host,VAR_port,VAR_server_name
    web01,8080,web01.example.com
    web02,8081,web02.example.com

- 先頭列は必ずホスト名（列名は host / hostname / ホスト のいずれか、または任意）。
- 2 列目以降が変数名。セル値は YAML として解釈を試み（8080→int, true→bool 等）、
  失敗した場合は文字列として扱う。
- ここで与えた値は Ansible の host_vars として展開され、Role の defaults を上書きする。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from typing import Any, Dict, List

import yaml


class ParamSheetError(Exception):
    """パラメータシートが不正な場合に送出する。"""


_HOST_COLUMN_ALIASES = {"host", "hostname", "ホスト", "ホスト名"}


@dataclass
class ParamSheet:
    """host -> {変数名: 値} のマッピング。"""

    host_vars: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    @property
    def hosts(self) -> List[str]:
        return list(self.host_vars.keys())


def _coerce(value: str) -> Any:
    """セル値を YAML として解釈。失敗時は元の文字列を返す。"""
    if value is None:
        return None
    stripped = value.strip()
    if stripped == "":
        return ""
    try:
        parsed = yaml.safe_load(stripped)
    except yaml.YAMLError:
        return value
    # yaml.safe_load はマップ/リスト表記も解釈するため、そのまま返す。
    return parsed


def load_paramsheet(path: str) -> ParamSheet:
    """パラメータシート CSV を読み込む。"""
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        rows = [row for row in reader if any(cell.strip() for cell in row)]

    if not rows:
        raise ParamSheetError(f"パラメータシートが空です: {path}")

    header = [h.strip() for h in rows[0]]
    if len(header) < 2:
        raise ParamSheetError(
            f"パラメータシートには 'host' 列と 1 つ以上の変数列が必要です: {path}"
        )

    var_columns = header[1:]
    sheet = ParamSheet()

    for lineno, row in enumerate(rows[1:], start=2):
        if not row or not row[0].strip():
            continue
        host = row[0].strip()
        if host in sheet.host_vars:
            raise ParamSheetError(
                f"ホストが重複しています: {host} (行 {lineno}, {path})"
            )
        values: Dict[str, Any] = {}
        for idx, col in enumerate(var_columns, start=1):
            if not col:
                continue
            cell = row[idx] if idx < len(row) else ""
            values[col] = _coerce(cell)
        sheet.host_vars[host] = values

    if not sheet.host_vars:
        raise ParamSheetError(f"データ行がありません: {path}")

    return sheet
