# RHEL / AlmaLinux 設定 Playbook

Exastro のパラメータシートで指定された値を対象サーバーへ設定する Playbook 群と、
設定内容を取得してパラメータシートへ登録するための収集 Playbook。

対象グループは `linux`（`inventory/hosts.ini` の `[linux]`）。
`windows_tasks/` と同じ構成ルールで書いている。

## 構成ルール

| PHASE | 内容 |
|---|---|
| PHASE 1 | **変更前**の設定情報を取得して表示 |
| PHASE 2 | 現在値と異なる場合のみ変更を実行（冪等） |
| PHASE 3 | **変更後**の設定情報を再取得し、指定値と一致するか `assert` で確認 |

- 読み取りだけのタスクには `changed_when: false` と `check_mode: false` を付けている。
  `--check`（dryrun）でも PHASE 1 が実際に動き、「現在値」と「何が変わるか」を確認できる。
- PHASE 3 の `assert` は `--check` ではスキップする（変更が行われないため）。
- サービス再起動は handler ではなくタスクで行う
  （handler は play の最後に実行され、PHASE 3 が再起動前の状態を見てしまうため）。

## Playbook 一覧

| ファイル | 設定内容 | パラメータシートの値 | 変数定義 |
|---|---|---|---|
| [01_ipv6_disable.yml](01_ipv6_disable.yml) | IPv6 無効化 | IPv6 = **False** | [vars/01_ipv6_disable.yml](vars/01_ipv6_disable.yml) |
| [02_service_startup.yml](02_service_startup.yml) | サービス起動設定 | chrony を**自動起動 ON + 起動** | [vars/02_service_startup.yml](vars/02_service_startup.yml) |
| [03_admin_user.yml](03_admin_user.yml) | 管理者権限ユーザー作成 | **mgAUTOXadm01** | [vars/03_admin_user.yml](vars/03_admin_user.yml) |
| [04_time_sync.yml](04_time_sync.yml) | 時刻同期設定 | NTPサーバ = **192.168.140.10** | [vars/04_time_sync.yml](vars/04_time_sync.yml) |
| [main.yml](main.yml) | 上記を 01 → 04 の順にまとめて実行 | - | - |

収集（現在値の取得と出力）は [collect/](collect/README.md) を参照。

## 実行方法

コントロールノードは Docker コンテナ。**PowerShell から** 実行する
（Git Bash だと MSYS のパス変換で `/ansible` が化ける）。

```bash
docker exec -w /ansible/exalite/rhel_tasks ansible ansible-playbook -i /ansible/playbook/inventory/hosts.ini --check --diff 01_ipv6_disable.yml
```

```bash
docker exec -w /ansible/exalite/rhel_tasks ansible ansible-playbook -i /ansible/playbook/inventory/hosts.ini 01_ipv6_disable.yml
```

管理者ユーザーのパスワードは vars に書かず、実行時に渡す。

```bash
docker exec -w /ansible/exalite/rhel_tasks ansible ansible-playbook -i /ansible/playbook/inventory/hosts.ini 03_admin_user.yml -e '{"server_admin_accounts":{"mgAUTOXadm01":"＜パスワード＞"}}'
```

## 各 Playbook の要点

### 01_ipv6_disable.yml

`nmcli connection modify <接続> ipv6.method disabled` で接続単位に無効化する
（リンクローカル `fe80::` も含めて消える）。手順書
[docs/almalinux9_static_ip_ipv6_disable.md](../../../docs/almalinux9_static_ip_ipv6_disable.md)
の「3-1」に相当。sysctl による OS 全体の無効化や、IPv6 を有効に戻す処理は持たない。

反映は `nmcli connection down` → `up`。**自分自身の接続が切れる**ため、down と up を
1コマンドにまとめて `async` + `poll: 0` で投げ、`wait_for_connection` で復帰を待つ。
IPv4 は変更しないので同じ IP アドレスで戻る。

### 02_service_startup.yml

`target_services` の各サービスを「自動起動 ON + 起動」に揃える。
パッケージの導入は行わない（導入済み前提）。ユニットが無い場合は PHASE 3 の
`assert` が `not-found` で失敗する。

### 03_admin_user.yml

管理者権限は **sudo 権限グループ（`wheel`）への追加**で与える。
`/etc/sudoers` は編集しない（RHEL 既定で `%wheel` の許可行が有効なため）。

- **パスワードは vars に書かない。** `server_admin_accounts`（Exastro の機密パラメータ
  または extra-vars）で渡す。渡されていない状態で新規作成が必要な場合は、
  1件も変更しないうちに PHASE 1 のガードで中止する。
- パスワードを使うタスクには `no_log: true` を付けている。
- `update_password: on_create` のため既存ユーザーのパスワードは変更しない。
- ソルトはユーザー名から決まる固定値（毎回変わるとハッシュが変わり差分が出続けるため）。
- グループは `append: true`（追加のみ）。既存の所属は削除しない。
- PHASE 3 では `sudo -l -U <ユーザー>` で実際に管理者権限が効くところまで確認する。

### 04_time_sync.yml

`/etc/chrony.conf` の同期先を指定値に揃える。2段階で変更する。

1. 指定サーバー**以外**の `server` / `pool` 行をコメントアウト
   （既定の `pool 2.almalinux.pool.ntp.org iburst` などを無効化）
2. 指定サーバーを `ANSIBLE MANAGED BLOCK` として追記

コメント済みの行は対象外なので、再実行しても設定は増殖しない。
変更した場合のみ chronyd を再起動し、`chronyc -a makestep` で即時補正する。

> **確認事項**: パラメータシートの NTPサーバ `192.168.140.10` は、inventory の
> `alma9`（本 Playbook の実行対象）と同じ IP。自分自身を同期先に指定すると設定は
> 入るが実際には同期できない（`chronyc sources` が Not synchronised のままになる）。
> 上位 NTP サーバーの IP が別にある場合は `vars/04_time_sync.yml` を差し替えること。

このため PHASE 3 の `assert` は「設定が反映されたこと」だけを確認し、
同期完了までは条件に含めていない。未同期の場合は注意メッセージを表示する。

## 前提

- 対象: RHEL 9 系（AlmaLinux 9 で確認）。時刻同期は chrony、chrony は導入済み。
- コントロールノード: Docker コンテナ `ansible`（ansible-core 2.17.6）。
- `become: true` で実行する。root 接続の場合も sudo 経由になるだけで影響はない。
- 03 の `password_hash` はコントロールノード側で実行されるため、Python 3.13 以降の
  環境では `passlib` の導入が必要（`crypt` モジュールが削除されたため）。
  現行コンテナは Python 3.12 のため不要。
