# 設定情報の収集と Exastro パラメータシートへの登録

各設定 Playbook の「情報取得」部分を使って現在値を集め、Exastro のパラメータシートへ
API でレコード登録するための Playbook 群。

## 構成

| Playbook | 役割 |
|---|---|
| `10_server_features_collect.yml` | サーバーの役割と機能 |
| `11_local_users_collect.yml` | ローカルユーザー |
| `12_uac_collect.yml` | UAC（ソフトウェア制御） |
| `13_rdp_collect.yml` | リモートデスクトップ |
| `14_time_sync_collect.yml` | 時刻同期 |
| `15_firewall_collect.yml` | Windows ファイアウォール |
| `16_memory_dump_collect.yml` | メモリダンプ |
| `99_register_parameters.yml` | 上記が出力した変数ファイルを読み込み、API でレコード登録 |

## 流れ

1. 各 collect Playbook が対象サーバーの現在値を取得する
2. 取得した値を Conductor の作業ディレクトリ `{{ __conductor_workflowdir__ }}` に
   変数ファイル（YAML）として出力する（`delegate_to: localhost`）
3. 最後の Movement で `99_register_parameters.yml` を実行する
   - `include_vars` で作業ディレクトリの変数ファイルをすべて読み込む
   - ホストごとに1レコードへマージし、Exastro の API へ POST する

## 変数ファイルの形式

`include_vars` はディレクトリ内の全ファイルを読み込むため、同名の変数があると
後から読んだファイルで上書きされる。そこで **「設定 × ホスト」で一意な変数名**にしている。

```yaml
collected_uac__MGOPSMG_PH01:
  host_name: "MGOPSMG-PH01"
  uac_notification_setting: "アプリがコンピューターに変更を加えようとする場合のみ通知する（既定）"
```

登録側はプレフィックス `collected_` の変数をすべて集め、`host_name` ごとに
マージして1レコードにする。設定 Playbook を増やしたときも、同じ規約で
変数ファイルを出力すれば登録側の修正は不要。

## 実行前に設定するもの

`vars/99_register_parameters.yml` の接続情報。
**パスワードはファイルに書かず、Exastro の機密パラメータや extra-vars で渡すこと。**

```bash
ansible-playbook 99_register_parameters.yml -e '{"exastro_password":"＜パスワード＞"}'
```

## 参考実装との違い

参考実装（OMCA）は `blockinfile` で変数ファイルを作成しているが、本 Playbook は
値に日本語・記号・可変個数の項目を含むため `copy` + `to_nice_yaml` で出力している。
引用符の付け忘れによる YAML 崩れを防ぐためで、`include_vars` での読み込み方は同じ。
