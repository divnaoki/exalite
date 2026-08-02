"""検証用サーバ環境（Docker）の管理。

AlmaLinux(9-init) ベースの systemd 稼働コンテナを起動し、Ansible が疎通できる
本番寄りの検証環境を用意する。systemd が PID1 で動くため、Playbook 中の
`systemctl`/`service`/handler によるサービス管理を本番同様に検証できる。
AlmaLinux は RHEL 互換かつサブスクリプション不要のため、認証なしで pull できる。

導入ミドルや有効化サービス、ベースイメージは exalite.yml の `verify_container`
ブロックで調整できる（既定は AlmaLinux 9 + httpd）。

注意: Windows Server コンテナは Windows ホスト上（Docker の windows-container モード）
でのみ動作するため、macOS/Linux ホストでは起動できない。ここでは Linux(AlmaLinux)
のみを対象とする。
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from typing import List, Optional

from .config import load_config

# 既定値（exalite.yml の verify_container で上書き可能）
DEFAULT_BASE_IMAGE = "almalinux/9-init:latest"
DEFAULT_PACKAGES = ["httpd"]
DEFAULT_ENABLE_SERVICES = ["sshd", "httpd"]

# ---- Docker アセットのテンプレート -------------------------------------------

_COMPOSE_YML = """\
# exalite 検証環境。`exalite env up` から利用される。
# systemd を PID1 で動かすため cgroup / cap 等の設定を付与している。
name: exalite-verify

services:
  linux:
    build:
      context: ./linux
      args:
        BASE_IMAGE: "${EXALITE_BASE_IMAGE:-almalinux/9-init:latest}"
        PUBKEY: "${EXALITE_PUBKEY:-}"
        EXTRA_PACKAGES: "${EXALITE_PACKAGES:-httpd}"
        ENABLE_SERVICES: "${EXALITE_ENABLE_SERVICES:-sshd httpd}"
    image: exalite-verify-linux:latest
    container_name: exalite-verify-linux
    hostname: verify-linux
    # --- systemd(PID1) を動かすための設定 ---
    cgroup: host
    cap_add:
      - SYS_ADMIN
    security_opt:
      - seccomp:unconfined
    stop_signal: SIGRTMIN+3
    tmpfs:
      - /run
      - /tmp
    volumes:
      - /sys/fs/cgroup:/sys/fs/cgroup:rw
    ports:
      - "2222:22"
      - "8080:80"    # 検証用 httpd を host から確認する場合に使用
"""

_DOCKERFILE = """\
# 本番寄りの検証コンテナ: AlmaLinux(9-init) + systemd + sshd + python3 + ミドル。
# AlmaLinux は RHEL 互換でサブスクリプション不要のため、認証なしで pull できる。
ARG BASE_IMAGE=almalinux/9-init:latest
FROM ${BASE_IMAGE}

ARG PUBKEY=""
ARG EXTRA_PACKAGES=""
ARG ENABLE_SERVICES="sshd"

RUN dnf -y install --setopt=install_weak_deps=False \\
        openssh-server python3 sudo procps-ng iproute ${EXTRA_PACKAGES} && \\
    dnf clean all && \\
    useradd -m -s /bin/bash ansible && \\
    echo 'ansible ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/ansible && \\
    chmod 440 /etc/sudoers.d/ansible && \\
    mkdir -p /home/ansible/.ssh && \\
    printf '%s\\n' "$PUBKEY" > /home/ansible/.ssh/authorized_keys && \\
    chmod 700 /home/ansible/.ssh && \\
    chmod 600 /home/ansible/.ssh/authorized_keys && \\
    chown -R ansible:ansible /home/ansible/.ssh && \\
    # root ログイン無効・パスワード認証無効（鍵のみ）
    sed -i 's/^#\\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config && \\
    sed -i 's/^#\\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config && \\
    # systemd により起動するサービスを有効化（オフラインでの symlink 作成）
    for svc in ${ENABLE_SERVICES}; do systemctl enable "$svc"; done

EXPOSE 22
# 9-init の CMD (/sbin/init = systemd) をそのまま使うため CMD は上書きしない。
"""

_VERIFY_INI_TEMPLATE = """\
# exalite env up により自動生成。検証用 Docker コンテナ(systemd 稼働)を指す。
[linux]
verify-linux ansible_host=127.0.0.1 ansible_port=2222

[linux:vars]
ansible_user=ansible
ansible_ssh_private_key_file={key_path}
ansible_python_interpreter=/usr/bin/python3
ansible_ssh_common_args=-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
"""

_DOCKER_DIR = "docker"
_COMPOSE_REL = os.path.join(_DOCKER_DIR, "docker-compose.yml")
_DOCKERFILE_REL = os.path.join(_DOCKER_DIR, "linux", "Dockerfile")
_SSH_DIR_REL = os.path.join("environments", "verify", "ssh")
_KEY_REL = os.path.join(_SSH_DIR_REL, "id_ed25519")
_VERIFY_INI_REL = os.path.join("environments", "verify.ini")


class EnvError(Exception):
    pass


def _write_if_absent(path: str, content: str) -> bool:
    if os.path.exists(path):
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return True


def ensure_docker_assets(base_dir: str) -> List[str]:
    """docker-compose.yml / Dockerfile を無ければ作成する。"""
    created = []
    if _write_if_absent(os.path.join(base_dir, _COMPOSE_REL), _COMPOSE_YML):
        created.append(_COMPOSE_REL)
    if _write_if_absent(os.path.join(base_dir, _DOCKERFILE_REL), _DOCKERFILE):
        created.append(_DOCKERFILE_REL)
    return created


def ensure_keypair(base_dir: str) -> str:
    """検証用 SSH 鍵ペアを無ければ生成し、秘密鍵の絶対パスを返す。"""
    key_path = os.path.join(base_dir, _KEY_REL)
    if os.path.isfile(key_path) and os.path.isfile(key_path + ".pub"):
        return key_path
    os.makedirs(os.path.dirname(key_path), exist_ok=True)
    if not shutil.which("ssh-keygen"):
        raise EnvError("ssh-keygen が見つかりません。")
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-N", "", "-C", "exalite-verify",
         "-f", key_path],
        check=True, capture_output=True,
    )
    os.chmod(key_path, 0o600)
    return key_path


def _read_pubkey(key_path: str) -> str:
    with open(key_path + ".pub", "r", encoding="utf-8") as fh:
        return fh.read().strip()


def write_verify_inventory(base_dir: str, key_path: str) -> str:
    path = os.path.join(base_dir, _VERIFY_INI_REL)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(_VERIFY_INI_TEMPLATE.format(key_path=os.path.abspath(key_path)))
    return path


def _compose_cmd(base_dir: str, *args: str) -> List[str]:
    compose = os.path.join(base_dir, _COMPOSE_REL)
    return ["docker", "compose", "-f", compose, *args]


def _ensure_docker() -> None:
    if not shutil.which("docker"):
        raise EnvError("docker が見つかりません。Docker をインストールしてください。")


def _wait_for_port(host: str, port: int, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                return True
        except OSError:
            time.sleep(1)
    return False


def _build_env(base_dir: str, pubkey: str) -> dict:
    """exalite.yml の verify_container 設定を docker build 用の環境変数に変換。"""
    env = dict(os.environ)
    env["EXALITE_PUBKEY"] = pubkey

    vc = load_config(base_dir).verify_container or {}
    base_image = vc.get("base_image") or DEFAULT_BASE_IMAGE
    packages = vc.get("packages")
    packages = list(packages) if packages is not None else DEFAULT_PACKAGES
    services = vc.get("enable_services")
    services = list(services) if services is not None else DEFAULT_ENABLE_SERVICES
    # sshd は必ず有効化する
    if "sshd" not in services:
        services = ["sshd", *services]

    env["EXALITE_BASE_IMAGE"] = str(base_image)
    env["EXALITE_PACKAGES"] = " ".join(str(p) for p in packages)
    env["EXALITE_ENABLE_SERVICES"] = " ".join(str(s) for s in services)
    return env


def up(base_dir: str, *, wait: bool = True) -> str:
    """検証コンテナをビルド＆起動し、verify.ini を生成する。"""
    _ensure_docker()
    ensure_docker_assets(base_dir)
    key_path = ensure_keypair(base_dir)
    pubkey = _read_pubkey(key_path)
    inv = write_verify_inventory(base_dir, key_path)

    env = _build_env(base_dir, pubkey)
    print(f"[exalite] ベースイメージ: {env['EXALITE_BASE_IMAGE']}")
    print(f"[exalite] 導入ミドル: {env['EXALITE_PACKAGES'] or '(なし)'}")
    print(f"[exalite] 有効化サービス: {env['EXALITE_ENABLE_SERVICES']}")
    print("[exalite] 検証コンテナをビルド＆起動します…")
    proc = subprocess.run(
        _compose_cmd(base_dir, "up", "-d", "--build"), env=env
    )
    if proc.returncode != 0:
        raise EnvError("docker compose up に失敗しました。")

    if wait:
        print("[exalite] systemd/sshd の起動を待機中 (127.0.0.1:2222)…")
        # systemd のブート後に sshd が上がるため余裕を持って待つ
        if not _wait_for_port("127.0.0.1", 2222, timeout=90.0):
            raise EnvError("検証コンテナの SSH ポートに接続できませんでした。")
        print("[exalite] 検証コンテナ準備完了。")
    print(f"[exalite] インベントリ: {inv}")
    return inv


def down(base_dir: str, *, volumes: bool = True) -> int:
    _ensure_docker()
    args = ["down"]
    if volumes:
        args.append("-v")
    proc = subprocess.run(_compose_cmd(base_dir, *args))
    return proc.returncode


def status(base_dir: str) -> int:
    _ensure_docker()
    proc = subprocess.run(_compose_cmd(base_dir, "ps"))
    return proc.returncode
