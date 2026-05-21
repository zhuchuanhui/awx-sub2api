# AWX deployment for sub2api

## 日本語

このリポジトリは、`sub2api` を Oracle Linux ホストへ配布するための、
AWX 向け Ansible 構成です。

### AWX 配置ホスト

- `instance-20251213-ARM_fw` (`140.83.58.183`)

### 配布先ホスト

- `amd-instance-internal1` (`10.0.1.100`)
- `amd-instance-internal2` (`10.0.2.100`)
- `arm-instance` (`140.83.81.132`)
- `amd-instance-1` (`152.70.84.253`)
- `amd-instance-2` (`141.147.149.131`)

### 構成

- `deploy-awx.yml`: AWX コントローラーサーバー自体を Kubernetes 上に構築する playbook（構築後にリソース自動セットアップあり）
- `deploy-k8s.yml`: Kubernetes のみを構築する playbook（ホストの動的選択に対応）
- `deploy-sub2api.yml`: AWX Job Template で実行するメイン playbook。古い Python 環境のターゲットに対し、自動で Python 3.11 を検知・インストールする処理を含みます。
- `setup-awx-resources.py`: AWX 構築後に Project / Credential / Inventory / Job Template を一括作成するスクリプト（`deploy-awx.yml` から自動実行）
- `setup-awx-resources.yml`: 上記と同等のリソースを Ansible 経由で作成する playbook（代替手段）
- `setup-awx-resources.sh`: `setup-awx-resources.yml` を実行するラッパースクリプト
- `inventory/hosts.yml`: 運用 inventory（ローカル専用・`.gitignore` 対象）
- `inventory/hosts.example.yml`: 配布用サンプル inventory
- `roles/docker`: Docker Engine と Compose plugin を導入。Python 3.11 移行時のインポート失敗を避けるため、DNFモジュールはシステム標準 Python で動くように最適化されています。
- `roles/awx_k8s`: `kubeadm` ベースの Kubernetes と AWX を導入
- `roles/sub2api`: 配置ディレクトリを準備して `sub2api` を起動
- `docs/AWX_K8S_SETUP.md`: 標準 Kubernetes 上で AWX を作る手順
- `k8s/awx/`: AWX Operator と AWX CR のサンプル

### 前提条件

- ローカルに Ansible がインストールされていること
- `instance-20251213-ARM_fw` (`140.83.58.183`) へ `opc` ユーザーで SSH できること
- AWX に登録する **opc 用 SSH 秘密鍵**がローカルにあること（既定: `~/.ssh/id_rsa`）
- `inventory/hosts.yml` が環境に合わせて用意されていること（未作成なら `inventory/hosts.example.yml` をコピーして編集）

### 実行手順（推奨）

#### 1. inventory を用意する

```bash
cp inventory/hosts.example.yml inventory/hosts.yml
# ansible_host や ProxyJump を環境に合わせて編集
```

#### 2. SSH 秘密鍵パスを確認する

`group_vars/all.yml` に既定値があります（Ansible を実行するマシン上のパス）。

```yaml
awx_ssh_private_key_file: "{{ lookup('env', 'HOME') }}/.ssh/id_rsa"
```

別の鍵を使う場合は、playbook 実行時に上書きします。

```bash
ansible-playbook -i inventory/hosts.yml deploy-awx.yml \
  -e awx_ssh_private_key_file=~/.ssh/別の鍵
```

#### 3. AWX 構築 + リソース自動セットアップを実行する

```bash
cd awx-sub2api
ANSIBLE_LOCAL_TEMP=/tmp/ansible-local \
  ansible-playbook -i inventory/hosts.yml deploy-awx.yml
```

この playbook は次の 2 段階で動きます。

| 段階 | 内容 |
|------|------|
| プレイ 1 | Kubernetes / AWX Operator / AWX 本体の構築（`roles/awx_k8s`） |
| プレイ 2 | `setup-awx-resources.py` による Project・Credential・Inventory・Job Template の自動作成 |

- プレイ 1 完了時に AWX の URL と admin パスワードが表示されます（Kubernetes Secret から取得）。
- プレイ 2 ではローカルの SSH 秘密鍵を一時的に AWX ホストへコピーし、AWX API 経由で Machine Credential を登録した後、一時ファイルは削除されます。
- AWX 本体の構築だけ行い、リソース登録を省略する場合:

```bash
ansible-playbook -i inventory/hosts.yml deploy-awx.yml -e awx_setup_resources=false
```

#### 4. AWX Web UI で sub2api を配布する

1. ブラウザで [AWX Templates](http://140.83.58.183:30080/#/templates) を開く
2. 初回構築直後は **`build-awx-controller`** が不要な場合があります（ローカル Ansible で既に構築済みのため）
3. Job Template **`deploy-sub2api`** を起動し、各 `sub2api_targets` へ配布する

#### 5. リソースセットアップだけやり直す場合

AWX は起動済みで、Project / Credential / Inventory / Job Template の登録だけ再実行したいときや、新しく追加された `deploy-k8s` ジョブテンプレートを自動登録したいとき:

最新化したスクリプトを実行すると、自動的に `deploy-k8s` テンプレートが作成されます。この際、**「変数（Variables）の起動時プロンプト（Prompt on Launch）」**設定も自動で有効（オン）になります。すでに存在する他のテンプレートやプロジェクト、インベントリは上書きされず「既存」としてそのままスキップされるため、安全に再実行できます。

```bash
# 2 つ目のプレイから再開（AWX 構築をスキップ）
ANSIBLE_LOCAL_TEMP=/tmp/ansible-local \
  ansible-playbook -i inventory/hosts.yml deploy-awx.yml \
  --start-at-task "AWX リソースセットアップをスキップするか確認"
```

または AWX ホスト上 / ローカルからスクリプトを直接実行:

```bash
# 管理者パスワードは kubectl で取得（AWX ホスト上）
sudo KUBECONFIG=/etc/kubernetes/admin.conf \
  kubectl -n awx get secret awx-admin-password -o jsonpath='{.data.password}' | base64 -d

python3 setup-awx-resources.py ~/.ssh/id_rsa '<上記パスワード>'
```

### AWX 手動セットアップ（自動セットアップを使わない場合）

自動セットアップ（`setup-awx-resources.py`）を使わない場合は、AWX Web UI から次を手動で行います。

1. ローカル Ansible から `deploy-awx.yml -e awx_setup_resources=false` を実行し AWX を構築する
2. このリポジトリを AWX の Project（Git SCM）として登録する
3. Oracle Linux の `opc` 用 SSH 鍵で Machine Credential を作成する
4. AWX から各ホストへ到達できることを確認する
5. Inventory を作成し、`inventory/hosts.yml` と同じグループ・ホストを定義する
6. Job Template `build-awx-controller` / `deploy-sub2api` / `deploy-k8s` を作成する
7. 各 Job Template に Machine Credential と privilege escalation（sudo）を設定し、`deploy-k8s` では Variables の `Prompt on Launch` を有効にする

### AWX Web UI からの手動実行手順

1. AWX コントローラーサーバーにこのリポジトリを配置します。
   - 例: `/home/opc/awx-sub2api` に `git clone` または `scp` で配置します。
   - 例:
     ```bash
     ssh opc@140.83.58.183
     cd /home/opc
     rm -rf awx-sub2api
     git clone https://github.com/zhuchuanhui/awx-sub2api.git
     cd awx-sub2api
     ```
   - `awx-sub2api` が既に存在する場合は、`rm -rf awx-sub2api` で削除してから再クローンします。
   - AWX から参照できる Git リポジトリを作成するのが推奨です。
2. AWX Web UI で Project を作成します。
   - SCM Type: `Git`
   - SCM URL: このリポジトリの Git URL
   - Branch/Revision: `main`
3. Machine Credential を作成します。
   - SSH private key: `opc` の秘密鍵
   - Privilege Escalation: sudo
4. Inventory を作成し、`inventory/hosts.yml` を取り込むか、AWX 上で同じホストとグループを定義します。
   - `awx_controller` グループには `instance-20251213-ARM_fw` を登録
   - `sub2api_targets` グループには対象ホストを登録
5. Job Template を 4 つ作成します。
   - `build-awx-controller`: Project = `awx-sub2api`, Playbook = `deploy-awx.yml`, Inventory = `awx_controller`, Credential = Machine, Privilege Escalation 有効, Variables の `Prompt on Launch` 有効 (ホスト選択可能)
   - `deploy-sub2api`: Project = `awx-sub2api`, Playbook = `deploy-sub2api.yml`, Inventory = `sub2api_targets`, Credential = Machine, Privilege Escalation 有効
   - `deploy-k8s`: Project = `awx-sub2api`, Playbook = `deploy-k8s.yml`, Inventory = 対象インベントリ, Credential = Machine, Privilege Escalation 有効, Variables の `Prompt on Launch` 有効
   - `run-any-playbook`: Project = `awx-sub2api`, Playbook = `{{ selected_playbook }}`, Inventory = 対象インベントリ, Credential = Machine, Privilege Escalation 有効, Variables の `Prompt on Launch` 有効 (Playbook とホストを実行時に選択)
6. まず `build-awx-controller` を実行し、AWX コントローラーを構築します。その後、必要に応じて `deploy-sub2api` または `deploy-k8s` を実行します。

#### 役割の対応

- `deploy-awx.yml` / `roles/awx_k8s`: AWX コントローラーサーバー構築
- `deploy-k8s.yml` / `roles/awx_k8s`: Kubernetes のみの配布（AWXインストールをスキップし、ホスト選択に対応）
- `deploy-sub2api.yml` / `roles/docker`, `roles/sub2api`: `sub2api` 配布

### 実行時に配布先ホストを選択する（ローカルコントローラー含む）

新しく追加された `deploy-k8s.yml` は、実行時に `target_hosts` 変数を指定することで、構築対象のホストやグループを動的に切り替えることができます。ローカルの AWX コントローラーサーバー（`awx_controller` グループ）に構築したい場合も同様です。

#### AWX UI での設定手順
1. AWX で `deploy-k8s` ジョブテンプレートを作成します。
   * Playbook: `deploy-k8s.yml`
   * Inventory: 対象ホストが含まれるインベントリ
2. **起動時プロンプト（Prompt on Launch）** または **Survey（サーベイ）** を設定します。
   * **オプション A（変数のプロンプト化）**: ジョブテンプレートの編集画面で「Variables（変数）」の「Prompt on Launch（起動時にプロンプト）」チェックボックスを有効にします。ジョブ起動時に `target_hosts: awx_controller` や `target_hosts: amd-instance-1` のように定義して実行します。
   * **オプション B（Surveyの作成）**: ジョブテンプレートの「Survey」タブを開き、以下の質問を追加します。
     * 質問のテキスト: `配布先ホスト・グループ名`
     * 記述名 (Answer Variable Name): `target_hosts`
     * タイプ: `Text`
     * デフォルト値: `sub2api_targets`
3. ジョブを起動し、Survey または変数で指定した対象ホストのみに Kubernetes がデプロイされることを確認します。

#### コマンドラインでの実行例
ローカルから直接実行する場合：
```bash
# sub2api_targets の全ホストに配布（デフォルト）
ansible-playbook -i inventory/hosts.yml deploy-k8s.yml

# ローカルの AWX コントローラーサーバーのみに配布
ansible-playbook -i inventory/hosts.yml deploy-k8s.yml -e "target_hosts=awx_controller"

# 特定のホストのみに配布
ansible-playbook -i inventory/hosts.yml deploy-k8s.yml -e "target_hosts=amd-instance-1"
```

### 便利な変数

`group_vars/all.yml` および AWX の Inventory vars / Survey で利用できます。

```yaml
# deploy-awx.yml プレイ 2（リソース自動セットアップ）
awx_ssh_private_key_file: "{{ lookup('env', 'HOME') }}/.ssh/id_rsa"  # 既定。ローカル opc 用秘密鍵
awx_setup_resources: true   # false で Project 等の自動作成をスキップ

# deploy-sub2api.yml
sub2api_deploy_dir: /home/opc/sub2api-deploy
sub2api_env_overrides:
  TZ: Asia/Tokyo
```

### トラブルシューティング

| 症状 | 原因 | 対処 |
|------|------|------|
| `awx_ssh_private_key_file is defined` で失敗 | SSH 秘密鍵パス未設定・ファイル不存在 | `group_vars/all.yml` を確認するか `-e awx_ssh_private_key_file=~/.ssh/id_rsa` を指定 |
| `setup-awx-resources.py` が Permission denied | 一時ファイルが root 所有 | `deploy-awx.yml` を最新版で再実行（`--start-at-task` でプレイ 2 から可） |
| `kubectl ... admin.conf: permission denied` | プレイ 2 で `kubectl` に sudo が必要 | 最新の `deploy-awx.yml` を使用（admin パスワード取得タスクは `become: true`） |
| プレイ 1 は成功、プレイ 2 のみ失敗 | AWX は起動済み | 上記「リソースセットアップだけやり直す」を参照 |
| AWX UI に Job Template が無い | プレイ 2 未実行または失敗 | `awx_setup_resources=false` を付けていないか確認し、プレイ 2 を再実行 |
| AWX Job で `Are you sure you want to continue connecting (yes/no)?` | AWX ランナーは `ansible.cfg` の `host_key_checking` を無視することがある | Project を SCM 同期後、Inventory 変数に `ansible_host_key_checking: false` を設定する（下記） |
| `Could not import the dnf python module using /usr/bin/python3.11` | DNFモジュールはシステム標準Pythonに依存するため、Python 3.11上で動かない | プレイブック側で dnf タスクのみ自動でシステム標準Pythonに戻して実行するように修正されました。 |
| DNF インストール中に `Killed` で失敗する（メモリ不足） | 1GB等の少メモリホストでDNFがキャッシュパース時にメモリ枯渇した | プレイブック側で dnf 実行前に `dnf clean all` を行い、不要ドキュメントを省く `--setopt=tsflags=nodocs` を追加してメモリ消費を抑えるよう修正されました。 |

#### AWX Job で SSH ホスト鍵確認が止まる場合

AWX から `deploy-sub2api` を実行するとき、ランナーは対話的な `yes/no` を受け付けません。
Inventory **`sub2api-inventory`** の Variables に次を追加するか、`setup-awx-resources.py` を再実行して変数を更新してください。

```yaml
ansible_host_key_checking: false
ansible_ssh_common_args: "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
```

`ProxyJump` 利用ホスト（`amd-instance-internal*`）はホスト変数で両方を指定します。

```yaml
ansible_ssh_common_args: "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ProxyJump=opc@140.83.58.183"
```

リポジトリ最新版の `deploy-sub2api.yml` にも playbook 側の既定値があります。AWX Project の **SCM 同期**後に Job を再実行してください。

### 補足

- `instance-20251213-ARM_fw` は AWX の管理ホストであり、この playbook の配布先ではありません。
- `amd-instance-internal1` と `amd-instance-internal2` は `ProxyJump=opc@140.83.58.183` を使う前提です。
- playbook は公式の `docker-deploy.sh` を取得し、`docker-compose.yml` が未作成のときだけ初期生成を行います。
- `.env` は毎回 Ansible から生成するため、AWX 側の変数を正とする運用にできます。
- `kubeadm` ベースの Kubernetes で AWX を作る手順は `docs/AWX_K8S_SETUP.md` を参照してください。

### 配布

`package-release.sh` を使って、配布可能なアーカイブを作成できます。

```bash
./package-release.sh
```

このコマンドは、playbook、role、ドキュメント、サンプル inventory、および Kubernetes マニフェストを含む tarball を生成します。`.git` ヒストリは含みません。

#### 配布用 inventory の準備

- `inventory/hosts.example.yml` は配布用のサニタイズ済みサンプルです。
- 環境に合わせてコピーまたはリネームし、`inventory/hosts.yml` として保存してください。
- ローカルのホスト inventory は `inventory/hosts.yml` を `.gitignore` に登録して管理します。

#### 配布すべきファイル

- `deploy-awx.yml`: AWX コントローラー配布用 playbook
- `deploy-sub2api.yml`: AWX Job Template 実行用 playbook
- `setup-awx-resources.py`: AWX リソース自動セットアップスクリプト
- `group_vars/all.yml`: 共通変数（`awx_ssh_private_key_file` など）
- `docs/AWX_K8S_SETUP.md`: AWX/Kubernetes セットアップガイド
- `k8s/awx/`: AWX Operator サンプルマニフェスト
- `roles/`: Docker / AWX/K8s / sub2api 配布用 Ansible role

## English

This repository contains an AWX-friendly Ansible layout for deploying
`sub2api` to Oracle Linux hosts.

### AWX host

- `instance-20251213-ARM_fw` (`140.83.58.183`)

### Deployment targets

- `amd-instance-internal1` (`10.0.1.100`)
- `amd-instance-internal2` (`10.0.2.100`)
- `arm-instance` (`140.83.81.132`)
- `amd-instance-1` (`152.70.84.253`)
- `amd-instance-2` (`141.147.149.131`)

### Layout

- `deploy-awx.yml`: builds the AWX controller on Kubernetes and optionally runs automated resource setup
- `deploy-k8s.yml`: builds Kubernetes only (supports dynamic target host selection)
- `deploy-sub2api.yml`: main playbook for the AWX Job Template. Automatically detects and installs Python 3.11 on hosts with older Python versions.
- `setup-awx-resources.py`: creates Project / Credential / Inventory / Job Templates via AWX API (invoked from `deploy-awx.yml`)
- `setup-awx-resources.yml`: Ansible-based alternative for the same resources
- `setup-awx-resources.sh`: wrapper for `setup-awx-resources.yml`
- `inventory/hosts.yml`: local inventory (gitignored)
- `inventory/hosts.example.yml`: sanitized sample inventory for distribution
- `roles/docker`: installs Docker Engine and the Compose plugin. DNF tasks are optimized to run under the system default Python to avoid python3.11 import failures.
- `roles/awx_k8s`: installs `kubeadm`-based Kubernetes and AWX
- `roles/sub2api`: prepares the deployment directory and starts `sub2api`
- `docs/AWX_K8S_SETUP.md`: setup guide for running AWX on standard Kubernetes
- `k8s/awx/`: sample AWX Operator and AWX custom resource manifests

### Prerequisites

- Ansible installed on your workstation
- SSH access to `instance-20251213-ARM_fw` (`140.83.58.183`) as `opc`
- Local `opc` SSH private key (default: `~/.ssh/id_rsa`)
- `inventory/hosts.yml` prepared for your environment (copy from `inventory/hosts.example.yml`)

### Recommended procedure

#### 1. Prepare inventory

```bash
cp inventory/hosts.example.yml inventory/hosts.yml
# Edit ansible_host and ProxyJump for your environment
```

#### 2. Confirm SSH private key path

Default in `group_vars/all.yml` (path on the machine that runs Ansible):

```yaml
awx_ssh_private_key_file: "{{ lookup('env', 'HOME') }}/.ssh/id_rsa"
```

Override at runtime if needed:

```bash
ansible-playbook -i inventory/hosts.yml deploy-awx.yml \
  -e awx_ssh_private_key_file=~/.ssh/other_key
```

#### 3. Build AWX and auto-configure resources

```bash
cd awx-sub2api
ANSIBLE_LOCAL_TEMP=/tmp/ansible-local \
  ansible-playbook -i inventory/hosts.yml deploy-awx.yml
```

| Play | Purpose |
|------|---------|
| Play 1 | Kubernetes + AWX Operator + AWX (`roles/awx_k8s`) |
| Play 2 | Project / Credential / Inventory / Job Templates via `setup-awx-resources.py` |

- Play 1 prints the AWX URL and admin password from the Kubernetes secret.
- Play 2 copies your local SSH key temporarily, registers the Machine Credential, then deletes temp files.
- AWX only, skip resource setup: `-e awx_setup_resources=false`

#### 4. Deploy sub2api from AWX UI

1. Open [AWX Templates](http://140.83.58.183:30080/#/templates)
2. Skip `build-awx-controller` if AWX was already built by local Ansible
3. Launch **`deploy-sub2api`** to deploy to `sub2api_targets`

#### 5. Re-running Setup / Updating Templates (Two Methods)

If AWX is already running and you want to register or update configuration details (like adding the new `deploy-k8s` template or Survey specifications), you can do so using one of the **two patterns** below. Existing resources are skipped (not overwritten) so both methods are completely safe to re-run.

##### Pattern A: Direct Script Run (Recommended, Fast)
Pushes settings directly via the AWX API without touching the server stack. Takes only **a few seconds** to complete.

1. Fetch the admin password on the AWX controller host (skip if you already know the password):
   ```bash
   sudo KUBECONFIG=/etc/kubernetes/admin.conf \
     kubectl -n awx get secret awx-admin-password -o jsonpath='{.data.password}' | base64 -d
   ```
2. Run the script directly with the credentials:
   ```bash
   python3 setup-awx-resources.py ~/.ssh/id_rsa '<PASSWORD>'
   ```

##### Pattern B: Ansible Playbook Run (Partial Execution)
Starts the deployment playbook from the second play (resource configuration phase) using Ansible.

```bash
ANSIBLE_LOCAL_TEMP=/tmp/ansible-local \
  ansible-playbook -i inventory/hosts.yml deploy-awx.yml \
  --start-at-task "AWX リソースセットアップをスキップするか確認"
```

### Manual AWX setup (without automation)

If you do not use `setup-awx-resources.py`, configure AWX in the Web UI after `deploy-awx.yml -e awx_setup_resources=false`:

1. Build AWX with local Ansible
2. Register this repo as a Git Project
3. Create Machine Credential with the `opc` SSH key
4. Verify connectivity to all targets
5. Create Inventory matching `inventory/hosts.yml`
6. Create Job Templates `build-awx-controller`, `deploy-sub2api`, and `deploy-k8s`
7. Enable sudo on templates, and enable `Prompt on Launch` for Variables on `deploy-k8s`

### Manual setup from AWX Web UI

1. Place this repository on the AWX controller host or make it accessible via Git.
   - Example:
     ```bash
     ssh opc@140.83.58.183
     cd /home/opc
     rm -rf awx-sub2api
     git clone https://github.com/zhuchuanhui/awx-sub2api.git
     cd awx-sub2api
     ```
   - If `awx-sub2api` already exists, remove it first with `rm -rf awx-sub2api` before cloning again.
2. Create an AWX Project:
   - SCM Type: `Git`
   - SCM URL: your repository URL
   - Branch/Revision: `main`
3. Create a Machine Credential using the `opc` SSH key and enable sudo.
4. Create Inventory and define the same hosts/groups as `inventory/hosts.yml`.
   - `awx_controller` group for `instance-20251213-ARM_fw`
   - `sub2api_targets` group for the target hosts
5. Create three Job Templates:
   - `build-awx-controller`: playbook `deploy-awx.yml`, inventory `awx_controller`, credential `Machine`, enable privilege escalation
   - `deploy-sub2api`: playbook `deploy-sub2api.yml`, inventory `sub2api_targets`, credential `Machine`, enable privilege escalation
   - `deploy-k8s`: playbook `deploy-k8s.yml`, inventory target inventory, credential `Machine`, enable privilege escalation, enable `Prompt on Launch` for Variables
6. Run `build-awx-controller` first, then run `deploy-sub2api` or `deploy-k8s` as needed.

#### Role mapping

- `deploy-awx.yml` / `roles/awx_k8s`: build AWX controller server
- `deploy-k8s.yml` / `roles/awx_k8s`: deploy Kubernetes only (skips AWX install, supports host selection)
- `deploy-sub2api.yml` / `roles/docker`, `roles/sub2api`: deploy `sub2api`

### Selecting Target Hosts at Runtime (Including Local Controller)

The newly added `deploy-k8s.yml` allows you to dynamically switch target hosts or groups by defining the `target_hosts` variable at runtime. This also applies when deploying to the local AWX controller host (`awx_controller` group).

#### Configuration Steps in AWX UI
1. Create a `deploy-k8s` Job Template in AWX:
   * Playbook: `deploy-k8s.yml`
   * Inventory: The inventory containing your target hosts
2. Set up **Prompt on Launch** or a **Survey**:
   * **Option A (Variables Prompt)**: Check the "Prompt on Launch" checkbox under "Variables" on the job template edit screen. Pass `target_hosts: awx_controller` or `target_hosts: amd-instance-1` when launching.
   * **Option B (Survey)**: Go to the "Survey" tab on the job template and add a question:
     * Question Text: `Target Host or Group Name`
     * Answer Variable Name: `target_hosts`
     * Answer Type: `Text`
     * Default Answer: `sub2api_targets`
3. Launch the job and ensure Kubernetes is deployed only to the hosts specified in the survey or variables.

#### CLI Execution Examples
To run directly from your terminal:
```bash
# Deploy to all sub2api_targets (default)
ansible-playbook -i inventory/hosts.yml deploy-k8s.yml

# Deploy only to the local AWX controller server
ansible-playbook -i inventory/hosts.yml deploy-k8s.yml -e "target_hosts=awx_controller"

# Deploy to a specific host
ansible-playbook -i inventory/hosts.yml deploy-k8s.yml -e "target_hosts=amd-instance-1"
```

### Useful variables

Defined in `group_vars/all.yml` and overridable in AWX Inventory / Survey.

```yaml
awx_ssh_private_key_file: "{{ lookup('env', 'HOME') }}/.ssh/id_rsa"
awx_setup_resources: true
sub2api_deploy_dir: /home/opc/sub2api-deploy
sub2api_env_overrides:
  TZ: Asia/Tokyo
```

### Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `awx_ssh_private_key_file` assert fails | Missing or wrong key path | Check `group_vars/all.yml` or pass `-e awx_ssh_private_key_file=~/.ssh/id_rsa` |
| `setup-awx-resources.py` Permission denied | Temp files owned by root | Re-run play 2 with latest `deploy-awx.yml` |
| `kubectl ... admin.conf: permission denied` | kubectl needs sudo on play 2 | Use latest `deploy-awx.yml` |
| Play 1 OK, play 2 failed | AWX already up | Re-run from play 2 only (see above) |
| No Job Templates in AWX UI | Play 2 skipped or failed | Ensure `awx_setup_resources` is not `false`; re-run play 2 |
| AWX Job stuck on SSH `yes/no` host key prompt | AWX runner may ignore project `ansible.cfg` | Set `ansible_host_key_checking: false` on inventory `sub2api-inventory`, sync SCM, re-run job (see Japanese section) |
| `Could not import the dnf python module using /usr/bin/python3.11` | DNF library depends on system default Python and cannot be imported in Python 3.11 | Fixed by temporarily switching python interpreter back to system default during DNF tasks. |
| DNF install fails with `Killed` (out of memory) | DNF process killed due to RAM exhaustion on small hosts (e.g. 1GB RAM) during metadata parsing | Fixed by cleaning cache beforehand and adding `--setopt=tsflags=nodocs` to reduce memory footprints. |

### Notes

- `instance-20251213-ARM_fw` is the AWX controller host and is not a deployment target for this playbook.
- `amd-instance-internal1` and `amd-instance-internal2` are configured to use `ProxyJump=opc@140.83.58.183`.
- The playbook downloads the official `docker-deploy.sh` bootstrap script and only initializes files when `docker-compose.yml` does not already exist.
- The `.env` file is rendered from Ansible variables each run, so AWX can remain the source of truth.
- For `kubeadm`-based Kubernetes setup, see `docs/AWX_K8S_SETUP.md`.

### Distribution

Use `package-release.sh` to create a portable distribution archive for handoff.

```bash
./package-release.sh
```

This produces a tarball containing the playbooks, role code, docs, sample inventory, and Kubernetes manifests without `.git` history.

### Inventory setup for distribution

- `inventory/hosts.example.yml` is a sanitized example for distribution.
- Copy or rename it to `inventory/hosts.yml` and update `ansible_host` values for your environment.
- Keep local host inventory out of source control by using `.gitignore` for `inventory/hosts.yml`.

### What to distribute

- `deploy-awx.yml`: AWX controller deployment playbook
- `deploy-sub2api.yml`: AWX Job Template playbook
- `setup-awx-resources.py`: automated AWX resource setup script
- `group_vars/all.yml`: shared variables including `awx_ssh_private_key_file`
- `docs/AWX_K8S_SETUP.md`: AWX/Kubernetes setup guide
- `k8s/awx/`: sample AWX Operator manifests
- `roles/`: Ansible roles for Docker, AWX/K8s, and sub2api deployment

## 中文

这个仓库提供了一套适用于 AWX 的 Ansible 结构，用于将 `sub2api`
部署到 Oracle Linux 主机。

### AWX 主机

- `instance-20251213-ARM_fw` (`140.83.58.183`)

### 部署目标主机

- `amd-instance-internal1` (`10.0.1.100`)
- `amd-instance-internal2` (`10.0.2.100`)
- `arm-instance` (`140.83.81.132`)
- `amd-instance-1` (`152.70.84.253`)
- `amd-instance-2` (`141.147.149.131`)

### 目录结构

- `deploy-awx.yml`: 在 Kubernetes 上构建 AWX，并可自动完成资源配置
- `deploy-k8s.yml`: 仅构建 Kubernetes（支持动态选择目标主机）
- `deploy-sub2api.yml`: AWX Job Template 使用的主 playbook。包含针对旧版 Python 目标主机的自动检测和 Python 3.11 安装逻辑。
- `setup-awx-resources.py`: 通过 AWX API 一键创建资源（由 `deploy-awx.yml` 调用）
- `setup-awx-resources.yml`: 用 Ansible 创建相同资源的替代 playbook
- `setup-awx-resources.sh`: `setup-awx-resources.yml` 的包装脚本
- `inventory/hosts.yml`: 本地 inventory（已 gitignore）
- `inventory/hosts.example.yml`: 分发用示例 inventory
- `roles/docker`: 安装 Docker Engine 和 Compose 插件。优化了 DNF 模块的运行机制，使其使用系统默认 Python 以避免 Python 3.11 导入失败。
- `roles/awx_k8s`: 安装基于 `kubeadm` 的 Kubernetes 与 AWX
- `roles/sub2api`: 准备部署目录并启动 `sub2api`
- `docs/AWX_K8S_SETUP.md`: 在标准 Kubernetes 上部署 AWX 的步骤说明
- `k8s/awx/`: AWX Operator 与 AWX 自定义资源示例

### 前提条件

- 本地已安装 Ansible
- 能以 `opc` 用户 SSH 到 `instance-20251213-ARM_fw` (`140.83.58.183`)
- 本地有 **opc 用 SSH 私钥**（默认 `~/.ssh/id_rsa`）
- 已准备 `inventory/hosts.yml`（可从 `inventory/hosts.example.yml` 复制）

### 推荐执行步骤

#### 1. 准备 inventory

```bash
cp inventory/hosts.example.yml inventory/hosts.yml
# 按环境修改 ansible_host 和 ProxyJump
```

#### 2. 确认 SSH 私钥路径

`group_vars/all.yml` 中的默认值（Ansible 执行机器上的路径）：

```yaml
awx_ssh_private_key_file: "{{ lookup('env', 'HOME') }}/.ssh/id_rsa"
```

需要其他密钥时在命令行覆盖：

```bash
ansible-playbook -i inventory/hosts.yml deploy-awx.yml \
  -e awx_ssh_private_key_file=~/.ssh/其他密钥
```

#### 3. 构建 AWX 并自动配置资源

```bash
cd awx-sub2api
ANSIBLE_LOCAL_TEMP=/tmp/ansible-local \
  ansible-playbook -i inventory/hosts.yml deploy-awx.yml
```

| 阶段 | 内容 |
|------|------|
| Play 1 | Kubernetes / AWX Operator / AWX 本体（`roles/awx_k8s`） |
| Play 2 | `setup-awx-resources.py` 创建 Project / Credential / Inventory / Job Template |

- Play 1 结束时会显示 AWX URL 和 admin 密码。
- Play 2 会临时复制本地 SSH 私钥、注册 Machine Credential 后删除临时文件。
- 仅构建 AWX、跳过资源注册：`-e awx_setup_resources=false`

#### 4. 在 AWX UI 中部署 sub2api

1. 打开 [AWX Templates](http://140.83.58.183:30080/#/templates)
2. 若本地 Ansible 已构建 AWX，可跳过 `build-awx-controller`
3. 启动 **`deploy-sub2api`** 部署到各 `sub2api_targets`

#### 5. 重新配置或更新模板配置（两种方式）

如果 AWX 已经运行，您只想注册或更新配置内容（例如添加新的 `deploy-k8s` 模板或 Survey 问卷配置），可以使用以下 **两种模式** 之一进行操作。由于已存在的项目和清单将被跳过（不会被覆盖），因此这两种方法都是安全的。

##### 模式 A：直接执行配置脚本（推荐，快速）
无需重新部署 AWX 服务器，直接通过 API 导入配置信息。只需 **数秒** 即可完成。

1. 在 AWX 控制器主机上获取管理员密码（若在本地执行且已知密码可跳过此步）：
   ```bash
   sudo KUBECONFIG=/etc/kubernetes/admin.conf \
     kubectl -n awx get secret awx-admin-password -o jsonpath='{.data.password}' | base64 -d
   ```
2. 使用凭据直接运行脚本：
   ```bash
   python3 setup-awx-resources.py ~/.ssh/id_rsa '<管理员密码>'
   ```

##### 模式 B：运行 Ansible Playbook（部分执行）
使用 Ansible 并通过指定任务从第二个 Play（资源配置阶段）开始运行部署 playbook。

```bash
ANSIBLE_LOCAL_TEMP=/tmp/ansible-local \
  ansible-playbook -i inventory/hosts.yml deploy-awx.yml \
  --start-at-task "AWX リソースセットアップをスキップするか確認"
```

### 手动 AWX 配置（不使用自动脚本）

使用 `deploy-awx.yml -e awx_setup_resources=false` 构建 AWX 后，在 Web UI 中手动完成：Project、Machine Credential、Inventory、Job Template（`build-awx-controller` / `deploy-sub2api` / `deploy-k8s`，并在 `deploy-k8s` 的 Variables 中启用 `Prompt on Launch`）等（步骤同日文「手動セットアップ」）。

### 从 AWX Web UI 手动执行

1. 将此仓库放置到 AWX 控制器服务器，或通过 Git 使其可被访问。
   - 示例：
     ```bash
     ssh opc@140.83.58.183
     cd /home/opc
     rm -rf awx-sub2api
     git clone https://github.com/zhuchuanhui/awx-sub2api.git
     cd awx-sub2api
     ```
   - 如果 `awx-sub2api` 已经存在，请先使用 `rm -rf awx-sub2api` 删除，然后再重新克隆。
2. 在 AWX Web UI 中创建 Project：
   - SCM Type: `Git`
   - SCM URL: 你的仓库 URL
   - Branch/Revision: `main`
3. 创建 Machine Credential，使用 `opc` 的 SSH 私钥，并启用 sudo。
4. 创建 Inventory，并定义与 `inventory/hosts.yml` 相同的主机与组。
   - `awx_controller` 组包含 `instance-20251213-ARM_fw`
   - `sub2api_targets` 组包含目标主机
5. 创建三个 Job Template：
   - `build-awx-controller`: playbook `deploy-awx.yml`，inventory `awx_controller`，credential `Machine`，启用 privilege escalation
   - `deploy-sub2api`: playbook `deploy-sub2api.yml`，inventory `sub2api_targets`，credential `Machine`，启用 privilege escalation
   - `deploy-k8s`: playbook `deploy-k8s.yml`，目标清单，credential `Machine`，启用 privilege escalation，在 Variables 中启用 `Prompt on Launch`
6. 先运行 `build-awx-controller`，然后根据需要运行 `deploy-sub2api` 或 `deploy-k8s`。

#### 角色对应

- `deploy-awx.yml` / `roles/awx_k8s`: 构建 AWX 控制器服务器
- `deploy-k8s.yml` / `roles/awx_k8s`: 仅部署 Kubernetes（跳过 AWX 安装，支持选择主机）
- `deploy-sub2api.yml` / `roles/docker`, `roles/sub2api`: 部署 `sub2api`

### 运行时选择部署目标主机（包括本地控制器）

新增加的 `deploy-k8s.yml` 允许在运行时通过定义 `target_hosts` 变量来动态切换目标主机或主机组。如果您想部署到本地 AWX 控制器服务器（`awx_controller` 组），此配置同样适用。

#### AWX UI 配置步骤
1. 在 AWX 中创建 `deploy-k8s` 作业模板（Job Template）：
   * Playbook: `deploy-k8s.yml`
   * Inventory: 包含目标主机的清单（Inventory）
2. 配置 **启动时提示（Prompt on Launch）** 或 **调查问卷（Survey）**：
   * **选项 A（变量提示）**: 在作业模板编辑界面的“Variables（变量）”区域勾选“Prompt on Launch（启动时提示）”。启动时传递 `target_hosts: awx_controller` 或 `target_hosts: amd-instance-1`。
   * **选项 B（Survey 问卷）**: 在作业模板的“Survey”标签页添加以下问题：
     * 问题文本: `目标主机或主机组名称`
     * 变量名称 (Answer Variable Name): `target_hosts`
     * 答案类型: `Text`
     * 默认答案: `sub2api_targets`
3. 启动作业，并确保 Kubernetes 仅部署到 Survey 或变量中指定的主机上。

#### 命令行执行示例
从终端直接运行：
```bash
# 部署到所有 sub2api_targets（默认）
ansible-playbook -i inventory/hosts.yml deploy-k8s.yml

# 仅部署到本地 AWX 控制器服务器
ansible-playbook -i inventory/hosts.yml deploy-k8s.yml -e "target_hosts=awx_controller"

# 部署到指定主机
ansible-playbook -i inventory/hosts.yml deploy-k8s.yml -e "target_hosts=amd-instance-1"
```

### 常用变量

见 `group_vars/all.yml`，可在 AWX Inventory / Survey 中覆盖。

```yaml
awx_ssh_private_key_file: "{{ lookup('env', 'HOME') }}/.ssh/id_rsa"
awx_setup_resources: true
sub2api_deploy_dir: /home/opc/sub2api-deploy
sub2api_env_overrides:
  TZ: Asia/Tokyo
```

### 故障排除

| 现象 | 原因 | 处理 |
|------|------|------|
| `awx_ssh_private_key_file` 断言失败 | 未设置或密钥文件不存在 | 检查 `group_vars/all.yml` 或 `-e awx_ssh_private_key_file=~/.ssh/id_rsa` |
| `setup-awx-resources.py` Permission denied | 临时文件属主为 root | 用最新 `deploy-awx.yml` 重跑 Play 2 |
| `kubectl ... admin.conf: permission denied` | Play 2 中 kubectl 需要 sudo | 使用最新 `deploy-awx.yml` |
| Play 1 成功、Play 2 失败 | AWX 已运行 | 仅重跑 Play 2（见上文） |
| AWX UI 无 Job Template | Play 2 未执行或失败 | 确认未设置 `awx_setup_resources=false`，重跑 Play 2 |
| AWX Job 停在 SSH `yes/no` 提示 | AWX 运行器可能不读取项目 `ansible.cfg` | 在 Inventory 设置 `ansible_host_key_checking: false`，同步 SCM 后重跑（见日文说明） |
| `Could not import the dnf python module using /usr/bin/python3.11` | DNF 依赖于系统默认 Python，而在 Python 3.11 下无法导入该库 | 现已在 DNF 任务中自动切换回系统默认 Python 进行处理。 |
| DNF 安装由于 `Killed` 失败（内存不足） | 内存较小（如 1GB）的主机在解析 DNF 缓存时耗尽内存 | 现已在 DNF 执行前清理缓存，并添加了 `--setopt=tsflags=nodocs` 以最大程度降低内存占用。 |

### 说明

- `instance-20251213-ARM_fw` 是 AWX 控制主机，不是这个 playbook 的部署目标。
- `amd-instance-internal1` 和 `amd-instance-internal2` 默认通过 `ProxyJump=opc@140.83.58.183` 连接。
- playbook 会下载官方 `docker-deploy.sh` 引导脚本，并且仅在 `docker-compose.yml` 不存在时执行初始化。
- `.env` 文件会在每次执行时由 Ansible 重新生成，因此可以将 AWX 变量作为唯一配置来源。
- 如果要在基于 `kubeadm` 的 Kubernetes 上部署 AWX，请参考 `docs/AWX_K8S_SETUP.md`。

### 分发

使用 `package-release.sh` 创建一个可移植的分发归档文件。

```bash
./package-release.sh
```

此归档将包含 playbook、role 代码、文档、示例 inventory 和 Kubernetes 清单，并且不包含 `.git` 历史记录。

#### 配置分发 inventory

- `inventory/hosts.example.yml` 是一个经过清理的分发示例。
- 将其复制或重命名为 `inventory/hosts.yml`，并根据你的环境更新 `ansible_host` 值。
- 使用 `.gitignore` 排除 `inventory/hosts.yml`，以避免将本地 inventory 提交到版本控制。

#### 需要分发的内容

- `deploy-awx.yml`: AWX 控制器部署 playbook
- `deploy-sub2api.yml`: AWX Job Template playbook
- `setup-awx-resources.py`: AWX 资源自动配置脚本
- `group_vars/all.yml`: 公共变量（含 `awx_ssh_private_key_file`）
- `docs/AWX_K8S_SETUP.md`: AWX/Kubernetes 设置指南
- `k8s/awx/`: AWX Operator 示例清单
- `roles/`: Docker、AWX/K8s 和 sub2api 部署的 Ansible role
