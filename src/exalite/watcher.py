"""ファイル変更監視（ローカル Sandbox ループ用）。

Role ディレクトリ・Movement ファイル・パラメータシートの mtime をポーリングし、
変更を検知したら指定モード（既定は構文チェック）を自動実行する。
watchdog 等の外部依存は使わず標準ライブラリのみで実装する。
"""

from __future__ import annotations

import os
import time
from typing import Dict, List

from .movement import Movement


def _iter_watch_files(mv: Movement) -> List[str]:
    files: List[str] = [mv.source_path]
    if mv.parameter_sheet:
        files.append(mv.parameter_sheet)
    roots = []
    if mv.role:
        roots.append(mv.role)
    if mv.playbook:
        roots.append(os.path.dirname(mv.playbook))
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            # 生成物ディレクトリは無視
            dirnames[:] = [d for d in dirnames if d not in {".exalite", ".git"}]
            for name in filenames:
                files.append(os.path.join(dirpath, name))
    return files


def _snapshot(files: List[str]) -> Dict[str, float]:
    snap: Dict[str, float] = {}
    for f in files:
        try:
            snap[f] = os.path.getmtime(f)
        except OSError:
            continue
    return snap


def watch(mv: Movement, on_change, *, interval: float = 1.0) -> None:
    """変更検知のたびに on_change(mv) を呼ぶ。Ctrl-C まで継続。"""
    print(f"[exalite] 監視開始: movement={mv.name} (Ctrl-C で終了)")
    last = _snapshot(_iter_watch_files(mv))
    # 起動直後に一度実行
    on_change(mv)
    try:
        while True:
            time.sleep(interval)
            current = _snapshot(_iter_watch_files(mv))
            changed = [
                f for f, m in current.items()
                if f not in last or last[f] != m
            ]
            removed = [f for f in last if f not in current]
            if changed or removed:
                names = ", ".join(os.path.basename(c) for c in (changed or removed)[:5])
                print(f"\n[exalite] 変更検知: {names} …再チェック")
                on_change(mv)
            last = current
    except KeyboardInterrupt:
        print("\n[exalite] 監視終了")
