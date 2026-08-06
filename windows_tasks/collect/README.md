# 設定情報の収集と Exastro パラメータシートへの登録

設定変更 Playbook が設定している項目の現在値を集め、Exastro のパラメータシートへ
API でレコード登録するための Playbook 群。

**「1設定項目 = 1レコード」（縦持ち）** で登録するため、パラメータシートに必要な
列は4つだけ。項目が増えてもシートの作り直しは不要。

## 作成するパラメータシート

Exastro ITA の「メニュー作成」でパラメータシートを1つ作る。

- ホスト/オペレーション: **あり**（ホストごと・オペレーションごとに記録するため）
- 項目（列）は以下の4つ。型はすべて「文字列（単一行）」でよい。

| 項目名 | 例 | 備考 |
|---|---|---|
| 大項目 | `(10) Windowsファイアウォール` | チェックシートの大項目 |
| 項目名 | `ドメイン プロファイル / 状態 / ファイアウォールの状態` | 画面表示に寄せた名称 |
| 項目キー | `s10_fw_domain_enabled` | 一意制約・期待値との突き合わせ用 |
| 設定値 | `有効（推奨）` | 取得した現在値 |

列名を変えたい場合は [vars/sections.yml](vars/sections.yml) の `param_columns` を
シート側の項目名に合わせて書き換える（Playbook の修正は不要）。

ホスト/オペレーションなしで作る場合は、[vars/99_register_parameters.yml](vars/99_register_parameters.yml)
の `exastro_use_operation` を `false` にする。

## 大項目と収集 Playbook の対応

| No. | 大項目 | 項目キーの接頭辞 | Playbook |
|---|---|---|---|
| (1) | リモートデスクトップ | `s01_rdp` | `13_rdp_collect.yml` |
| (2) | コンピューター名 | `s02_computer` | `02_computer_name_collect.yml` |
| (3) | パフォーマンス | `s03_performance` | 未対応 |
| (4) | メモリダンプ | `s04_dump` | `16_memory_dump_collect.yml` |
| (5) | ネットワークアダプター | `s05_netadapter` | 未対応 |
| (6) | コンピューターの管理 | `s06_computermgmt` | `11_local_users_collect.yml` |
| (7) | NTPサーバの設定 | `s07_ntp` | `14_time_sync_collect.yml` |
| (8) | 役割と機能 | `s08_feature` | `10_server_features_collect.yml` |
| (9) | ローカルセキュリティポリシー | `s09_policy` | `09_local_policy_collect.yml` |
| (10) | Windowsファイアウォール | `s10_fw` | `15_firewall_collect.yml` |
| (11) | 変更レジストリ | `s11_registry` | 未対応 |
| (12) | ネットワークのチーム化と優先度、IPv6無効化 | `s12_teaming` | 未対応 |
| (13) | SMB3.0 マルチチャネルの無効化 | `s13_smb` | `13_smb_collect.yml` |
| (14) | ソフトウェア制御 | `s14_uac` | `12_uac_collect.yml` |
| (15) | イベントログ | `s15_eventlog` | 未対応 |
| (16) | SNMPサービス、ESMPRO/ServerAgentService | `s16_snmp` | 未対応 |
| (17) | サービス | `s17_service` | `17_service_collect.yml` |

最後に `99_register_parameters.yml` を実行して、収集結果をまとめて API 登録する。

## 流れ

1. 各 collect Playbook が対象サーバーの現在値を取得する
2. Conductor の作業ディレクトリ `{{ __conductor_workflowdir__ }}` に
   変数ファイル（YAML）を出力する（`delegate_to: localhost`）
3. 最後の Movement で `99_register_parameters.yml` を実行する
   - `include_vars` で作業ディレクトリの変数ファイルをすべて読み込む
   - `rows` を1レコードずつに展開し、メニュー単位でまとめて POST する

## 変数ファイルの形式

`include_vars` はディレクトリ内の全ファイルを読み込むため、同名の変数があると
後から読んだファイルで上書きされる。そこで **「大項目 × ホスト」で一意な変数名**にしている。

```yaml
collected_firewall__SV01:
  menu: check_parameter                      # 登録先パラメータシート
  host_name: SV01
  section: (10) Windowsファイアウォール
  rows:
    - key: s10_fw_domain_enabled
      item: ドメイン プロファイル / 状態 / ファイアウォールの状態
      value: 有効（推奨）
```

登録側はプレフィックス `collected_` の変数をすべて集めるため、大項目を増やしたときも
同じ規約で変数ファイルを出力すれば登録側の修正は不要。

## 大項目ごとにシートを分ける場合

[vars/sections.yml](vars/sections.yml) の `param_section_menus` に指定する。
登録側はメニュー単位で POST をまとめるため、Playbook の修正は不要。

```yaml
param_section_menus:
  feature: "win_role_feature_sheet"
  service: "win_service_sheet"
```

## 実行前に設定するもの

[vars/99_register_parameters.yml](vars/99_register_parameters.yml) の接続情報。
**パスワードはファイルに書かず、Exastro の機密パラメータや extra-vars で渡すこと。**

```bash
ansible-playbook 99_register_parameters.yml -e '{"exastro_password":"＜パスワード＞"}'
```

## 再実行時の注意

`type: "Register"` は新規登録のため、同じホスト・同じ項目で2回実行すると
重複または一意制約エラーになる。オペレーションを分けて履歴として残す運用を想定している。
上書きしたい場合は `exastro_record_type` を `Update` にする（更新対象の特定が別途必要）。

## 参考実装との違い

参考実装（OMCA）は `blockinfile` で変数ファイルを作成しているが、本 Playbook は
値に日本語・記号・可変個数の項目を含むため `copy` + `to_nice_yaml` で出力している。
引用符の付け忘れによる YAML 崩れを防ぐためで、`include_vars` での読み込み方は同じ。
