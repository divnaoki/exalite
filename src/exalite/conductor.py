"""Conductor: 複数の Movement を登録順に実行する。

Exastro の「Conductor（Movement を組み合わせた作業フロー）」に相当する最小構成。
分岐や並列は持たず、**登録した順に上から 1 つずつ実行**する。

Conductor YAML の例（``conductors/<name>.yml``）::

    name: setup_all
    description: 検証環境を一通り構成する
    movements:
      - ping_linux                # 文字列で書けば既定動作（失敗したら中断）
      - movement: setup_user
        on_failure: continue      # 失敗しても次へ進む（既定 stop）
      - movement: web_linux
        target: verify            # このステップだけ実行先を固定（省略時は実行時の --target）

ステップ名は Movement の **ファイル名**（``movements/<name>.yml`` の ``<name>``）で
指定する。実行そのものは runner に委譲し、ここでは順序と失敗時の扱いだけを決める。
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import yaml

from .movement import Movement, MovementError, load_movement, resolve_movement_path

DEFAULT_CONDUCTORS_DIR = "conductors"

ON_FAILURE_CHOICES = ("stop", "continue")

_KNOWN_KEYS = {"name", "description", "movements"}
_STEP_KEYS = {"movement", "on_failure", "target", "no_paramsheet"}


class ConductorError(Exception):
    """Conductor 設定が不正な場合に送出する。"""


@dataclass
class Step:
    """Conductor の 1 ステップ。"""

    movement: str
    on_failure: str = "stop"
    target: Optional[str] = None
    no_paramsheet: bool = False


@dataclass
class Conductor:
    name: str
    source_path: str
    base_dir: str
    description: str = ""
    steps: List[Step] = field(default_factory=list)


@dataclass
class StepOutcome:
    """1 ステップの実行結果。"""

    index: int
    movement: str
    status: str  # success | failed | error | skipped
    returncode: Optional[int] = None

    @property
    def ok(self) -> bool:
        return self.status == "success"


@dataclass
class ConductorResult:
    conductor: str
    outcomes: List[StepOutcome] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(o.ok for o in self.outcomes)

    @property
    def returncode(self) -> int:
        """最初に失敗したステップの終了コード（無ければ 0）。"""
        for o in self.outcomes:
            if o.status in ("failed", "error"):
                return o.returncode if o.returncode else 2
        return 0


def _parse_step(raw, path: str, index: int) -> Step:
    if isinstance(raw, str):
        return Step(movement=raw)
    if not isinstance(raw, dict):
        raise ConductorError(
            f"movements[{index}] は文字列かマップである必要があります ({path})"
        )
    unknown = set(raw) - _STEP_KEYS
    if unknown:
        raise ConductorError(
            f"movements[{index}] に未知のキーがあります: "
            f"{', '.join(sorted(unknown))} ({path})"
        )
    name = raw.get("movement")
    if not name:
        raise ConductorError(f"movements[{index}] には movement が必要です ({path})")
    on_failure = str(raw.get("on_failure") or "stop")
    if on_failure not in ON_FAILURE_CHOICES:
        raise ConductorError(
            f"movements[{index}].on_failure は "
            f"{' / '.join(ON_FAILURE_CHOICES)} のいずれかです ({path})"
        )
    return Step(
        movement=str(name),
        on_failure=on_failure,
        target=raw.get("target"),
        no_paramsheet=bool(raw.get("no_paramsheet", False)),
    )


def load_conductor(path: str, base_dir: Optional[str] = None) -> Conductor:
    """Conductor YAML を読み込んで検証する。"""
    if not os.path.isfile(path):
        raise ConductorError(f"Conductor ファイルが見つかりません: {path}")

    with open(path, "r", encoding="utf-8") as fh:
        try:
            data = yaml.safe_load(fh) or {}
        except yaml.YAMLError as exc:
            raise ConductorError(f"YAML の解析に失敗しました ({path}): {exc}") from exc

    if not isinstance(data, dict):
        raise ConductorError(f"Conductor は YAML マップである必要があります: {path}")

    unknown = set(data) - _KNOWN_KEYS
    if unknown:
        raise ConductorError(
            f"未知のキーがあります: {', '.join(sorted(unknown))} ({path})"
        )

    raw_steps = data.get("movements")
    if not raw_steps:
        raise ConductorError(f"movements に 1 つ以上の Movement が必要です ({path})")
    if not isinstance(raw_steps, list):
        raise ConductorError(f"movements はリストである必要があります ({path})")

    steps = [_parse_step(raw, path, i) for i, raw in enumerate(raw_steps)]
    base_dir = os.path.abspath(base_dir) if base_dir else os.getcwd()
    name = data.get("name") or os.path.splitext(os.path.basename(path))[0]

    return Conductor(
        name=str(name),
        source_path=os.path.abspath(path),
        base_dir=base_dir,
        description=str(data.get("description") or ""),
        steps=steps,
    )


def resolve_conductor_path(name_or_path: str, conductors_dir: str) -> str:
    """Conductor 名またはパスから YAML ファイルのパスを解決する。"""
    if os.path.isfile(name_or_path):
        return name_or_path
    for ext in (".yml", ".yaml"):
        candidate = os.path.join(conductors_dir, name_or_path + ext)
        if os.path.isfile(candidate):
            return candidate
        if name_or_path.endswith(ext):
            candidate2 = os.path.join(conductors_dir, name_or_path)
            if os.path.isfile(candidate2):
                return candidate2
    raise ConductorError(
        f"Conductor '{name_or_path}' が見つかりません "
        f"(検索先: {conductors_dir}/ とカレントパス)"
    )


def list_conductor_paths(conductors_dir: str) -> List[str]:
    patterns = [
        os.path.join(conductors_dir, "*.yml"),
        os.path.join(conductors_dir, "*.yaml"),
    ]
    return sorted({p for pat in patterns for p in glob.glob(pat)})


def load_step_movement(
    cond: Conductor, step: Step, movements_dir: str
) -> Movement:
    """ステップが指す Movement を読み込む。"""
    path = resolve_movement_path(step.movement, movements_dir)
    return load_movement(path, base_dir=cond.base_dir)


def run_conductor(
    cond: Conductor,
    *,
    run_step: Callable[[Movement, Step], int],
    movements_dir: str,
    emit: Callable[[str], None] = print,
) -> ConductorResult:
    """ステップを登録順に実行する。

    実際の ansible-playbook 起動は ``run_step(movement, step)`` に委譲し
    （CLI は runner.run、WebUI はログファイルへ書き出す実行器を渡す）、
    ここでは順序・中断・結果の集計だけを行う。

    ``on_failure: stop``（既定）のステップが失敗したら以降は実行せず skipped にする。
    ``continue`` なら失敗しても次のステップへ進む。
    """
    result = ConductorResult(conductor=cond.name)
    total = len(cond.steps)
    aborted = False

    for index, step in enumerate(cond.steps, start=1):
        if aborted:
            emit(f"---------- [{index}/{total}] {step.movement} : スキップ（中断済み） ----------")
            result.outcomes.append(StepOutcome(index, step.movement, "skipped"))
            continue

        emit(f"========== [{index}/{total}] {step.movement} ==========")
        try:
            mv = load_step_movement(cond, step, movements_dir)
        except MovementError as exc:
            emit(f"エラー: {exc}")
            result.outcomes.append(StepOutcome(index, step.movement, "error"))
            if step.on_failure == "stop":
                aborted = True
            continue

        returncode = run_step(mv, step)
        if returncode == 0:
            result.outcomes.append(StepOutcome(index, step.movement, "success", 0))
            continue

        emit(f"[exalite] ステップ失敗: {step.movement} (rc={returncode})")
        result.outcomes.append(StepOutcome(index, step.movement, "failed", returncode))
        if step.on_failure == "stop":
            aborted = True
            emit("[exalite] on_failure=stop のため以降のステップを中断します。")
        else:
            emit("[exalite] on_failure=continue のため次のステップへ進みます。")

    return result


_STATUS_LABEL = {
    "success": "OK",
    "failed": "失敗",
    "error": "エラー",
    "skipped": "スキップ",
}


def format_summary(result: ConductorResult) -> List[str]:
    """結果サマリを行のリストとして返す。"""
    lines = [f"========== Conductor 結果: {result.conductor} =========="]
    for o in result.outcomes:
        label = _STATUS_LABEL.get(o.status, o.status)
        rc = f" (rc={o.returncode})" if o.status == "failed" else ""
        lines.append(f"  [{o.index}] {o.movement:<24} {label}{rc}")
    lines.append(
        "[exalite] Conductor OK" if result.ok
        else f"[exalite] Conductor 失敗 (rc={result.returncode})"
    )
    return lines
