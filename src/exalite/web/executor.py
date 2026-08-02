"""Movement 実行エンジン（WebUI 用）。

ansible-playbook をバックグラウンドスレッドで起動し、標準出力/エラーをログファイルへ
逐次書き出す。WebUI の「実行画面」はこのログと状態をポーリングして表示する。
"""

from __future__ import annotations

import datetime as _dt
import os
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..movement import Movement
from ..runner import Mode, prepare


@dataclass
class RunInfo:
    id: str
    movement: str
    mode: str
    target: Optional[str]
    status: str = "running"          # running | success | failed | error
    returncode: Optional[int] = None
    log_path: str = ""
    run_dir: str = ""
    used_param_sheet: bool = False
    started_at: str = ""
    ended_at: Optional[str] = None
    command: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "movement": self.movement,
            "mode": self.mode,
            "target": self.target,
            "status": self.status,
            "returncode": self.returncode,
            "used_param_sheet": self.used_param_sheet,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "command": " ".join(self.command),
        }


class Executor:
    """実行の起動・状態管理を担う。プロセス内メモリで履歴を保持する。"""

    def __init__(self) -> None:
        self._runs: Dict[str, RunInfo] = {}
        self._order: List[str] = []
        self._lock = threading.Lock()

    def start(
        self,
        mv: Movement,
        mode: Mode,
        *,
        target: Optional[str] = None,
        inventory_override: Optional[str] = None,
        force_no_paramsheet: bool = False,
    ) -> RunInfo:
        cmd, run_dir, cfg, used_sheet = prepare(
            mv, mode,
            force_no_paramsheet=force_no_paramsheet,
            inventory_override=inventory_override,
        )
        run_id = f"{mv.name}-{mode.value}-{_dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')[:-3]}"
        log_path = os.path.join(run_dir, "output.log")
        info = RunInfo(
            id=run_id,
            movement=mv.name,
            mode=mode.value,
            target=target,
            log_path=log_path,
            run_dir=run_dir,
            used_param_sheet=used_sheet,
            started_at=_dt.datetime.now().isoformat(timespec="seconds"),
            command=cmd,
        )
        with self._lock:
            self._runs[run_id] = info
            self._order.append(run_id)

        thread = threading.Thread(
            target=self._run, args=(info, cmd, cfg), daemon=True
        )
        thread.start()
        return info

    def _run(self, info: RunInfo, cmd: List[str], cfg: str) -> None:
        env = dict(os.environ)
        env["ANSIBLE_CONFIG"] = cfg
        # WebUI ログは色コード無しで見やすく
        env["ANSIBLE_FORCE_COLOR"] = "0"
        env["ANSIBLE_NOCOLOR"] = "1"
        try:
            with open(info.log_path, "w", encoding="utf-8") as log:
                log.write(f"$ {' '.join(cmd)}\n")
                log.write(f"# target={info.target or '(既定)'} "
                          f"parameter_sheet={'使用' if info.used_param_sheet else '未使用'}\n\n")
                log.flush()
                proc = subprocess.run(
                    cmd, env=env, stdout=log, stderr=subprocess.STDOUT
                )
            info.returncode = proc.returncode
            info.status = "success" if proc.returncode == 0 else "failed"
        except Exception as exc:  # noqa: BLE001 - ログに残して継続
            info.status = "error"
            try:
                with open(info.log_path, "a", encoding="utf-8") as log:
                    log.write(f"\n[executor error] {exc}\n")
            except OSError:
                pass
        finally:
            info.ended_at = _dt.datetime.now().isoformat(timespec="seconds")

    def get(self, run_id: str) -> Optional[RunInfo]:
        return self._runs.get(run_id)

    def log(self, run_id: str) -> str:
        info = self._runs.get(run_id)
        if not info or not os.path.isfile(info.log_path):
            return ""
        with open(info.log_path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()

    def list(self, limit: int = 30) -> List[RunInfo]:
        with self._lock:
            ids = list(reversed(self._order))[:limit]
        return [self._runs[i] for i in ids]
