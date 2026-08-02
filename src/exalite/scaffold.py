"""`exalite init` 用のサンプルプロジェクト生成。"""

from __future__ import annotations

import os
from typing import Dict, List

_FILES: Dict[str, str] = {
    "movements/hello.yml": """\
# パラメータシートを使わない Movement。
# 変数は roles/hello/defaults/main.yml の値がそのまま使われる。
name: hello
role: roles/hello
hosts: all
connection: local
gather_facts: false
""",
    "movements/hello_params.yml": """\
# 同じ Role を、パラメータシートで一部の変数を上書きして実行する Movement。
name: hello_params
role: roles/hello
hosts: all
connection: local
gather_facts: false
parameter_sheet: params/hello.csv
""",
    "roles/hello/defaults/main.yml": """\
# Role の既定値。パラメータシートが無ければこの値が使われる。
VAR_greeting: "こんにちは"
VAR_target: "world"
""",
    "roles/hello/tasks/main.yml": """\
---
- name: greeting を表示
  ansible.builtin.debug:
    msg: "{{ VAR_greeting }}, {{ VAR_target }}! (from exalite)"
""",
    "params/hello.csv": """\
host,VAR_target,VAR_greeting
localhost,Exastro,やあ
""",
    # ---- 検証(verify)/本番(prod) 環境 ------------------------------------
    "exalite.yml": """\
# プロジェクト設定。検証(verify)と本番(prod)のインベントリを定義する。
# `exalite run <mv> --target verify` / `exalite verify <mv>` / `exalite promote <mv>`
# で切り替えて使う。verify.ini は `exalite env up` が自動生成する。
environments:
  verify:
    inventory: environments/verify.ini
  prod:
    inventory: environments/prod.ini

# 検証コンテナ(Docker)を本番へ寄せるための設定。`exalite env up` が参照する。
# systemd(PID1) 稼働の AlmaLinux ベース（RHEL 互換・サブスクリプション不要）。
# 導入ミドルや有効化サービスを本番に合わせる。
verify_container:
  base_image: almalinux/9-init:latest
  packages: [httpd]              # 本番に合わせて追加（例: [httpd, chrony, postgresql]）
  enable_services: [sshd, httpd] # systemd で自動起動させるサービス
""",
    "environments/prod.ini": """\
# 本番サーバのインベントリ。実際のホストに合わせて編集してください。
# グループ名 (linux) は Movement の hosts と一致させます。
[linux]
# prod-web01 ansible_host=10.0.0.11 ansible_user=youruser
""",
    "movements/ping_linux.yml": """\
# 検証コンテナ / 本番サーバ (linux グループ) への疎通確認 Movement。
#   exalite verify ping_linux      # 検証コンテナで実行
#   exalite promote ping_linux     # 検証OKなら本番へ
name: ping_linux
role: roles/sysinfo
hosts: linux
""",
    "roles/sysinfo/tasks/main.yml": """\
---
- name: 疎通確認 (ping)
  ansible.builtin.ping:

- name: OS 情報を表示
  ansible.builtin.debug:
    msg: >-
      {{ ansible_facts['distribution'] }}
      {{ ansible_facts['distribution_version'] }}
      ({{ ansible_facts['architecture'] }})
""",
    # ---- 本番寄りサンプル: systemd でサービスを管理する Role ----------------
    "movements/web_linux.yml": """\
# httpd を導入し systemd で起動する、本番寄りの Movement。
# systemd(PID1) 稼働の検証コンテナでハンドラ/サービス管理まで検証できる。
#   exalite verify web_linux       # 検証コンテナで実行
#   exalite promote web_linux      # 検証OKなら本番へ
name: web_linux
role: roles/webserver
hosts: linux
become: true
# この Movement だけ Ansible 設定を変えたい場合（例: -vvv 相当のログ）
# ansible_cfg: ansible/verbose.cfg
""",
    "roles/webserver/tasks/main.yml": """\
---
- name: httpd をインストール
  ansible.builtin.dnf:
    name: httpd
    state: present

- name: 配信ページを配置
  ansible.builtin.copy:
    dest: /var/www/html/index.html
    content: "exalite verified on {{ ansible_facts['distribution'] }} {{ ansible_facts['distribution_version'] }}\\n"
    mode: "0644"
  notify: restart httpd

- name: httpd を有効化・起動 (systemd)
  ansible.builtin.systemd:
    name: httpd
    enabled: true
    state: started

- name: httpd が active か確認
  ansible.builtin.command: systemctl is-active httpd
  register: httpd_active
  changed_when: false

- name: 結果表示
  ansible.builtin.debug:
    msg: "httpd is {{ httpd_active.stdout }}"
""",
    "roles/webserver/handlers/main.yml": """\
---
- name: restart httpd
  ansible.builtin.systemd:
    name: httpd
    state: restarted
""",
    # ---- Ansible 設定（手編集用の外部ファイル） ---------------------------
    "ansible.cfg": """\
# プロジェクト共通の Ansible 設定。exalite が実行のたびに読み込み、
# .exalite/runs/<movement>-<mode>-<timestamp>/ansible.cfg にマージして
# ANSIBLE_CONFIG 経由で ansible-playbook に渡す。ここは手で編集してよい。
#
# 優先順位（後勝ち）:
#   exalite の既定値 → このファイル → Movement の ansible_cfg
# Movement ごとに変えたい場合は Movement YAML に次を書く:
#   ansible_cfg: ansible/verbose.cfg
#
# 注意: roles_path は Role 解決のため exalite が算出した値を必ず後ろに足す。
#       パスを書く場合はプロジェクトルート基準の相対パスか絶対パスにする。

[defaults]
# exalite の既定値（必要ならここで上書きする）
host_key_checking = False
retry_files_enabled = False

# 実行ログの詳細度。3 で `-vvv` 相当。
# verbosity = 3

# 出力フォーマット（yaml にすると複数行の値が読みやすい）
# stdout_callback = yaml

# 接続タイムアウト(秒)
# timeout = 30

[ssh_connection]
# 転送を減らして高速化する（対象で requiretty が無効な場合のみ）
# pipelining = True
""",
    "ansible/verbose.cfg": """\
# Movement 個別の Ansible 設定サンプル。使うには Movement YAML に次を書く:
#   ansible_cfg: ansible/verbose.cfg
# プロジェクト直下の ansible.cfg を土台に、ここの値が上書きされる。
[defaults]
# `-vvv` 相当の詳細ログ
verbosity = 3
""",
    ".gitignore": """\
.exalite/
environments/verify/
environments/verify.ini
""",
}


def scaffold(dest: str) -> List[str]:
    """dest 配下にサンプル一式を作成し、作成したファイルパスの一覧を返す。"""
    created: List[str] = []
    for rel, content in _FILES.items():
        path = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path):
            continue
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        created.append(path)
    return created
