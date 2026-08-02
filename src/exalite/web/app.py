"""exalite WebUI の Flask アプリ。

プロジェクトルート（サーバ起動時のカレントディレクトリ）を対象に、
機器一覧 / Movement 作成・紐付け / 実行 を提供する。
"""

from __future__ import annotations

import glob
import os
from typing import Optional

import yaml
from flask import Flask, jsonify, request, send_from_directory

from .. import env as env_mod
from ..config import ConfigError, load_config
from ..movement import MovementError, load_movement
from ..runner import PROJECT_CFG_NAME, Mode, RunnerError
from . import devices as devmod
from .executor import Executor

_STATIC = os.path.join(os.path.dirname(__file__), "static")


def create_app(base_dir: Optional[str] = None) -> Flask:
    base_dir = os.path.abspath(base_dir or os.getcwd())
    movements_dir = os.path.join(base_dir, "movements")
    roles_dir = os.path.join(base_dir, "roles")
    playbooks_dir = os.path.join(base_dir, "playbooks")
    params_dir = os.path.join(base_dir, "params")
    ansible_cfg_dir = os.path.join(base_dir, "ansible")

    app = Flask(__name__, static_folder=_STATIC, static_url_path="/static")
    executor = Executor()

    # ---- ヘルパ --------------------------------------------------------------

    def _rel(p: str) -> str:
        return os.path.relpath(p, base_dir)

    def _list_roles():
        if not os.path.isdir(roles_dir):
            return []
        out = []
        for name in sorted(os.listdir(roles_dir)):
            d = os.path.join(roles_dir, name)
            if os.path.isdir(d):
                out.append(f"roles/{name}")
        return out

    def _list_playbooks():
        out = []
        for pat in ("*.yml", "*.yaml"):
            out += [ _rel(p) for p in glob.glob(os.path.join(playbooks_dir, pat)) ]
        return sorted(out)

    def _list_paramsheets():
        if not os.path.isdir(params_dir):
            return []
        return sorted(_rel(p) for p in glob.glob(os.path.join(params_dir, "*.csv")))

    def _list_ansible_cfgs():
        """Movement に紐付けられる ansible.cfg（直下と ansible/ 配下）。"""
        out = []
        project_cfg = os.path.join(base_dir, PROJECT_CFG_NAME)
        if os.path.isfile(project_cfg):
            out.append(_rel(project_cfg))
        out += sorted(_rel(p) for p in glob.glob(os.path.join(ansible_cfg_dir, "*.cfg")))
        return out

    def _environments():
        try:
            cfg = load_config(base_dir)
        except ConfigError:
            return []
        out = []
        for name, inv in cfg.environments.items():
            out.append({
                "name": name,
                "inventory": _rel(inv),
                "exists": os.path.exists(inv),
                "generated": name == "verify",  # env up が自動生成する
            })
        return out

    def _movement_path(name: str) -> str:
        return os.path.join(movements_dir, f"{name}.yml")

    # ---- 画面 ----------------------------------------------------------------

    @app.get("/")
    def index():
        return send_from_directory(_STATIC, "index.html")

    @app.get("/api/meta")
    def meta():
        return jsonify({
            "base_dir": base_dir,
            "roles": _list_roles(),
            "playbooks": _list_playbooks(),
            "paramsheets": _list_paramsheets(),
            "ansible_cfgs": _list_ansible_cfgs(),
            "project_cfg": PROJECT_CFG_NAME if os.path.isfile(
                os.path.join(base_dir, PROJECT_CFG_NAME)) else None,
            "environments": _environments(),
            "modes": [m.value for m in Mode],
        })

    # ---- Movement ------------------------------------------------------------

    @app.get("/api/movements")
    def list_movements():
        out = []
        for pat in ("*.yml", "*.yaml"):
            for p in sorted(glob.glob(os.path.join(movements_dir, pat))):
                try:
                    mv = load_movement(p, base_dir=base_dir)
                    out.append({
                        "name": mv.name,
                        "kind": "role" if mv.role else "playbook",
                        "target_ref": _rel(mv.role) if mv.role else _rel(mv.playbook),
                        "hosts": mv.hosts,
                        "parameter_sheet": _rel(mv.parameter_sheet) if mv.parameter_sheet else None,
                        "ansible_cfg": _rel(mv.ansible_cfg) if mv.ansible_cfg else None,
                        "valid": True,
                    })
                except MovementError as exc:
                    out.append({"name": os.path.basename(p), "valid": False, "error": str(exc)})
        return jsonify(out)

    @app.get("/api/movements/<name>")
    def get_movement(name):
        path = _movement_path(name)
        if not os.path.isfile(path):
            return jsonify({"error": "not found"}), 404
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return jsonify(data)

    @app.post("/api/movements")
    def save_movement():
        body = request.get_json(force=True) or {}
        name = (body.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name は必須です"}), 400
        kind = body.get("kind") or "role"
        doc = {"name": name}
        if kind == "playbook":
            if not body.get("playbook"):
                return jsonify({"error": "playbook を選択してください"}), 400
            doc["playbook"] = body["playbook"]
        else:
            if not body.get("role"):
                return jsonify({"error": "role を選択してください"}), 400
            doc["role"] = body["role"]
        doc["hosts"] = body.get("hosts") or "all"
        if body.get("parameter_sheet"):
            doc["parameter_sheet"] = body["parameter_sheet"]
        if body.get("connection"):
            doc["connection"] = body["connection"]
        if body.get("ansible_cfg"):
            doc["ansible_cfg"] = body["ansible_cfg"]
        if body.get("become"):
            doc["become"] = True
        if body.get("gather_facts") is False:
            doc["gather_facts"] = False
        extra = body.get("extra_vars")
        if isinstance(extra, dict) and extra:
            doc["extra_vars"] = extra

        os.makedirs(movements_dir, exist_ok=True)
        path = _movement_path(name)
        with open(path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(doc, fh, allow_unicode=True, sort_keys=False)

        # 検証
        try:
            load_movement(path, base_dir=base_dir)
        except MovementError as exc:
            return jsonify({"error": f"保存しましたが検証に失敗: {exc}", "saved": _rel(path)}), 400
        return jsonify({"ok": True, "saved": _rel(path)})

    @app.delete("/api/movements/<name>")
    def delete_movement(name):
        path = _movement_path(name)
        if os.path.isfile(path):
            os.remove(path)
            return jsonify({"ok": True})
        return jsonify({"error": "not found"}), 404

    # ---- 機器一覧 (インベントリ) ---------------------------------------------

    def _env_inventory_path(env_name: str) -> Optional[str]:
        cfg = load_config(base_dir)
        return cfg.environments.get(env_name)

    @app.get("/api/devices/<env_name>")
    def get_devices(env_name):
        try:
            inv = _env_inventory_path(env_name)
        except ConfigError as exc:
            return jsonify({"error": str(exc)}), 400
        if not inv:
            return jsonify({"error": f"環境 {env_name} が未定義"}), 404
        parsed = devmod.parse(inv)
        gv = {g: "\n".join(lines).strip() for g, lines in parsed["group_vars_blocks"].items()}
        return jsonify({
            "environment": env_name,
            "path": _rel(inv),
            "generated": env_name == "verify",
            "devices": parsed["devices"],
            "group_vars": gv,
        })

    @app.post("/api/devices/<env_name>")
    def save_devices(env_name):
        try:
            inv = _env_inventory_path(env_name)
        except ConfigError as exc:
            return jsonify({"error": str(exc)}), 400
        if not inv:
            return jsonify({"error": f"環境 {env_name} が未定義"}), 404
        body = request.get_json(force=True) or {}
        devices = body.get("devices") or []
        # 既存の group:vars と header は保持
        existing = devmod.parse(inv)
        devmod.write(inv, devices, existing["group_vars_blocks"], existing["header"])
        return jsonify({"ok": True, "path": _rel(inv)})

    # ---- 実行 ----------------------------------------------------------------

    @app.post("/api/run")
    def run_movement():
        body = request.get_json(force=True) or {}
        name = body.get("movement")
        mode_str = body.get("mode") or "run"
        target = body.get("target") or None
        no_ps = bool(body.get("no_paramsheet"))
        if not name:
            return jsonify({"error": "movement は必須です"}), 400
        try:
            mv = load_movement(_movement_path(name), base_dir=base_dir)
        except MovementError as exc:
            return jsonify({"error": str(exc)}), 400
        try:
            mode = Mode(mode_str)
        except ValueError:
            return jsonify({"error": f"不正な mode: {mode_str}"}), 400
        inv_override = None
        if target:
            try:
                inv_override = load_config(base_dir).inventory_for(target)
            except ConfigError as exc:
                return jsonify({"error": str(exc)}), 400
        try:
            info = executor.start(
                mv, mode, target=target,
                inventory_override=inv_override, force_no_paramsheet=no_ps,
            )
        except RunnerError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(info.to_dict())

    @app.get("/api/runs")
    def list_runs():
        return jsonify([r.to_dict() for r in executor.list()])

    @app.get("/api/runs/<run_id>")
    def get_run(run_id):
        info = executor.get(run_id)
        if not info:
            return jsonify({"error": "not found"}), 404
        d = info.to_dict()
        d["log"] = executor.log(run_id)
        return jsonify(d)

    # ---- 検証環境(Docker)の操作 ---------------------------------------------

    @app.post("/api/env/<action>")
    def env_action(action):
        try:
            if action == "up":
                env_mod.up(base_dir, wait=True)
            elif action == "down":
                env_mod.down(base_dir)
            else:
                return jsonify({"error": "up|down のみ"}), 400
        except env_mod.EnvError as exc:
            return jsonify({"error": str(exc)}), 500
        return jsonify({"ok": True})

    return app
