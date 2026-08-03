# Windows 検証環境セットアップ手順（ワークグループ / WinRM HTTP 5985 / NTLM）

別端末の **Windows PC** を exalite の検証環境として使うための、**Windows 側**の設定手順。
前提は以下（`まず手軽に` 構成）:

- ネットワーク: **ワークグループ**（Active Directory ドメイン非参加）
- 接続: **WinRM / HTTP 5985**
- 認証: **NTLM**（ローカル管理者アカウント）

> NTLM は HTTP(5985) でも **メッセージレベルで暗号化**されます（`AllowUnencrypted` は false のままで OK）。
> ただしトランスポート自体は平文なので、社内 LAN の検証用途に留め、本番や信頼できない経路では
> HTTPS(5986) 構成へ移行してください（別途手順化予定）。

---

## 0. 事前確認

- Windows PC と Mac が同一 LAN で相互到達できること。
- Windows PC の IP アドレスを控える（例: `192.168.1.50`）。PowerShell で `ipconfig`。
- Mac(コントロールノード) 側は準備済み: `pywinrm 0.5.0` / `ansible.windows` / `community.windows`。

以降の Windows 側コマンドは、すべて **「管理者として実行」した PowerShell** で行う。

---

## 1. ネットワークプロファイルを Private にする

Public プロファイルだと WinRM のファイアウォール例外が制限される。まず現在のプロファイルを確認し、
Public なら Private に変更する。

```powershell
Get-NetConnectionProfile
# 対象の InterfaceIndex を確認して:
Set-NetConnectionProfile -InterfaceIndex <番号> -NetworkCategory Private
```

---

## 2. WinRM を有効化する

`Enable-PSRemoting` で WinRM サービス起動・自動起動化・HTTP(5985) リスナー作成・
ファイアウォール例外の追加までまとめて行われる。

```powershell
Enable-PSRemoting -Force -SkipNetworkProfileCheck
```

リスナーが 5985 で立っているか確認:

```powershell
winrm enumerate winrm/config/listener
# Transport = HTTP, Port = 5985, Enabled = true を確認
```

---

## 3. 検証用のローカル管理者アカウントを作成する

Ansible が使う専用アカウントを用意する（例: ユーザー名 `ansible`）。

```powershell
$pass = Read-Host -AsSecureString "検証用アカウントのパスワード"
New-LocalUser -Name "ansible" -Password $pass -FullName "Ansible Verify" -Description "exalite verify account"
Add-LocalGroupMember -Group "Administrators" -Member "ansible"
```

> パスワードは後で Mac 側の Ansible からも使う。**平文でリポジトリに置かない**こと（後述）。

---

## 4. ローカルアカウントのリモート昇格を許可する（重要）

ワークグループのローカル管理者アカウントは、UAC のリモートトークンフィルタにより
**ネットワーク越しだと管理者権限が剥奪される**。ビルトイン Administrator 以外を使う場合は
以下のレジストリ設定が必須。これを入れないと接続はできても多くの `win_*` タスクが権限エラーになる。

```powershell
New-ItemProperty `
  -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System" `
  -Name "LocalAccountTokenFilterPolicy" -Value 1 -PropertyType DWord -Force
```

---

## 5. 認証・暗号化設定を確認する

NTLM（Negotiate）は既定で有効。**Basic は有効化しない / AllowUnencrypted は false のまま**でよい
（NTLM がメッセージ暗号化するため）。念のため確認:

```powershell
winrm get winrm/config/service/auth
#   Negotiate = true              ← NTLM。これが true なら OK
#   Basic     = false             ← false のままで良い（NTLMでは不要）
winrm get winrm/config/service
#   AllowUnencrypted = false      ← false のままで良い
```

（任意）大きめの処理でメモリ不足を避けたい場合:

```powershell
Set-Item -Path WSMan:\localhost\Shell\MaxMemoryPerShellMB -Value 1024
```

---

## 6. ファイアウォールで Mac からの 5985 を許可する

`Enable-PSRemoting` が既定の例外を追加するが、送信元を Mac の IP に絞ると安全。

```powershell
# 送信元を限定した受信規則（推奨）
New-NetFirewallRule -DisplayName "WinRM HTTP (exalite verify)" `
  -Direction Inbound -Action Allow -Protocol TCP -LocalPort 5985 `
  -RemoteAddress 192.168.1.0/24   # ← Mac のサブネット/IP に合わせる
```

---

## 7. Windows 内で疎通確認

```powershell
Test-WSMan            # ローカルの WinRM 応答確認
Test-NetConnection -ComputerName localhost -Port 5985   # TcpTestSucceeded : True
```

---

## 8. Mac 側から Ansible で疎通確認（win_ping）

Mac のターミナルで、一時的なインベントリを作って `win_ping` を打つ。

`win-verify.ini`（例。パスワードは一時確認用。恒久運用では Vault 化）:

```ini
[windows]
verify-win01 ansible_host=192.168.1.50

[windows:vars]
ansible_connection=winrm
ansible_user=ansible
ansible_password=＜手順3で設定したパスワード＞
ansible_port=5985
ansible_winrm_transport=ntlm
ansible_winrm_scheme=http
```

疎通テスト:

```bash
ansible -i win-verify.ini windows -m ansible.windows.win_ping
```

期待される結果:

```
verify-win01 | SUCCESS => {
    "changed": false,
    "ping": "pong"
}
```

これが返れば、**Mac → Windows 検証 PC の WinRM 疎通は成立**。あとは exalite の
`hosts: windows` な Movement を流せば検証実行できる。

---

## トラブルシュート

| 症状 | 主な原因と対処 |
|---|---|
| TCP 5985 に届かない | ファイアウォール（手順6）/ ネットワークプロファイルが Public（手順1）/ IP 相違 |
| `the specified credentials were rejected` | ユーザー名/パスワード誤り、またはアカウントが Administrators 未所属（手順3） |
| 接続はできるが権限エラー・アクセス拒否 | `LocalAccountTokenFilterPolicy` 未設定（手順4） |
| `ntlm: auth method ntlm requires ...` | Mac 側 `pywinrm` 不足 → `pip install pywinrm`（本環境は導入済み） |
| `basic auth` を要求される | インベントリの `ansible_winrm_transport` が ntlm になっているか確認 |
| `win_ping` モジュールが見つからない | `ansible-galaxy collection install ansible.windows`（本環境は導入済み） |

---

## セキュリティ上の注意

- `ansible_password` を **平文でコミットしない**。恒久運用では Ansible Vault で暗号化する:
  ```bash
  ansible-vault encrypt_string 'パスワード' --name 'ansible_password'
  ```
- HTTP+NTLM は LAN 内の検証用途に限定。本番/非信頼経路は **HTTPS(5986)** へ。
- 検証 PC は **VM＋スナップショット** か専用機にして、Playbook 実行前の状態へ戻せるようにする
  （Windows は Docker のように使い捨てできないため）。
- ファイアウォールの送信元は Mac の IP/サブネットに絞る。

---

## exalite 側の設定（実装済み）

検証環境のインベントリはディレクトリ形式になっており、Linux(Docker) と Windows(実機) が
共存します。

```
environments/
  .ssh/verify/id_ed25519    exalite env up が生成する検証コンテナ用の鍵
  verify/
    linux.ini               exalite env up が自動生成（検証コンテナ）
    windows.ini             ← ここに本手順で設定した Windows 機を登録（手動管理）
  prod.ini
```

`exalite.yml` は verify をディレクトリで指します。

```yaml
environments:
  verify:
    inventory: environments/verify
```

`environments/verify/windows.ini` の例（`exalite init` / `env up` が雛形を生成します）:

```ini
[windows]
verify-win01 ansible_host=192.168.1.50

[windows:vars]
ansible_connection=winrm
ansible_user=ansible
ansible_port=5985
ansible_winrm_transport=ntlm
ansible_winrm_scheme=http
ansible_password="{{ lookup('env', 'EXALITE_WIN_PASSWORD') }}"
```

パスワードは平文で置かず、環境変数（上記）か Ansible Vault を使ってください。
このファイルは既定で `.gitignore` 済みです。

使い方:

```bash
export EXALITE_WIN_PASSWORD='＜手順3で設定したパスワード＞'
exalite env check-win        # WinRM 疎通チェック（win_ping）
exalite verify ping_win      # hosts: windows の Movement を検証機で実行
exalite promote ping_win     # 検証OKなら本番(prod.ini の windows グループ)へ
```

`exalite env check-win` は windows.ini にホストが未登録なら設定を促し、登録済みなら
`ansible.windows.win_ping` を実行します（pywinrm 未導入なら明示エラー）。

## Windows コンテナを検証環境にできないのか

Docker で Windows 検証環境を使い捨てにできれば理想ですが、次の理由で採用していません。

| 制約 | 内容 |
|---|---|
| ホスト OS | Windows コンテナは **Windows ホスト**（Docker Desktop の Windows containers モード）でのみ動作。macOS/Linux では不可 |
| エディション | Windows 10/11 の **Pro / Enterprise / Education** が必要（Home は不可） |
| 同時実行 | Docker Desktop は Linux コンテナと Windows コンテナを**切り替え式**で動かすため、Linux 検証コンテナと同時には使えない |
| コントロールノード | **Ansible は Windows ネイティブ非対応**。Windows PC で exalite を動かす場合も WSL2 の中で動かし、そこから WinRM で接続することになる |
| 機能差 | Windows コンテナは systemd/sshd が無く、**再起動もドメイン参加も GUI も不可**。`win_reboot` を含む Playbook や AD 前提の設定は検証できない |
| サイズ | `servercore:ltsc2022` は展開後 5GB 超 |

Windows PC 上で exalite を動かす場合は、**WSL2 の中で exalite + Ansible を動かし、その PC 自身
（または別の Windows 機）を windows.ini に WinRM 接続先として登録**するのが、切り替え不要で
Linux 検証コンテナと同時に使える構成です。
