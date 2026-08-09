# 設定情報の収集と Exastro パラメータシートへの登録（RHEL / AlmaLinux）

`rhel_tasks/` の各設定 Playbook が設定している項目の**現在値を取得して出力**する
Playbook 群。出力形式は `windows_tasks/collect/` と同じ
**「1設定項目 = 1レコード」（縦持ち）** なので、パラメータシートは Windows 側と共用できる。

設定は一切変更しない（読み取りのみ）。

## 大項目と収集 Playbook の対応

| No. | 大項目 | 項目キーの接頭辞 | Playbook | 対応する設定 Playbook |
|---|---|---|---|---|
| (1) | IPv6無効化 | `l01_ipv6` | [01_ipv6_collect.yml](01_ipv6_collect.yml) | `01_ipv6_disable.yml` |
| (2) | サービス起動設定 | `l02_service` | [02_service_collect.yml](02_service_collect.yml) | `02_service_startup.yml` |
| (3) | 管理者権限ユーザー | `l03_user` | [03_admin_user_collect.yml](03_admin_user_collect.yml) | `03_admin_user.yml` |
| (4) | 時刻同期設定 | `l04_ntp` | [04_time_sync_collect.yml](04_time_sync_collect.yml) | `04_time_sync.yml` |

項目キーは Linux が `l01`〜、Windows が `s01`〜 で始まるため、同じシートへ登録しても衝突しない。

02 / 03 / 04 は対象（サービス名・ユーザー名・chrony.conf のパス）を設定側と同じ
`../vars/` から読むので、対象を増やしても収集側の修正は不要。

## 取得する項目

| Playbook | 項目 |
|---|---|
| 01 | IPv6 の有効/無効判定、IPv6 アドレス数（グローバル / リンクローカル）、接続ごとの `ipv6.method` |
| 02 | サービスごとの自動起動（`is-enabled`）と起動状態（`is-active`） |
| 03 | アカウントの有無、UID、所属グループ、ログインシェル、ホーム、パスワード状態、sudo 権限 |
| 04 | 同期先の設定有無、`chrony.conf` の server/pool 行、chronyd の稼働状態、同期完了、同期元、Stratum、タイムゾーン |

> 03 が取得するパスワード情報は `passwd -S` の状態（`PS`=設定済 / `LK`=ロック / `NP`=未設定）のみ。
> **パスワードやハッシュは取得も出力もしない。**

## 実行方法

```bash
docker exec -w /ansible/exalite/rhel_tasks/collect ansible ansible-playbook -i /ansible/playbook/inventory/hosts.ini 01_ipv6_collect.yml
```

出力先は [vars/sections.yml](vars/sections.yml) の `collect_dir`。
Exastro の Conductor から実行すると `__conductor_workflowdir__` が渡るため通常は変更不要で、
単体実行時は `collect/_collected/` に出力する。任意の場所へ出したい場合は上書きする。

```bash
docker exec -w /ansible/exalite/rhel_tasks/collect ansible ansible-playbook -i /ansible/playbook/inventory/hosts.ini -e collect_dir=/tmp/_collected 04_time_sync_collect.yml
```

## 変数ファイルの形式

`include_vars` はディレクトリ内の全ファイルを読み込むため、同名の変数があると
後から読んだファイルで上書きされる。そこで **「大項目 × ホスト」で一意な変数名**にしている。

```yaml
collected_ntp__alma9:
  menu: check_parameter                      # 登録先パラメータシート
  host_name: alma9
  section: (4) 時刻同期設定
  rows:
    - key: l04_ntp_servers
      item: NTPサーバ（/etc/chrony.conf の server/pool 行）
      value: 192.168.140.10
```

## パラメータシートへの登録

登録処理（`99_register_parameters.yml`）は **OS に依存しない**ため、
Windows 側のものをそのまま使う。プレフィックス `collected_` の変数を全て拾い、
メニュー単位でまとめて API に POST する。

```bash
docker exec -w /ansible/exalite/windows_tasks/collect ansible ansible-playbook 99_register_parameters.yml -e '{"exastro_password":"＜パスワード＞"}'
```

接続情報は [windows_tasks/collect/vars/99_register_parameters.yml](../../windows_tasks/collect/vars/99_register_parameters.yml)
で定義する。**パスワードはファイルに書かず、Exastro の機密パラメータか extra-vars で渡すこと。**

列名を変えたい場合は [vars/sections.yml](vars/sections.yml) の `param_columns` を
シート側の項目名に合わせて書き換える（Playbook の修正は不要）。
大項目ごとにシートを分ける場合は `param_section_menus` に「大項目キー: メニューID」を書く。

## 再実行時の注意

`type: "Register"` は新規登録のため、同じホスト・同じ項目で2回実行すると
重複または一意制約エラーになる。オペレーションを分けて履歴として残す運用を想定している。
