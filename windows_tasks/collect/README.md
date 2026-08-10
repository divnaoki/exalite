# 設定情報の収集と Exastro パラメータシートへの登録

設定変更 Playbook が設定している項目の現在値を集め、Exastro のパラメータシートへ
登録するための Playbook 群。

大項目ごとに **「1 大項目 = 1 変数 = 1 ファイル」** で出力する。値は JSON なので、
項目が増えてもファイル構成は変わらない。

## 流れ

1. 各 collect Playbook が対象サーバーの現在値を取得する
2. 取得した内容を `set_fact` で登録用の形（snake_case のキー）に整形する
3. Conductor の作業ディレクトリへ変数ファイルを出力する（`delegate_to: localhost`）

```
{{ __conductor_workflowdir__ }}/{{ __inventory_hostname__ }}/<大項目名>
```

出力されるファイルの中身は変数1つだけ。値は**改行とインデント付きの JSON 文字列**で、
そのままパラメータシートのセルに入っても読める形にしている。

```yaml
# BEGIN ANSIBLE MANAGED BLOCK
memory_dump_info: |
  {
    "crash_dump_enabled": 2,
    "crash_dump_type": "カーネルダンプ",
    "dump_file": "C:\\Windows\\MEMORY.DMP",
    "auto_reboot": true
  }
# END ANSIBLE MANAGED BLOCK
```

## Playbook 一覧

出力先ファイル名と変数名は下表のとおり。ホストごとのディレクトリ
（`{{ __conductor_workflowdir__ }}/{{ __inventory_hostname__ }}/`）配下に作られる。

| Playbook | 出力ファイル | 変数名 | 対象の指定 |
|---|---|---|---|
| `02_computer_name_collect.yml` | `computer_name` | `computer_name_info` | － |
| `05_network_collect.yml` | `network` | `network_info` | 全アダプター列挙（`netadapter_target_names` で絞り込み可） |
| `09_local_policy_collect.yml` | `local_policy` | `local_policy_info` | － |
| `10_server_features_collect.yml` | `server_features` | `server_features_info` | `target_features` |
| `11_local_users_collect.yml` | `local_users` | `local_users_info` | 全ユーザー列挙 |
| `12_uac_collect.yml` | `uac` | `uac_info` | `uac_levels`（対応表） |
| `13_rdp_collect.yml` | `rdp` | `rdp_info` | `rdp_firewall_rules` |
| `13_smb_collect.yml` | `smb` | `smb_info` | － |
| `14_time_sync_collect.yml` | `time_sync` | `time_sync_info` | － |
| `15_firewall_collect.yml` | `firewall` | `firewall_info` | `fw_profile_settings` / `fw_rules` |
| `16_memory_dump_collect.yml` | `memory_dump` | `memory_dump_info` | － |
| `17_service_collect.yml` | `service` | `service_info` | `target_services` |

### 変数は各 Playbook 内で完結させる

収集対象の指定（上表の「対象の指定」列）は、**設定変更側の `../vars/*.yml` を
読み込まず、各 collect Playbook の `vars:` に直接書く**。`vars_files` は使わない。

設定変更側の変数には「設定したい値」（`enabled` など）が含まれるが、収集側で
必要なのは対象の `name` と表示用の `label` だけなので、その2つだけを持つ。

対象を増減するときは各 collect Playbook の `vars:` を直接編集する。
設定変更側の変数ファイルとは連動しないため、対象を追加した場合は両方に反映すること。

未対応の大項目: パフォーマンス / 変更レジストリ / ネットワークのチーム化 /
イベントログ / SNMP・ESMPRO。

## Playbook の書き方（共通の型）

全 Playbook を次の4タスク構成で揃えている。新しい大項目を追加するときも同じ形にする。

```yaml
    - name: <大項目>設定情報取得（Before）
      ansible.windows.win_shell: |
        ...
        [ordered]@{ ... } | ConvertTo-Json -Compress
      args:
        executable: powershell
      changed_when: false          # 読み取り専用
      register: xxx_info

    - name: パラメータシート登録用形式設定
      set_fact:
        xxx_raw: >-
          {{ { 'key_name': cur.KeyName, ... } }}
      vars:
        cur: "{{ xxx_info.stdout | from_json }}"

    - name: 取得情報出力
      debug:
        msg: |
          "{{ xxx_raw }}"

    - name: パラメータシート登録用ファイル出力
      blockinfile:
        create: yes
        mode: '0644'
        dest: "{{ __conductor_workflowdir__ }}/{{ __inventory_hostname__ }}/<大項目名>"
        block: |
          <大項目名>_info: |
            {{ xxx_raw | to_nice_json(indent=2, sort_keys=False, ensure_ascii=False) | indent(2) }}
      delegate_to: localhost
```

`block` 内の書き方には意味がある。

| 書き方 | 理由 |
|---|---|
| `<大項目名>_info: \|` | 値を**文字列**にする。構造（辞書）のまま渡すと、登録時に再度1行へ潰れて読めなくなる |
| `to_nice_json(indent=2)` | 改行とインデントを入れる |
| `sort_keys=False` | キーをアルファベット順に並べ替えず、Playbook で組み立てた論理順のまま出す |
| `ensure_ascii=False` | 日本語を `\uXXXX` にエスケープさせない |
| `\| indent(2)` | 2行目以降にも 2 スペースを付ける。付けないとブロックスカラーとして成立しない |

### 値の持ち方

- キーは snake_case の英字。値は取得した生値（`true` / `false` / 数値 / 配列）をそのまま入れる
- 値が `null` のものは「レジストリ値なし = 未構成 / 未設定」を意味する
- 数値コードのままでは読めないものだけ、既存の対応表を使って
  読める値も併せて持たせる（UAC の `notification_level`、メモリダンプの `crash_dump_type`）
- アダプター・ユーザー・サービスのように複数件あるものは、名前をキーにした辞書にする

### 注意点

- `dest` の親ディレクトリ（ホスト名のディレクトリ）は事前に存在している必要がある。
  `blockinfile` はファイルは作るがディレクトリは作らない
- 出力される値は「改行入りの JSON 文字列」。登録側で
  `some_var: "{{ xxx_info }}"` のように**単独のテンプレートとして参照すると、
  Ansible が辞書へ変換し直してしまい改行が失われる**。
  文字列のまま扱いたい場合は `{{ xxx_info | string }}` と書くこと
- PS5.1 の `ConvertFrom-Json` は配列を1個のオブジェクトとしてパイプへ流すため、
  `@('...' | ConvertFrom-Json)` と書くと要素数が常に 1 になる。
  一度変数へ受けてから `@()` で配列化すること

## 旧形式（縦持ち）について

`99_register_parameters.yml` と `vars/sections.yml` は、
「1設定項目 = 1レコード」で API 登録していた旧形式のもの。
**現在どの collect Playbook からも参照されていない。**
本業実装の `register_parameter.yml` に合わせた登録側を用意するまで残している。
