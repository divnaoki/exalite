# 設定情報の収集と Exastro パラメータシートへの登録（RHEL / AlmaLinux）

`rhel_tasks/` の各設定 Playbook が設定している項目の**現在値を取得して出力**する
Playbook 群。設定は一切変更しない（読み取りのみ）。

出力形式は `windows_tasks/collect/` と同じ
**「1 大項目 = 1 変数 = 1 ファイル」**（値は改行入りの JSON 文字列）。

## 流れ

1. 各 collect Playbook が対象サーバーの現在値を取得する
2. 取得した内容を `set_fact` で登録用の形（snake_case のキー）に整形する
3. Conductor の作業ディレクトリへ変数ファイルを出力する（`delegate_to: localhost`）

```
{{ __conductor_workflowdir__ }}/{{ __inventory_hostname__ }}/<大項目名>
```

```yaml
# BEGIN ANSIBLE MANAGED BLOCK
selinux_info: |
  {
    "runtime": "Permissive",
    "config": "permissive"
  }
# END ANSIBLE MANAGED BLOCK
```

## Playbook 一覧

| Playbook | 出力ファイル | 変数名 | 対応する設定 Playbook | 形式 |
|---|---|---|---|---|
| [01_ipv6_collect.yml](01_ipv6_collect.yml) | `ipv6` | `ipv6_info` | `01_ipv6_disable.yml` | 新 |
| [03_admin_user_collect.yml](03_admin_user_collect.yml) | `admin_user` | `admin_user_info` | `03_admin_user.yml` | 新 |
| [05_selinux_collect.yml](05_selinux_collect.yml) | `selinux` | `selinux_info` | `05_selinux_disable.yml` | 新 |
| [02_service_collect.yml](02_service_collect.yml) | - | - | `02_service_startup.yml` | **旧**（縦持ち） |
| [04_time_sync_collect.yml](04_time_sync_collect.yml) | - | - | `04_time_sync.yml` | **旧**（縦持ち） |

02 / 04 は旧形式（`collected_*` の縦持ち + `vars/sections.yml`）のまま。
今回の対象（SELinux / 管理者ユーザー / IPv6）に含まれないため変換していない。

## 取得する項目

| 変数 | 項目 |
|---|---|
| `ipv6_info` | `ipv6_enabled`（グローバルアドレスの有無で判定） / `global_addrs` / `link_addrs` / `sysctl_all_disable_ipv6` / `sysctl_default_disable_ipv6` / `connections`（接続ごとの `ipv6.method`） |
| `admin_user_info` | ユーザー名をキーに `exists` / `uid` / `shell` / `home` / `groups` / `password_status` / `sudo` |
| `selinux_info` | `runtime`（getenforce） / `config`（`/etc/selinux/config` の `SELINUX=`） |

> `password_status` は `passwd -S` の2列目（`PS`=設定済 / `LK`=ロック / `NP`=未設定）のみ。
> **パスワードやハッシュは取得も出力もしない。**

## 収集対象は各 Playbook 内で指定する

Windows 側と同じく、設定変更側の `../vars/*.yml` は読み込まない（`vars_files` を使わない）。
対象は各 collect Playbook の `vars:` に直接書く。

| Playbook | 変数 |
|---|---|
| `01_ipv6_collect.yml` | `ipv6_connections`（nmcli の接続名） |
| `03_admin_user_collect.yml` | `admin_users`（ユーザー名） |
| `05_selinux_collect.yml` | なし（対象はホスト単位で固定） |

設定変更側の変数ファイルとは連動しないため、対象を追加した場合は両方に反映すること。

## 実行方法

```bash
docker exec -w /ansible/exalite/rhel_tasks/collect ansible ansible-playbook \
  -i /ansible/playbook/inventory/hosts.ini 05_selinux_collect.yml \
  -e __conductor_workflowdir__=/tmp/_collected -e __inventory_hostname__=alma9
```

`blockinfile` はファイルは作るがディレクトリは作らないため、
出力先のホスト名ディレクトリが存在している必要がある。

## 注意点

- 取得結果は `キー:値` 形式の行で受け取り、`set_fact` で辞書に組み立てている。
  **値に `:` を含むものは分割が崩れる**ため、`sudo -l`（`NOPASSWD:` を含む）だけは
  別タスクで取得している。nmcli の接続名にも `:` を使わないこと
- 出力される値は「改行入りの JSON 文字列」。登録側で
  `some_var: "{{ xxx_info }}"` のように**単独のテンプレートとして参照すると、
  Ansible が辞書へ変換し直してしまい改行が失われる**。
  文字列のまま扱いたい場合は `{{ xxx_info | string }}` と書くこと
