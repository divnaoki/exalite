"""ローカル実行ランナー。

Exastro が内部で行っている「Ansible 実行用のディレクトリ・変数ファイル・
インベントリの自動生成」を、ローカルで透過的に再現する。

生成物は `<base>/.exalite/runs/<movement>-<timestamp>/` に置かれる::

    playbook.yml            role の場合の wrapper playbook（playbook 指定時はそれを使用）
    inventory/hosts         インベントリ（config 指定 > パラメータシート > localhost）
    inventory/host_vars/*   パラメータシート由来の host_vars（Role defaults を上書き）
    extra_vars.yml          movement の extra_vars（-e で最優先適用）
    ansible.cfg             roles_path 等（外部 ansible.cfg のマージ結果）

ansible.cfg は「exalite の既定値 → プロジェクト直下の ansible.cfg →
Movement の ansible_cfg」の順に後勝ちでマージし、ANSIBLE_CONFIG で渡す。
利用者が手で編集するのは前者 2 つ（外部ファイル）で、run_dir のものは毎回の生成物。
"""

from __future__ import annotations

import configparser
import datetime as _dt
import os
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

import yaml

from .movement import Movement
from .paramsheet import ParamSheet, load_paramsheet


# プロジェクト直下に置く、手編集用の共通 Ansible 設定ファイル名。
PROJECT_CFG_NAME = "ansible.cfg"


class Mode(str, Enum):
    SYNTAX = "syntax"   # --syntax-check
    DRYRUN = "dryrun"   # --check --diff
    RUN = "run"         # 通常実行


class RunnerError(Exception):
    pass


@dataclass
class RunResult:
    returncode: int
    command: List[str]
    run_dir: str
    used_param_sheet: bool

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _timestamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def _ensure_ansible_playbook() -> str:
    path = shutil.which("ansible-playbook")
    if not path:
        raise RunnerError(
            "ansible-playbook が見つかりません。Ansible をインストールしてください "
            "(例: pip install ansible)。"
        )
    return path


def _build_run_dir(mv: Movement, mode: Mode) -> str:
    root = os.path.join(mv.base_dir, ".exalite", "runs")
    run_dir = os.path.join(root, f"{mv.name}-{mode.value}-{_timestamp()}")
    os.makedirs(os.path.join(run_dir, "inventory", "host_vars"), exist_ok=True)
    return run_dir


def _write_wrapper_playbook(mv: Movement, run_dir: str) -> str:
    """role 用の wrapper playbook を生成。playbook 指定時はそのパスを返す。"""
    if mv.playbook:
        return mv.playbook

    play = {
        "name": f"exalite: {mv.name}",
        "hosts": mv.hosts,
        "gather_facts": mv.gather_facts,
        "become": mv.become,
        "roles": [mv.role_name],
    }
    if mv.connection:
        play["connection"] = mv.connection

    path = os.path.join(run_dir, "playbook.yml")
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump([play], fh, allow_unicode=True, sort_keys=False)
    return path


def _write_inventory(
    mv: Movement,
    sheet: Optional[ParamSheet],
    run_dir: str,
    inventory_override: Optional[str] = None,
) -> str:
    """インベントリを決定。

    優先順位: inventory_override(--target) > movement.inventory
              > パラメータシート由来 > 暗黙の localhost。
    パラメータシートがある場合は host_vars ファイルも書き出す。
    """
    inv_dir = os.path.join(run_dir, "inventory")
    hosts_file = os.path.join(inv_dir, "hosts")

    # host_vars（パラメータシートがある場合のみ）
    if sheet:
        for host, values in sheet.host_vars.items():
            hv_path = os.path.join(inv_dir, "host_vars", f"{host}.yml")
            with open(hv_path, "w", encoding="utf-8") as fh:
                yaml.safe_dump(values, fh, allow_unicode=True, sort_keys=False)

    # 0) --target 由来のインベントリ（最優先）
    effective_inventory = inventory_override or mv.inventory

    # 1) 明示インベントリ
    if effective_inventory:
        # 実インベントリを直接指す。host_vars を効かせるため両方を返せないので、
        # 明示インベントリを inventory ディレクトリにコピーして host_vars と同居させる。
        dest = os.path.join(inv_dir, "hosts")
        if os.path.isdir(effective_inventory):
            # ディレクトリインベントリはそのまま利用（host_vars は本ツール側を優先しない）
            return effective_inventory
        shutil.copyfile(effective_inventory, dest)
        return inv_dir

    # 2) パラメータシート由来
    if sheet:
        lines = list(sheet.hosts)
        with open(hosts_file, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        return inv_dir

    # 3) 暗黙 localhost
    with open(hosts_file, "w", encoding="utf-8") as fh:
        fh.write("localhost ansible_connection=local\n")
    return inv_dir


def _write_extra_vars(mv: Movement, run_dir: str) -> Optional[str]:
    if not mv.extra_vars:
        return None
    path = os.path.join(run_dir, "extra_vars.yml")
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(mv.extra_vars, fh, allow_unicode=True, sort_keys=False)
    return path


def _dedup(paths: List[str]) -> List[str]:
    """順序を保ったまま重複を除去する。"""
    seen = set()
    return [p for p in paths if not (p in seen or seen.add(p))]


def _exalite_roles_paths(mv: Movement) -> List[str]:
    """wrapper playbook から Role を解決するための roles_path を組み立てる。"""
    roles_paths = []
    if mv.roles_path:
        roles_paths.append(os.path.abspath(mv.roles_path))
    if mv.playbook:
        pb_dir = os.path.dirname(mv.playbook)
        roles_paths.extend([pb_dir, os.path.join(pb_dir, "roles")])
    roles_paths.append(mv.base_dir)
    roles_paths.append(os.path.join(mv.base_dir, "roles"))
    return _dedup(roles_paths)


def cfg_sources(mv: Movement) -> List[str]:
    """適用する外部 ansible.cfg を、適用順（後勝ち）で返す。

    1. プロジェクト直下の ansible.cfg（あれば全 Movement 共通で適用）
    2. Movement の ansible_cfg（その Movement 専用。1 を上書き）
    """
    sources = []
    project_cfg = os.path.join(mv.base_dir, PROJECT_CFG_NAME)
    if os.path.isfile(project_cfg):
        sources.append(project_cfg)
    if mv.ansible_cfg:
        sources.append(mv.ansible_cfg)
    return _dedup([os.path.abspath(p) for p in sources])


def _write_ansible_cfg(mv: Movement, run_dir: str) -> str:
    """外部 ansible.cfg をマージし、実行用の ansible.cfg を生成する。

    exalite の既定値を土台に、プロジェクト直下 → Movement の順で上書きする。
    roles_path だけは Role 解決に必須のため、利用者の指定を優先しつつ
    exalite が算出した解決先を後ろに追記する。
    """
    parser = configparser.ConfigParser(
        interpolation=None, strict=False, allow_no_value=True
    )
    parser.optionxform = str  # キーの大文字小文字をそのまま保持する
    parser["defaults"] = {
        "host_key_checking": "False",
        "retry_files_enabled": "False",
    }

    sources = cfg_sources(mv)
    for src in sources:
        try:
            parser.read(src, encoding="utf-8")
        except (configparser.Error, OSError) as exc:
            raise RunnerError(f"ansible.cfg の読み込みに失敗しました ({src}): {exc}") from exc

    if not parser.has_section("defaults"):
        parser.add_section("defaults")
    user_roles = parser.get("defaults", "roles_path", fallback="") or ""
    roles_paths = [p for p in user_roles.split(os.pathsep) if p.strip()]
    roles_paths += _exalite_roles_paths(mv)
    parser.set("defaults", "roles_path", os.pathsep.join(_dedup(roles_paths)))

    path = os.path.join(run_dir, "ansible.cfg")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# exalite が実行のたびに自動生成するマージ結果。\n")
        fh.write("# このファイルを直接編集しても次回実行には残りません。\n")
        if sources:
            fh.write("# 反映元（下ほど優先）:\n")
            for src in sources:
                fh.write(f"#   {src}\n")
        else:
            fh.write("# 反映元: exalite の既定値のみ\n")
            fh.write(f"#   （{PROJECT_CFG_NAME} も Movement の ansible_cfg も未配置）\n")
        fh.write("\n")
        parser.write(fh)
    return path


def prepare(
    mv: Movement,
    mode: Mode,
    *,
    force_no_paramsheet: bool = False,
    inventory_override: Optional[str] = None,
):
    """実行ディレクトリを組み立て、(コマンド, run_dir, cfg, used_sheet) を返す。"""
    ansible_playbook = _ensure_ansible_playbook()

    sheet: Optional[ParamSheet] = None
    if mv.parameter_sheet and not force_no_paramsheet:
        sheet = load_paramsheet(mv.parameter_sheet)

    run_dir = _build_run_dir(mv, mode)
    playbook = _write_wrapper_playbook(mv, run_dir)
    inventory = _write_inventory(mv, sheet, run_dir, inventory_override)
    extra_vars_file = _write_extra_vars(mv, run_dir)
    cfg = _write_ansible_cfg(mv, run_dir)

    cmd = [ansible_playbook, "-i", inventory, playbook]
    if mode is Mode.SYNTAX:
        cmd.append("--syntax-check")
    elif mode is Mode.DRYRUN:
        cmd.extend(["--check", "--diff"])
    if extra_vars_file:
        cmd.extend(["-e", f"@{extra_vars_file}"])

    return cmd, run_dir, cfg, (sheet is not None)


def run(
    mv: Movement,
    mode: Mode,
    *,
    force_no_paramsheet: bool = False,
    inventory_override: Optional[str] = None,
    target: Optional[str] = None,
    extra_args: Optional[List[str]] = None,
    quiet: bool = False,
) -> RunResult:
    """指定モードで ansible-playbook を起動する。"""
    cmd, run_dir, cfg, used_sheet = prepare(
        mv, mode,
        force_no_paramsheet=force_no_paramsheet,
        inventory_override=inventory_override,
    )
    if extra_args:
        cmd.extend(extra_args)

    env = dict(os.environ)
    env["ANSIBLE_CONFIG"] = cfg
    env.setdefault("ANSIBLE_FORCE_COLOR", "1")

    if not quiet:
        sources = cfg_sources(mv)
        print(f"[exalite] mode={mode.value} movement={mv.name} target={target or '(既定)'}")
        print(f"[exalite] parameter_sheet={'使用' if used_sheet else '未使用 (Role の defaults/vars を使用)'}")
        print(f"[exalite] ansible.cfg={' + '.join(sources) if sources else '既定値のみ'}")
        print(f"[exalite] run_dir={run_dir}")
        print(f"[exalite] $ {' '.join(cmd)}\n")

    proc = subprocess.run(cmd, env=env)
    return RunResult(
        returncode=proc.returncode,
        command=cmd,
        run_dir=run_dir,
        used_param_sheet=used_sheet,
    )
