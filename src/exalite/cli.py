"""exalite CLI。

サブコマンド:
  list     Movement 一覧を表示
  syntax   構文チェック (ansible-playbook --syntax-check)
  dryrun   ドライラン (ansible-playbook --check --diff)
  run      実行（--target で検証/本番を切替）
  verify   検証環境(Docker)で実行（run --target verify の別名）
  promote  検証環境で実行し、成功したら本番環境へ投入
  watch    ファイル変更を監視して自動で構文チェック（ローカル Sandbox ループ）
  env      検証用サーバ環境(Docker)の管理 (up/down/status)
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
from .config import ConfigError, load_config
from .movement import Movement, MovementError, load_movement
from .runner import Mode, RunnerError, run
from .watcher import watch as watch_files

DEFAULT_MOVEMENTS_DIR = "movements"


def _inventory_for_target(target: Optional[str]) -> Optional[str]:
    """--target 名から exalite.yml のインベントリ絶対パスを解決する。"""
    if not target:
        return None
    cfg = load_config()
    return cfg.inventory_for(target)


def _find_movement(name_or_path: str, movements_dir: str) -> str:
    """Movement 名またはパスから YAML ファイルのパスを解決する。"""
    if os.path.isfile(name_or_path):
        return name_or_path
    for ext in (".yml", ".yaml"):
        candidate = os.path.join(movements_dir, name_or_path + ext)
        if os.path.isfile(candidate):
            return candidate
        # 拡張子付きで渡されたケース
        if name_or_path.endswith(ext):
            candidate2 = os.path.join(movements_dir, name_or_path)
            if os.path.isfile(candidate2):
                return candidate2
    raise MovementError(
        f"Movement '{name_or_path}' が見つかりません "
        f"(検索先: {movements_dir}/ とカレントパス)"
    )


def _load(name_or_path: str, movements_dir: str) -> Movement:
    return load_movement(_find_movement(name_or_path, movements_dir))


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
    p_env.set_defaults(func=_cmd_env)

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
