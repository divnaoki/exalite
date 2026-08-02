"""Movement 設定の読み込み。

Exastro の「Movement」に相当する最小の作業単位を、1 つの YAML ファイルで表現する。
Exastro と違い、Playbook / Role はローカルのパスをそのまま参照するため、
編集したらアップロード不要でそのまま実行できる。

Movement YAML の例::

    name: install_nginx
    role: roles/nginx          # ローカル Role のパス（playbook と排他）
    # playbook: playbooks/site.yml
    hosts: web                  # 対象ホストパターン（省略時 all）
    inventory: inventory/hosts  # 省略可。無ければ parameter_sheet / localhost から生成
    parameter_sheet: params/nginx.csv   # 省略可。無ければ Role の defaults/vars を使用
    connection: ssh             # 省略可（local を指定するとローカル実行）
    become: true                # 省略可
    gather_facts: true          # 省略可（既定 true）
    ansible_cfg: ansible/verbose.cfg    # 省略可。この Movement 専用の Ansible 設定
    extra_vars:                 # 省略可。全ホスト共通の強制上書き
      VAR_env: production
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import yaml


class MovementError(Exception):
    """Movement 設定が不正な場合に送出する。"""


@dataclass
class Movement:
    """1 つの Movement 設定を表す。パスはすべて絶対パスに解決済み。"""

    name: str
    source_path: str
    base_dir: str
    role: Optional[str] = None
    playbook: Optional[str] = None
    hosts: str = "all"
    inventory: Optional[str] = None
    parameter_sheet: Optional[str] = None
    connection: Optional[str] = None
    become: bool = False
    gather_facts: bool = True
    ansible_cfg: Optional[str] = None
    extra_vars: Dict[str, Any] = field(default_factory=dict)

    @property
    def role_name(self) -> Optional[str]:
        """roles_path から解決するための Role 名（ディレクトリ名）。"""
        if not self.role:
            return None
        return os.path.basename(os.path.normpath(self.role))

    @property
    def roles_path(self) -> Optional[str]:
        """wrapper playbook から Role を解決するための roles ディレクトリ。"""
        if not self.role:
            return None
        return os.path.dirname(os.path.normpath(self.role))


_KNOWN_KEYS = {
    "name", "role", "playbook", "hosts", "inventory", "parameter_sheet",
    "connection", "become", "gather_facts", "ansible_cfg", "extra_vars",
}


def _resolve(base_dir: str, value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    if os.path.isabs(value):
        return os.path.normpath(value)
    return os.path.normpath(os.path.join(base_dir, value))


def load_movement(path: str, base_dir: Optional[str] = None) -> Movement:
    """Movement YAML ファイルを読み込んで検証する。

    role/playbook/inventory/parameter_sheet 等の相対パスは ``base_dir``
    （既定はカレントディレクトリ＝プロジェクトルート）を基準に解決する。
    Exastro 的に ``movements/`` ``roles/`` ``params/`` を直下に並べる前提。
    """
    if not os.path.isfile(path):
        raise MovementError(f"Movement ファイルが見つかりません: {path}")

    with open(path, "r", encoding="utf-8") as fh:
        try:
            data = yaml.safe_load(fh) or {}
        except yaml.YAMLError as exc:  # pragma: no cover - メッセージ整形のみ
            raise MovementError(f"YAML の解析に失敗しました ({path}): {exc}") from exc

    if not isinstance(data, dict):
        raise MovementError(f"Movement は YAML マップである必要があります: {path}")

    unknown = set(data) - _KNOWN_KEYS
    if unknown:
        raise MovementError(
            f"未知のキーがあります: {', '.join(sorted(unknown))} ({path})"
        )

    base_dir = os.path.abspath(base_dir) if base_dir else os.getcwd()
    name = data.get("name") or os.path.splitext(os.path.basename(path))[0]

    role = data.get("role")
    playbook = data.get("playbook")
    if role and playbook:
        raise MovementError(f"role と playbook は同時に指定できません ({path})")
    if not role and not playbook:
        raise MovementError(f"role か playbook のいずれかが必要です ({path})")

    extra_vars = data.get("extra_vars") or {}
    if not isinstance(extra_vars, dict):
        raise MovementError(f"extra_vars はマップである必要があります ({path})")

    mv = Movement(
        name=str(name),
        source_path=os.path.abspath(path),
        base_dir=base_dir,
        role=_resolve(base_dir, role),
        playbook=_resolve(base_dir, playbook),
        hosts=str(data.get("hosts") or "all"),
        inventory=_resolve(base_dir, data.get("inventory")),
        parameter_sheet=_resolve(base_dir, data.get("parameter_sheet")),
        connection=data.get("connection"),
        become=bool(data.get("become", False)),
        gather_facts=bool(data.get("gather_facts", True)),
        ansible_cfg=_resolve(base_dir, data.get("ansible_cfg")),
        extra_vars=extra_vars,
    )

    if mv.role and not os.path.isdir(mv.role):
        raise MovementError(f"Role ディレクトリが見つかりません: {mv.role}")
    if mv.playbook and not os.path.isfile(mv.playbook):
        raise MovementError(f"Playbook が見つかりません: {mv.playbook}")
    if mv.inventory and not os.path.exists(mv.inventory):
        raise MovementError(f"インベントリが見つかりません: {mv.inventory}")
    if mv.parameter_sheet and not os.path.isfile(mv.parameter_sheet):
        raise MovementError(
            f"パラメータシートが見つかりません: {mv.parameter_sheet}"
        )
    if mv.ansible_cfg and not os.path.isfile(mv.ansible_cfg):
        raise MovementError(f"ansible.cfg が見つかりません: {mv.ansible_cfg}")

    return mv
