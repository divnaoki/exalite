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
from typing import Callable, Dict, List, Optional

from ..conductor import Conductor, format_summary, run_conductor
from ..movement import Movement
from ..runner import Mode, base_env, prepare


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
    kind: str = "movement"           # movement | conductor
    steps: List[dict] = field(default_factory=list)  # conductor のステップ結果

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
            "kind": self.kind,
            "steps": self.steps,
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
        env = base_env()
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

    def start_conductor(
        self,
        cond: Conductor,
        mode: Mode,
        *,
        movements_dir: str,
        resolve_inventory: Callable[[Optional[str]], Optional[str]],
        target: Optional[str] = None,
        force_no_paramsheet: bool = False,
    ) -> RunInfo:
        """Conductor のステップを登録順に実行し、ログを 1 本にまとめる。"""
        run_id = f"{cond.name}-conductor-{_dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')[:-3]}"
        run_dir = os.path.join(
            cond.base_dir, ".exalite", "runs",
            f"conductor-{cond.name}-{mode.value}-"
            f"{_dt.datetime.now().strftime('%Y%m%d-%H%M%S')}",
        )
        os.makedirs(run_dir, exist_ok=True)
        info = RunInfo(
            id=run_id,
            movement=cond.name,
            mode=mode.value,
            target=target,
            log_path=os.path.join(run_dir, "output.log"),
            run_dir=run_dir,
            started_at=_dt.datetime.now().isoformat(timespec="seconds"),
            kind="conductor",
            steps=[{"index": i, "movement": s.movement, "status": "pending"}
                   for i, s in enumerate(cond.steps, start=1)],
        )
        with self._lock:
            self._runs[run_id] = info
            self._order.append(run_id)

        thread = threading.Thread(
            target=self._run_conductor,
            args=(info, cond, mode, movements_dir, resolve_inventory,
                  target, force_no_paramsheet),
            daemon=True,
        )
        thread.start()
        return info

    def _run_conductor(
        self,
        info: RunInfo,
        cond: Conductor,
        mode: Mode,
        movements_dir: str,
        resolve_inventory: Callable[[Optional[str]], Optional[str]],
        target: Optional[str],
        force_no_paramsheet: bool,
    ) -> None:
        try:
            with open(info.log_path, "w", encoding="utf-8") as log:

                def emit(line: str) -> None:
                    log.write(line + "\n")
                    log.flush()

                def run_step(mv, step) -> int:
                    step_target = step.target or target
                    cmd, run_dir, cfg, used_sheet = prepare(
                        mv, mode,
                        force_no_paramsheet=force_no_paramsheet or step.no_paramsheet,
                        inventory_override=resolve_inventory(step_target),
                    )
                    emit(f"# target={step_target or '(既定)'} "
                         f"parameter_sheet={'使用' if used_sheet else '未使用'}")
                    emit(f"$ {' '.join(cmd)}")
                    env = base_env()
                    env["ANSIBLE_CONFIG"] = cfg
                    env["ANSIBLE_FORCE_COLOR"] = "0"
                    env["ANSIBLE_NOCOLOR"] = "1"
                    # subprocess が同じ fd に書くため、直前に flush しておく
                    log.flush()
                    proc = subprocess.run(
                        cmd, env=env, stdout=log, stderr=subprocess.STDOUT
                    )
                    log.flush()
                    return proc.returncode

                emit(f"# Conductor={cond.name} mode={mode.value} "
                     f"target={target or '(既定)'} steps={len(cond.steps)}")
                if cond.description:
                    emit(f"# {cond.description}")
                emit("")
                result = run_conductor(
                    cond, run_step=run_step, movements_dir=movements_dir, emit=emit,
                )
                emit("")
                for line in format_summary(result):
                    emit(line)

            info.steps = [
                {"index": o.index, "movement": o.movement,
                 "status": o.status, "returncode": o.returncode}
                for o in result.outcomes
            ]
            info.returncode = result.returncode
            info.status = "success" if result.ok else "failed"
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
