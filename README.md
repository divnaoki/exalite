# exalite — Exastro-lite

ローカルの Ansible Role / Playbook を **Exastro に再アップロードすることなく**、その場で
**構文チェック / ドライラン / 実行**できる軽量 CLI。**パラメータシートは任意**で、無ければ
Role の `defaults/main.yml`・`vars/main.yml` を Ansible ネイティブに尊重して実行します。

Exastro IT Automation（ITA）を普段使う中で不便だった以下 2 点を解消することを目的にした
最小構成のクローンです。

| Exastro での不便 | exalite での解決 |
|---|---|
| **① AnsibleLegacyRole で Playbook 修正のたびに再アップロードが必要**。Sandbox で構文だけ先に確認したい | ローカルの Role ディレクトリを**直接参照**。編集したら即 `syntax` / `dryrun` / `run`。`watch` で保存のたびに自動構文チェック |
| **② パラメータシートが無いと Movement を実行できない**。可変値でなく `defaults/main.yml`・`vars/main.yml` で足りるなら不要にしたい | パラメータシートを**任意化**。無ければ Role の defaults/vars をそのまま使用。ある場合だけ host_vars として上書き |

## 仕組み

Exastro が内部でやっている「Ansible 実行用のディレクトリ・変数ファイル・インベントリの
自動生成」を、ローカルで透過的に再現します。実行のたびに
`.exalite/runs/<movement>-<mode>-<timestamp>/` に以下を生成し、`ansible-playbook` を起動します。

```
playbook.yml            role 指定時の wrapper playbook（playbook 指定時はそれを使用）
inventory/hosts         インベントリ（movement の inventory > パラメータシート > localhost）
inventory/host_vars/*   パラメータシート由来の host_vars（Role defaults を上書き）
extra_vars.yml          movement の extra_vars（-e で最優先）
ansible.cfg             外部 ansible.cfg のマージ結果（roles_path 等）
```

変数の優先順位は Ansible ネイティブそのままです：
**extra_vars（movement）> パラメータシート（host_vars）> Role の vars/main.yml > Role の defaults/main.yml**。
パラメータシートを使わなければ、素の Ansible と同じ解決になります。

## インストール

```bash
pip install -e .        # 要 Python 3.8+ と Ansible (ansible-playbook)
```

> `password_hash` フィルタを使う Role（ユーザー作成など）を実行する場合は、**実行側**に
> `passlib` も必要です（`pip install passlib`）。macOS の `crypt` は DES しか持たず
> sha512 にフォールバックできないため、未導入だと
> `Unable to encrypt nor hash, passlib must be installed` で失敗します。

## クイックスタート

```bash
exalite init myproject      # サンプル一式を生成
cd myproject
exalite list                # Movement 一覧

exalite syntax hello        # ① 構文チェック（アップロード不要）
exalite dryrun hello        # ドライラン（--check --diff）
exalite run hello           # ② パラメータシート無しで実行（defaults を使用）
exalite run hello_params    # パラメータシートで一部変数を上書きして実行
exalite watch hello         # ① 保存のたびに自動で構文チェック
```

## 簡易 WebUI

CLI に加えて、ブラウザから **機器一覧 / Movement 作成・紐付け / Conductor / 実行** を行える簡易 WebUI を同梱。

```bash
pip install -e '.[web]'     # Flask を導入（未導入の場合）
exalite web                 # http://127.0.0.1:8765 で起動（対象はカレントのプロジェクト）
exalite web --port 9000     # ポート変更
```

画面構成:

- **機器一覧**: 環境（verify/prod）ごとのインベントリを表形式で編集（機器名・グループ・接続変数）。
  `[group:vars]` ブロックやコメントは保持されます。検証コンテナの起動/停止ボタンも配置。
- **Movement 作成・紐付け**: 名前・対象グループ(hosts) を決め、**Role/Playbook・パラメータシート・
  ansible.cfg** をドロップダウンで紐付けて保存（= `movements/<name>.yml` を生成）。
  既存 Movement の編集・削除も可能。
- **Conductor**: Movement を順番に並べた作業フローを作成・編集・実行。↑↓ で順序変更、
  ステップごとに失敗時の扱い(stop/continue)と実行先を指定できます。
- **実行 / 実行画面**: Movement・実行先(target)・モード(run/dryrun/syntax) を選んで実行。
  バックグラウンドで `ansible-playbook` が走り、**実行画面にログがリアルタイム表示**（1秒間隔ポーリング）。
  最近の実行履歴からログを再表示できます。

> WebUI は開発用サーバ(Flask)で `127.0.0.1` 待受が既定です。共有する場合は逆プロキシ等の前段を用意してください。

## 検証サーバ（Docker）→ 本番 のワークフロー

Playbook をいきなり本番へ流さず、**まず検証用サーバ（Docker コンテナ）で実行 →
問題なければ本番サーバへ投入**できます。検証コンテナは **AlmaLinux + systemd** で
本番に寄せてあり、`systemctl`/`service`/handler によるサービス管理まで本番同様に検証できます。

```bash
exalite env up                 # AlmaLinux(systemd) 検証コンテナをビルド＆起動（SSH 鍵も自動生成）
exalite env status             # 状態確認
exalite verify web_linux       # 検証コンテナで実行（run --target verify の別名）
exalite promote web_linux      # 検証で実行し、成功したら確認のうえ本番へ投入
exalite env down               # 検証コンテナを停止・破棄
```

- `exalite env up` は `docker/linux/Dockerfile`（**almalinux/9-init** ベース = systemd が PID1。
  RHEL 互換かつサブスクリプション不要で認証なしに pull できる）を
  ビルドし、`environments/.ssh/verify/` に生成した鍵で `127.0.0.1:2222` に SSH 疎通できる
  検証環境を用意します。インベントリ `environments/verify/linux.ini`（グループ `linux`）も
  自動生成されます。検証 httpd はホストの `127.0.0.1:8080` にも公開されます。
- **systemd が PID1 で動く**ため、Playbook 中の `ansible.builtin.systemd` / `service` / handler の
  サービス再起動が本番と同じ挙動で検証できます（同梱の `web_linux` が httpd の dnf 導入
  → systemd 起動 → handler 再起動までを実演）。
- 本番は `environments/prod.ini`（同じ `linux` グループ）を実ホストに合わせて編集します。
- `promote` は **検証が失敗したら本番へ進みません**。本番投入前に確認プロンプトを出します
  （`-y` で省略可）。
- 環境の切替は任意コマンドで `--target verify|prod`（例: `exalite dryrun web_linux --target verify`）。

環境と検証コンテナの定義は `exalite.yml`：

```yaml
environments:
  verify:
    inventory: environments/verify   # ディレクトリ。linux.ini と windows.ini を共存
  prod:
    inventory: environments/prod.ini

# 検証コンテナを本番へ寄せる設定（exalite env up が参照）
verify_container:
  base_image: almalinux/9-init:latest
  packages: [httpd]              # 本番に合わせて追加（例: [httpd, chrony, postgresql]）
  enable_services: [sshd, httpd] # systemd で自動起動させるサービス
```

`packages` / `enable_services` / `base_image` を編集して `exalite env up` すると、
本番構成に近づけた検証コンテナが再ビルドされます。

> **systemd 実行の前提**: 検証コンテナは systemd を動かすため、compose で `cgroup: host` /
> `cap_add: SYS_ADMIN` / `/sys/fs/cgroup` マウント等を付与しています（`docker/docker-compose.yml`）。
> Docker Desktop(cgroup v2) で動作確認済みです。

## Windows 検証機（実機 / VM を WinRM で）

Windows は Docker で用意せず、**実機/VM を WinRM 接続先として登録**します。検証環境の
インベントリはディレクトリ形式で、Linux(Docker) と Windows(実機) が共存します。

```
environments/
  .ssh/verify/id_ed25519    exalite env up が生成する検証コンテナ用の鍵
  verify/
    linux.ini               exalite env up が自動生成（検証コンテナ / グループ linux）
    windows.ini             手動管理（WinRM 接続先 / グループ windows）
  prod.ini
```

```bash
export EXALITE_WIN_PASSWORD='＜検証用アカウントのパスワード＞'
exalite env check-win        # WinRM 疎通チェック（ansible.windows.win_ping）
exalite verify ping_win      # hosts: windows の Movement を Windows 検証機で実行
```

- Windows 側の準備（WinRM 有効化・アカウント・ファイアウォール）は
  **[docs/windows-verify-setup.md](docs/windows-verify-setup.md)** に手順があります。
- `windows.ini` はパスワード等を含みうるため既定で `.gitignore` 済みです。値は環境変数
  （`lookup('env', ...)`）か Ansible Vault で渡してください。
- 実行側に `pywinrm` と `ansible.windows` コレクションが必要です。

> **なぜ Windows コンテナを使わないか**: Windows コンテナは Windows ホスト
> （Docker Desktop の windows-container モード / Pro・Enterprise 以上）でしか動かず、
> Linux コンテナと**同時に動かせません**。さらに Ansible のコントロールノード自体が
> Windows ネイティブ非対応で、Windows PC で使う場合も WSL2 の中で動かすことになります。
> コンテナには systemd/sshd が無く、再起動・ドメイン参加・GUI も不可のため、実機の代替に
> なりません。詳細な比較は [docs/windows-verify-setup.md](docs/windows-verify-setup.md) に記載。
> **Windows PC で exalite を動かす場合も、WSL2 + 実機 WinRM の構成なら Linux 検証コンテナと
> Windows 検証を同時に扱えます。**

## Movement 設定

Exastro の「Movement（最小の作業単位、1 実行 = Playbook 1 回）」に相当する定義を
`movements/<name>.yml` に書きます。パスはプロジェクトルート基準です。

```yaml
name: install_nginx
role: roles/nginx            # ローカル Role のパス（playbook と排他）
# playbook: playbooks/site.yml
hosts: web                   # 対象ホストパターン（省略時 all）
inventory: inventory/hosts   # 省略可。無ければ parameter_sheet / localhost から生成
parameter_sheet: params/nginx.csv   # 省略可。無ければ Role の defaults/vars を使用
connection: ssh              # 省略可（local でローカル実行）
become: true                 # 省略可
gather_facts: true           # 省略可（既定 true）
ansible_cfg: ansible/verbose.cfg    # 省略可。この Movement 専用の Ansible 設定
extra_vars:                  # 省略可。全ホスト共通の強制上書き
  VAR_env: production
```

## パラメータシート（任意）

Exastro のパラメータシートに相当する CSV。**無くても実行できます。**

```csv
host,VAR_port,VAR_server_name
web01,8080,web01.example.com
web02,8081,web02.example.com
```

- 先頭列がホスト名、2 列目以降が変数名。
- セル値は YAML として解釈を試みます（`8080`→int, `true`→bool, それ以外は文字列）。
- ここで与えた値は host_vars として Role の defaults を上書きします。
- `--no-paramsheet` を付けると、シートが設定済みでも無視して defaults/vars のみで実行します。

## Conductor（Movement を登録順に実行）

Exastro の Conductor に相当する、**登録した順に Movement を 1 つずつ実行**する作業フロー。
`conductors/<name>.yml` に書きます。分岐や並列は持たない最小構成です。

```yaml
name: setup_all
description: 疎通確認 → ユーザー作成 → パッケージ導入 → Web サーバ構築
movements:
  - ping_linux              # 文字列だけなら既定動作（失敗したら以降を中断）
  - setup_user
  - movement: install_package
    on_failure: continue    # 失敗しても次のステップへ進む（既定 stop）
  - movement: web_linux
    target: verify          # このステップだけ実行先を固定（省略時は実行時の --target）
```

```bash
exalite conductor list                              # 一覧（実行順を表示）
exalite conductor run setup_all --target verify     # 検証環境へ順に実行
exalite conductor run setup_all --mode syntax       # 全ステップ構文チェックのみ
exalite conductor run setup_all --mode dryrun       # 全ステップドライラン
```

- ステップ名は Movement の **ファイル名**（`movements/<name>.yml` の `<name>`）で指定します
  （YAML 内の `name:` ではありません）。
- `on_failure: stop`（既定）のステップが失敗すると、以降のステップは実行せず
  **スキップ**として記録します。`continue` なら失敗しても次へ進みます。
- 実行先は `--target` で全体指定、ステップ側の `target:` があればそちらが優先されます。
- 終了コードは最初に失敗したステップのものを返すので、CI からもそのまま使えます。
- 最後に結果サマリを表示します。

```
========== Conductor 結果: setup_all ==========
  [1] ping_linux               OK
  [2] setup_user               失敗 (rc=2)
  [3] install_package          スキップ
[exalite] Conductor 失敗 (rc=2)
```

WebUI の **Conductor タブ**からも、Movement をドロップダウンで追加し ↑↓ で並べ替えて
保存・実行できます。実行ログは 1 本にまとめられ「実行 / 実行画面」タブに表示されます。

## Ansible 設定（ansible.cfg）

Ansible の挙動は **手で編集できる外部ファイル**で指定します。実行時に次の順で
**後勝ちマージ**され、結果が run ディレクトリに書き出されて `ANSIBLE_CONFIG` 経由で
`ansible-playbook` に渡ります。

| 優先 | 対象 | 場所 | 効き方 |
|---|---|---|---|
| 低 | exalite の既定値 | （コード内） | `host_key_checking` / `retry_files_enabled` / `roles_path` |
| 中 | プロジェクト共通 | `ansible.cfg`（プロジェクト直下） | 全 Movement に適用。あれば自動で読まれる |
| 高 | Movement 個別 | Movement の `ansible_cfg:` が指すファイル | その Movement だけ上書き |

```
myproject/
├── ansible.cfg              # 全 Movement 共通（手編集）
├── ansible/verbose.cfg      # Movement 個別（手編集）
└── movements/web_linux.yml  # ansible_cfg: ansible/verbose.cfg で紐付け
```

```ini
# ansible/verbose.cfg — この Movement だけ -vvv 相当にする例
[defaults]
verbosity = 3
```

```yaml
# movements/web_linux.yml
name: web_linux
role: roles/webserver
hosts: linux
ansible_cfg: ansible/verbose.cfg
```

- **実行ログを `-vvv` 相当にする**には `[defaults] verbosity = 3` を書きます
  （プロジェクト直下に書けば全体、Movement の `ansible_cfg` に書けばその Movement だけ）。
  CLI・WebUI・`watch`・`promote` のいずれの経路でも同じように効きます。
- WebUI の Movement フォームからも `ansible.cfg` をドロップダウンで紐付けられます
  （直下の `ansible.cfg` と `ansible/*.cfg` が候補）。
- `roles_path` だけは Role 解決に必須のため、指定した値の**後ろに** exalite が算出した
  パスを追記します。それ以外のキーは書いた値がそのまま使われます。
- run ディレクトリの `ansible.cfg` は毎回の生成物です（先頭に反映元がコメントで入ります）。
  直接編集しても次の実行には残りません。編集するのは上記の外部ファイルです。
- 設定ファイル中に相対パスを書く場合は、プロジェクトルート基準の相対パスか絶対パスにしてください。
- 一時的に詳細ログを見たいだけなら `exalite run <mv> -- -vvv`（`--` 以降は
  `ansible-playbook` に素通し）や `ANSIBLE_VERBOSITY=3` も使えます。

## コマンド

| コマンド | 内容 |
|---|---|
| `exalite list` | Movement 一覧（種別・パラメータシート有無） |
| `exalite syntax <mv>` | `ansible-playbook --syntax-check` |
| `exalite dryrun <mv>` | `ansible-playbook --check --diff` |
| `exalite run <mv> [--target verify\|prod]` | 実行（環境切替可） |
| `exalite verify <mv>` | 検証環境(Docker)で実行 |
| `exalite promote <mv> [-y]` | 検証で実行し成功したら本番へ投入 |
| `exalite conductor list` | Conductor 一覧（実行順を表示） |
| `exalite conductor run <cd> [--mode M] [--target T]` | Conductor を登録順に実行 |
| `exalite env up\|down\|status` | 検証用サーバ環境(Docker)の管理 |
| `exalite env check-win` | Windows 検証機への WinRM 疎通確認 (win_ping) |
| `exalite web [--host H] [--port P]` | 簡易 WebUI を起動 |
| `exalite watch <mv> [--on-change syntax\|dryrun\|run]` | 変更監視して自動実行（既定 syntax） |
| `exalite init [dir]` | サンプル生成 |

共通オプション：`--movements-dir DIR`（既定 `movements`）、`--conductors-dir DIR`（既定 `conductors`）、`--no-paramsheet`。
`--` 以降の引数は `ansible-playbook` にそのまま渡ります（例: `exalite run hello -- -vvv`）。

## 現状の制約（MVP）

- Ansible-LegacyRole / 単一 Playbook 実行に対応。Pioneer モードや Exastro の CMDB・
  代入値自動登録設定の GUI は未対応（本ツールは CLI ＋ Ansible ネイティブ解決に寄せています）。
- Conductor は逐次実行のみ。並列実行・条件分岐・待ち合わせは未対応。
- パラメータシートは 1 ホスト 1 行の CSV。Exastro の複数繰り返し（メニュー種別）は未対応。
- 検証コンテナは Linux(AlmaLinux/systemd)。Windows はコンテナ化せず、実機/VM への WinRM 接続で対応します（理由は上記セクション）。
