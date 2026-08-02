"""プロジェクト設定 (exalite.yml) の読み込み。

環境（検証 / 本番）ごとのインベントリを定義する。存在しなくても動作する
（その場合は従来通り movement.inventory / localhost にフォールバック）。

exalite.yml の例::

    environments:
      verify:
        inventory: environments/verify.ini
      prod:
        inventory: environments/prod.ini
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, Optional

import yaml

CONFIG_FILENAMES = ("exalite.yml", "exalite.yaml")


class ConfigError(Exception):
    pass


@dataclass
class ProjectConfig:
    base_dir: str
    environments: Dict[str, str] = field(default_factory=dict)  # name -> inventory 絶対パス
    verify_container: Dict[str, object] = field(default_factory=dict)  # 検証コンテナ設定

    def inventory_for(self, target: str) -> str:
        if target not in self.environments:
            known = ", ".join(sorted(self.environments)) or "(未定義)"
            raise ConfigError(
                f"環境 '{target}' は exalite.yml に未定義です。定義済み: {known}"
            )
        return self.environments[target]


def find_config(base_dir: Optional[str] = None) -> Optional[str]:
    base_dir = base_dir or os.getcwd()
    for name in CONFIG_FILENAMES:
        path = os.path.join(base_dir, name)
        if os.path.isfile(path):
            return path
    return None


def load_config(base_dir: Optional[str] = None) -> ProjectConfig:
    base_dir = os.path.abspath(base_dir or os.getcwd())
    path = find_config(base_dir)
    if not path:
        return ProjectConfig(base_dir=base_dir)

    with open(path, "r", encoding="utf-8") as fh:
        try:
            data = yaml.safe_load(fh) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"exalite.yml の解析に失敗しました: {exc}") from exc

    envs: Dict[str, str] = {}
    for name, spec in (data.get("environments") or {}).items():
        if not isinstance(spec, dict) or "inventory" not in spec:
            raise ConfigError(f"environments.{name} には inventory が必要です")
        inv = spec["inventory"]
        inv = inv if os.path.isabs(inv) else os.path.join(base_dir, inv)
        envs[name] = os.path.normpath(inv)

    verify_container = data.get("verify_container") or {}
    if not isinstance(verify_container, dict):
        raise ConfigError("verify_container はマップである必要があります")

    return ProjectConfig(
        base_dir=base_dir,
        environments=envs,
        verify_container=verify_container,
    )
