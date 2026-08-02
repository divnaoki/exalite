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

## 次のステップ（exalite 統合・実装予定）

1. 環境インベントリを **ディレクトリ化**して Linux(Docker) と Windows(実PC) を共存:
   `environments/verify/linux.ini`（自動生成）＋ `environments/verify/windows.ini`（手動）。
2. `movements/ping_win.yml`（`ansible.windows.win_ping`）など `hosts: windows` の Movement 追加。
3. `exalite verify <mv>` / `exalite promote <mv>` を Windows ターゲットでもそのまま利用。
4. `exalite env check-win`（Windows 検証 PC への WinRM 疎通チェック）コマンドの追加。
