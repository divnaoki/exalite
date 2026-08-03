"""exalite CLI。

サブコマンド:
  list     Movement 一覧を表示
  syntax   構文チェック (ansible-playbook --syntax-check)
  dryrun   ドライラン (ansible-playbook --check --diff)
  run      実行（--target で検証/本番を切替）
  verify   検証環境(Docker)で実行（run --target verify の別名）
  promote  検証環境で実行し、成功したら本番環境へ投入
  conductor Movement を登録順に実行する作業フロー (list/run)
  watch    ファイル変更を監視して自動で構文チェック（ローカル Sandbox ループ）
  env      検証用サーバ環境の管理 (up/down/status/check-win)
  web      簡易 WebUI を起動（機器一覧/Movement作成・紐付け/実行）
  init     サンプルプロジェクトを生成
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
from typing import List, Optional

from . import __version__, env as env_mod
from .conductor import (
    DEFAULT_CONDUCTORS_DIR,
    ConductorError,
    format_summary,
    list_conductor_paths,
    load_conductor,
    resolve_conductor_path,
    run_conductor,
)
from .config import ConfigError, load_config
from .movement import Movement, MovementError, load_movement, resolve_movement_path
from .runner import Mode, RunnerError, run
from .watcher import watch as watch_files

DEFAULT_MOVEMENTS_DIR = "movements"


def _inventory_for_target(target: Optional[str]) -> Optional[str]:
    """--target 名から exalite.yml のインベントリ絶対パスを解決する。"""
    if not target:
        return None
    cfg = load_config()
    return cfg.inventory_for(target)


def _load(name_or_path: str, movements_dir: str) -> Movement:
    return load_movement(resolve_movement_path(name_or_path, movements_dir))


def _cmd_list(args: argparse.Namespace) -> int:
    patterns = [
        os.path.join(args.movements_dir, "*.yml"),
        os.path.join(args.movements_dir, "*.yaml"),
    ]
    paths = sorted({p for pat in patterns for p in glob.glob(pat)})
    if not paths:
        print(f"Movement がありません ({args.movements_dir}/)。'exalite init' で雛形を作成できます。")
        return 0
    print(f"{'NAME':<24} {'KIND':<8} {'PARAM SHEET':<12} SOURCE")
    for p in paths:
        try:
            mv = load_movement(p)
        except MovementError as exc:
            print(f"{'(不正)':<24} {'-':<8} {'-':<12} {p}  # {exc}")
            continue
        kind = "role" if mv.role else "playbook"
        sheet = "あり" if mv.parameter_sheet else "なし"
        print(f"{mv.name:<24} {kind:<8} {sheet:<12} {os.path.relpath(p)}")
    return 0


def _run_mode(args: argparse.Namespace, mode: Mode, target: Optional[str] = None) -> int:
    target = target or getattr(args, "target", None)
    try:
        mv = _load(args.movement, args.movements_dir)
        inventory_override = _inventory_for_target(target)
        result = run(
            mv,
            mode,
            force_no_paramsheet=getattr(args, "no_paramsheet", False),
            inventory_override=inventory_override,
            target=target,
            extra_args=args.ansible_args or None,
        )
    except (MovementError, RunnerError, ConfigError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 2
    if result.ok:
        print(f"\n[exalite] OK ({mode.value})")
    else:
        print(f"\n[exalite] 失敗 ({mode.value}) rc={result.returncode}", file=sys.stderr)
    return result.returncode


def _cmd_verify(args: argparse.Namespace) -> int:
    return _run_mode(args, Mode.RUN, target="verify")


def _cmd_promote(args: argparse.Namespace) -> int:
    """検証環境で実行し、成功したら本番環境へ投入する。"""
    try:
        mv = _load(args.movement, args.movements_dir)
        verify_inv = _inventory_for_target("verify")
        prod_inv = _inventory_for_target("prod")
    except (MovementError, ConfigError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 2

    print("========== [1/2] 検証環境 (verify) で実行 ==========")
    try:
        v = run(mv, Mode.RUN, inventory_override=verify_inv, target="verify",
                force_no_paramsheet=args.no_paramsheet)
    except RunnerError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 2
    if not v.ok:
        print(f"\n[exalite] 検証で失敗 (rc={v.returncode})。本番へは進みません。", file=sys.stderr)
        return v.returncode
    print("\n[exalite] 検証成功。")

    # 本番は不可逆・外向きの操作のため、明示確認を必須にする。
    if not args.yes:
        try:
            ans = input("本番環境 (prod) へ投入しますか? [y/N]: ").strip().lower()
        except EOFError:
            ans = ""
        if ans not in ("y", "yes"):
            print("[exalite] 本番投入を中止しました。")
            return 0

    print("\n========== [2/2] 本番環境 (prod) で実行 ==========")
    try:
        p = run(mv, Mode.RUN, inventory_override=prod_inv, target="prod",
                force_no_paramsheet=args.no_paramsheet)
    except RunnerError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 2
    if p.ok:
        print("\n[exalite] 本番実行 OK。")
    else:
        print(f"\n[exalite] 本番実行 失敗 (rc={p.returncode})", file=sys.stderr)
    return p.returncode


def _cmd_conductor(args: argparse.Namespace) -> int:
    if args.conductor_action == "list":
        return _conductor_list(args)
    return _conductor_run(args)


def _conductor_list(args: argparse.Namespace) -> int:
    paths = list_conductor_paths(args.conductors_dir)
    if not paths:
        print(
            f"Conductor がありません ({args.conductors_dir}/)。"
            "'exalite init' で雛形を作成できます。"
        )
        return 0
    print(f"{'NAME':<24} {'STEPS':<6} MOVEMENTS")
    for p in paths:
        try:
            cond = load_conductor(p)
        except ConductorError as exc:
            print(f"{'(不正)':<24} {'-':<6} {p}  # {exc}")
            continue
        flow = " → ".join(
            s.movement + ("*" if s.on_failure == "continue" else "") for s in cond.steps
        )
        print(f"{cond.name:<24} {len(cond.steps):<6} {flow}")
    print("\n* = on_failure: continue（失敗しても次へ進む）")
    return 0


def _conductor_run(args: argparse.Namespace) -> int:
    """Conductor のステップを登録順に実行する。"""
    try:
        cond = load_conductor(
            resolve_conductor_path(args.conductor, args.conductors_dir)
        )
        mode = Mode(args.mode)
    except ConductorError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 2

    print(f"[exalite] Conductor={cond.name} mode={mode.value} "
          f"target={args.target or '(既定)'} steps={len(cond.steps)}")
    if cond.description:
        print(f"[exalite] {cond.description}")

    def _run_step(mv: Movement, step) -> int:
        target = step.target or args.target
        try:
            inventory_override = _inventory_for_target(target)
            result = run(
                mv, mode,
                force_no_paramsheet=args.no_paramsheet or step.no_paramsheet,
                inventory_override=inventory_override,
                target=target,
                extra_args=args.ansible_args or None,
            )
        except (RunnerError, ConfigError) as exc:
            print(f"エラー: {exc}", file=sys.stderr)
            return 2
        return result.returncode

    result = run_conductor(
        cond, run_step=_run_step, movements_dir=args.movements_dir,
    )
    print()
    for line in format_summary(result):
        print(line)
    return result.returncode


def _cmd_web(args: argparse.Namespace) -> int:
    try:
        from .web.app import create_app
    except ImportError as exc:
        print(f"エラー: WebUI には Flask が必要です (pip install flask)。 {exc}", file=sys.stderr)
        return 2
    app = create_app(os.getcwd())
    print(f"[exalite] WebUI: http://{args.host}:{args.port}  (Ctrl-C で終了)")
    print(f"[exalite] 対象プロジェクト: {os.getcwd()}")
    app.run(host=args.host, port=args.port, debug=False, threaded=True)
    return 0


def _cmd_env(args: argparse.Namespace) -> int:
    base = os.getcwd()
    try:
        if args.env_action == "up":
            env_mod.up(base, wait=not args.no_wait)
        elif args.env_action == "down":
            return env_mod.down(base, volumes=not args.keep_volumes)
        elif args.env_action == "status":
            return env_mod.status(base)
        elif args.env_action == "check-win":
            return env_mod.check_win(base)
    except env_mod.EnvError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 2
    return 0


def _cmd_watch(args: argparse.Namespace) -> int:
    try:
        mv = _load(args.movement, args.movements_dir)
    except MovementError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 2
    mode = Mode(args.on_change)

    def _on_change(m: Movement) -> None:
        try:
            run(m, mode, force_no_paramsheet=args.no_paramsheet, quiet=False)
        except RunnerError as exc:
            print(f"エラー: {exc}", file=sys.stderr)

    watch_files(mv, _on_change, interval=args.interval)
    return 0


def _cmd_init(args: argparse.Namespace) -> int:
    from .scaffold import scaffold
    dest = os.path.abspath(args.dir)
    created = scaffold(dest)
    created += [os.path.join(dest, p) for p in env_mod.ensure_docker_assets(dest)]
    # Windows 検証機のインベントリ雛形（実機/VM を WinRM で登録する）
    env_mod.ensure_windows_inventory(dest)
    created.append(os.path.join(dest, env_mod._VERIFY_WIN_INI_REL))
    print(f"サンプルを作成しました: {dest}")
    for f in created:
        print(f"  + {os.path.relpath(f, dest)}")
    print("\n次のコマンドで試せます:")
    rel = os.path.relpath(dest) if dest != os.getcwd() else "."
    prefix = f"cd {rel} && " if rel != "." else ""
    print(f"  {prefix}exalite syntax hello        # ① 構文チェック（アップロード不要）")
    print(f"  {prefix}exalite run hello           # ② パラメータシート無しで実行")
    print(f"  {prefix}exalite env up              # 検証コンテナ(AlmaLinux)を起動")
    print(f"  {prefix}exalite verify ping_linux   # 検証コンテナへ疎通")
    print(f"  {prefix}exalite promote ping_linux  # 検証OKなら本番へ投入")
    print(f"  {prefix}exalite env check-win       # Windows 検証機(実機/VM)へ WinRM 疎通")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="exalite",
        description="Exastro-lite: ローカル Ansible Role を再アップロード不要で"
        "構文チェック/ドライラン/実行する軽量 CLI。パラメータシートは任意。",
    )
    parser.add_argument("--version", action="version", version=f"exalite {__version__}")
    parser.add_argument(
        "--movements-dir", default=DEFAULT_MOVEMENTS_DIR,
        help=f"Movement ディレクトリ (既定: {DEFAULT_MOVEMENTS_DIR})",
    )
    parser.add_argument(
        "--conductors-dir", default=DEFAULT_CONDUCTORS_DIR,
        help=f"Conductor ディレクトリ (既定: {DEFAULT_CONDUCTORS_DIR})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="Movement 一覧")
    p_list.set_defaults(func=_cmd_list)

    def _add_run_like(name: str, help_: str, mode: Mode):
        p = sub.add_parser(name, help=help_)
        p.add_argument("movement", help="Movement 名またはファイルパス")
        p.add_argument(
            "--no-paramsheet", action="store_true",
            help="パラメータシートを無視して Role の defaults/vars のみで実行",
        )
        p.add_argument(
            "--target", default=None,
            help="実行先環境 (exalite.yml の environments。例: verify, prod)",
        )
        p.set_defaults(func=lambda a, _m=mode: _run_mode(a, _m))
        return p

    _add_run_like("syntax", "構文チェック (--syntax-check)", Mode.SYNTAX)
    _add_run_like("dryrun", "ドライラン (--check --diff)", Mode.DRYRUN)
    _add_run_like("run", "実行", Mode.RUN)

    # verify: run --target verify の別名
    p_verify = sub.add_parser("verify", help="検証環境(Docker)で実行")
    p_verify.add_argument("movement", help="Movement 名またはファイルパス")
    p_verify.add_argument("--no-paramsheet", action="store_true")
    p_verify.set_defaults(func=_cmd_verify)

    # promote: 検証→本番
    p_promote = sub.add_parser("promote", help="検証で実行し成功したら本番へ投入")
    p_promote.add_argument("movement", help="Movement 名またはファイルパス")
    p_promote.add_argument("--no-paramsheet", action="store_true")
    p_promote.add_argument(
        "-y", "--yes", action="store_true", help="本番投入の確認を省略",
    )
    p_promote.set_defaults(func=_cmd_promote)

    # env: 検証用サーバ環境(Docker)の管理
    p_env = sub.add_parser("env", help="検証用サーバ環境(Docker)の管理")
    env_sub = p_env.add_subparsers(dest="env_action", required=True)
    p_env_up = env_sub.add_parser("up", help="検証コンテナをビルド＆起動")
    p_env_up.add_argument("--no-wait", action="store_true", help="sshd 起動待ちをしない")
    p_env_down = env_sub.add_parser("down", help="検証コンテナを停止・破棄")
    p_env_down.add_argument("--keep-volumes", action="store_true", help="ボリュームを残す")
    env_sub.add_parser("status", help="検証コンテナの状態表示")
    env_sub.add_parser(
        "check-win", help="Windows 検証機への WinRM 疎通確認 (win_ping)",
    )
    p_env.set_defaults(func=_cmd_env)

    # conductor: Movement を登録順に実行
    p_cond = sub.add_parser("conductor", help="Conductor（Movement を登録順に実行）")
    cond_sub = p_cond.add_subparsers(dest="conductor_action", required=True)
    cond_sub.add_parser("list", help="Conductor 一覧")
    p_cond_run = cond_sub.add_parser("run", help="Conductor を登録順に実行")
    p_cond_run.add_argument("conductor", help="Conductor 名またはファイルパス")
    p_cond_run.add_argument(
        "--mode", choices=[m.value for m in Mode], default=Mode.RUN.value,
        help="各ステップの実行モード (既定: run)",
    )
    p_cond_run.add_argument(
        "--target", default=None,
        help="実行先環境 (ステップ側の target が優先。例: verify, prod)",
    )
    p_cond_run.add_argument(
        "--no-paramsheet", action="store_true",
        help="全ステップでパラメータシートを無視",
    )
    p_cond.set_defaults(func=_cmd_conductor, target=None, no_paramsheet=False,
                        mode=Mode.RUN.value)

    p_watch = sub.add_parser("watch", help="変更監視して自動チェック")
    p_watch.add_argument("movement", help="Movement 名またはファイルパス")
    p_watch.add_argument(
        "--on-change", choices=[m.value for m in Mode], default=Mode.SYNTAX.value,
        help="変更検知時に実行するモード (既定: syntax)",
    )
    p_watch.add_argument("--interval", type=float, default=1.0, help="ポーリング間隔秒")
    p_watch.add_argument("--no-paramsheet", action="store_true")
    p_watch.set_defaults(func=_cmd_watch)

    p_web = sub.add_parser("web", help="簡易 WebUI を起動")
    p_web.add_argument("--host", default="127.0.0.1", help="バインドホスト (既定 127.0.0.1)")
    p_web.add_argument("--port", type=int, default=8765, help="ポート (既定 8765)")
    p_web.set_defaults(func=_cmd_web)

    p_init = sub.add_parser("init", help="サンプルプロジェクトを生成")
    p_init.add_argument("dir", nargs="?", default=".", help="生成先 (既定: カレント)")
    p_init.set_defaults(func=_cmd_init)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    # '--' 以降は ansible-playbook へそのまま渡す passthrough として分離する。
    passthrough: List[str] = []
    if "--" in argv:
        idx = argv.index("--")
        passthrough = argv[idx + 1:]
        argv = argv[:idx]
    parser = build_parser()
    args = parser.parse_args(argv)
    args.ansible_args = passthrough or None
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
