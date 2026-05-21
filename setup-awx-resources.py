#!/usr/bin/env python3
"""
AWX リソース初期セットアップスクリプト
Ansible の become 問題を回避するため、AWX REST API を直接呼び出します。

使い方:
    python3 setup-awx-resources.py [SSH秘密鍵ファイルパス] [AWXパスワード]

引数を省略した場合は対話的に入力します。
"""

import sys
import os
import json
import getpass
import urllib.request
import urllib.error
import urllib.parse
import base64
import time
import yaml

# ── 設定 ────────────────────────────────────────────────────
AWX_HOST     = "http://140.83.58.183:30080"
AWX_USERNAME = "admin"
AWX_ORG      = "Default"
SCM_URL      = "https://github.com/zhuchuanhui/awx-sub2api.git"
SCM_BRANCH   = "main"
PROJECT_NAME = "awx-sub2api"
CRED_NAME    = "opc-machine-credential"
INV_NAME     = "sub2api-inventory"

# AWX ジョブランナーはプロジェクト ansible.cfg を読まないため SSH オプションを inventory 変数で渡す
SSH_HOST_KEY_OPTS = "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
PROXY_JUMP_TARGET = "opc@140.83.58.183"
PROXY_SSH_ARGS = f"{SSH_HOST_KEY_OPTS} -o ProxyJump={PROXY_JUMP_TARGET}"

# Inventory 定義（inventory/hosts.yml と同じ構成）
INVENTORY_GROUPS = [
    {
        "name": "awx_controller",
        "hosts": [
            {"name": "instance-20251213-ARM_fw", "vars": {"ansible_host": "140.83.58.183"}},
        ],
    },
    {
        "name": "sub2api_targets",
        "hosts": [
            {"name": "amd-instance-internal1", "vars": {"ansible_host": "10.0.1.100",  "ansible_ssh_common_args": PROXY_SSH_ARGS}},
            {"name": "amd-instance-internal2", "vars": {"ansible_host": "10.0.2.100",  "ansible_ssh_common_args": PROXY_SSH_ARGS}},
            {"name": "arm-instance",            "vars": {"ansible_host": "140.83.81.132"}},
            {"name": "amd-instance-1",          "vars": {"ansible_host": "152.70.84.253"}},
            {"name": "amd-instance-2",          "vars": {"ansible_host": "141.147.149.131"}},
        ],
    },
]

JOB_TEMPLATES = [
    {
        "name": "build-awx-controller",
        "playbook": "deploy-awx.yml",
        "limit": "awx_controller",
        "description": "AWX コントローラーサーバーを Kubernetes 上に構築する",
    },
    {
        "name": "deploy-sub2api",
        "playbook": "deploy-sub2api.yml",
        "limit": "sub2api_targets",
        "description": "sub2api を対象ホストに配布・起動する",
    },
    {
        "name": "deploy-k8s",
        "playbook": "deploy-k8s.yml",
        "limit": "",
        "description": "Kubernetes (kubeadm/Calico) のみを対象ホストに配布する",
        "ask_variables_on_launch": True,
    },
]

# Inventory レベルの共通変数。
# ansible_python_interpreter を明示することで Python 3.6 ホストでも
# AWX ランナーが適切な Python を選択できる（auto_silent は ansible.cfg 未参照環境で無効）。
INV_VARS = yaml.dump({
    "ansible_user": "opc",
    "ansible_become": True,
    "ansible_host_key_checking": False,
    "ansible_ssh_common_args": SSH_HOST_KEY_OPTS,
    "ansible_python_interpreter": "auto_legacy_silent",
    "sub2api_deploy_dir": "/home/opc/sub2api-deploy",
    "sub2api_compose_project_name": "sub2api-deploy",
}, default_flow_style=False, allow_unicode=True)


# ── API ヘルパー ─────────────────────────────────────────────
class AWXClient:
    def __init__(self, host, username, password):
        self.base = host.rstrip("/") + "/api/v2"
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        self.headers = {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
        }

    def _req(self, method, path, body=None, ok=(200, 201, 204)):
        url = self.base + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers=self.headers, method=method)
        try:
            with urllib.request.urlopen(req) as r:
                raw = r.read()
                return r.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raw = e.read()
            return e.code, json.loads(raw) if raw else {}

    def get(self, path):
        s, j = self._req("GET", path)
        return j

    def post(self, path, body, skip_if_exists=False):
        s, j = self._req("POST", path, body)
        if s in (200, 201):
            return j
        if s == 400 and skip_if_exists:
            # 既存エラーは無視
            return None
        raise RuntimeError(f"POST {path} → {s}: {j}")


def find_or_none(client, path, name):
    result = client.get(f"{path}?name={urllib.parse.quote(name)}")
    items = result.get("results", [])
    return items[0] if items else None


def find_in_results(client, path):
    """クエリ付きパスで一覧検索し、先頭の結果を返す。"""
    result = client.get(path)
    items = result.get("results", [])
    return items[0] if items else None


def ensure(label, client, path, name, body):
    """存在すればそれを返し、なければ作成する"""
    existing = find_or_none(client, path, name)
    if existing:
        print(f"  ✓ {label} '{name}' は既存 (ID={existing['id']})")
        return existing
    created = client.post(path, body)
    print(f"  ✨ {label} '{name}' を作成しました (ID={created['id']})")
    return created


# ── メイン処理 ───────────────────────────────────────────────
def main():
    # 引数 / 対話入力
    ssh_key_file = sys.argv[1] if len(sys.argv) > 1 else ""
    awx_password = sys.argv[2] if len(sys.argv) > 2 else ""

    if not ssh_key_file:
        default = os.path.expanduser("~/.ssh/id_ed25519" if os.path.exists(os.path.expanduser("~/.ssh/id_ed25519")) else "~/.ssh/id_rsa")
        ssh_key_file = input(f"SSH 秘密鍵ファイルパス [{default}]: ").strip() or default

    ssh_key_file = os.path.expanduser(ssh_key_file)

    if not awx_password:
        awx_password = getpass.getpass("AWX admin パスワード: ")

    if not os.path.exists(ssh_key_file):
        print(f"エラー: SSH 鍵ファイルが見つかりません: {ssh_key_file}", file=sys.stderr)
        sys.exit(1)

    with open(ssh_key_file, "r") as f:
        ssh_private_key = f.read()

    print(f"\n{'='*40}")
    print(" AWX リソースセットアップ開始")
    print(f"{'='*40}")
    print(f"  AWX host : {AWX_HOST}")
    print(f"  SSH key  : {ssh_key_file}\n")

    client = AWXClient(AWX_HOST, AWX_USERNAME, awx_password)

    # 0. 接続確認
    ping = client.get("/ping/")
    print(f"  ✓ AWX 接続確認 OK (version: {ping.get('version', '?')})\n")

    # 1. Organization
    print("▶ Organization 確認")
    org = find_or_none(client, "/organizations/", AWX_ORG)
    if not org:
        print(f"  エラー: Organization '{AWX_ORG}' が見つかりません", file=sys.stderr)
        sys.exit(1)
    org_id = org["id"]
    print(f"  ✓ '{AWX_ORG}' (ID={org_id})\n")

    # 2. Project
    print("▶ Project 作成")
    project = ensure("Project", client, "/projects/", PROJECT_NAME, {
        "name": PROJECT_NAME,
        "organization": org_id,
        "scm_type": "git",
        "scm_url": SCM_URL,
        "scm_branch": SCM_BRANCH,
        "scm_update_on_launch": True,
        "scm_clean": True,
        "description": "awx-sub2api リポジトリ",
    })
    project_id = project["id"]

    # SCM 同期完了を待機（新規作成時のみ）
    print("  ⏳ SCM 同期を待機中...", end="", flush=True)
    for _ in range(30):
        status = client.get(f"/projects/{project_id}/")["status"]
        if status in ("successful", "failed", "canceled"):
            break
        print(".", end="", flush=True)
        time.sleep(5)
    print(f" {status}")
    if status != "successful":
        print(f"  エラー: SCM 同期失敗 ({status})", file=sys.stderr)
        sys.exit(1)
    print()

    # 3. Credential
    print("▶ Machine Credential 作成")
    cred_types = client.get("/credential_types/?name=Machine")
    cred_type_id = cred_types["results"][0]["id"]
    credential = ensure("Credential", client, "/credentials/", CRED_NAME, {
        "name": CRED_NAME,
        "organization": org_id,
        "credential_type": cred_type_id,
        "inputs": {
            "username": "opc",
            "ssh_key_data": ssh_private_key,
            "become_method": "sudo",
        },
    })
    cred_id = credential["id"]
    print()

    # 4. Inventory
    print("▶ Inventory 作成")
    inventory = ensure("Inventory", client, "/inventories/", INV_NAME, {
        "name": INV_NAME,
        "organization": org_id,
        "description": "sub2api 配布対象インベントリ",
        "variables": INV_VARS,
    })
    inv_id = inventory["id"]
    client._req("PATCH", f"/inventories/{inv_id}/", {"variables": INV_VARS})
    print("  ↻ Inventory 変数を更新")
    print()

    # 5. グループ・ホスト登録
    print("▶ グループ・ホスト登録")
    for group_def in INVENTORY_GROUPS:
        gname = group_def["name"]
        group_path = f"/groups/?inventory={inv_id}&name={urllib.parse.quote(gname)}"
        group = find_in_results(client, group_path)
        if not group:
            s, j = client._req("POST", "/groups/", {"name": gname, "inventory": inv_id})
            group = j if s in (200, 201) and j.get("id") else find_in_results(client, group_path)
            if not group:
                raise RuntimeError(f"グループ '{gname}' の作成に失敗: {s} {j}")
            print(f"  ✨ グループ '{gname}' を作成 (ID={group['id']})")
        else:
            print(f"  ✓ グループ '{gname}' は既存 (ID={group['id']})")
        group_id = group["id"]

        for host_def in group_def["hosts"]:
            hname = host_def["name"]
            # yaml.dump でシリアライズすることで、スペースを含む値（SSH オプション等）を
            # 正しくクォートした YAML 文字列として AWX API に渡す
            hvars = yaml.dump(host_def["vars"], default_flow_style=False, allow_unicode=True)

            host_path = f"/hosts/?inventory={inv_id}&name={urllib.parse.quote(hname)}"
            host = find_in_results(client, host_path)
            if not host:
                s, j = client._req("POST", "/hosts/", {
                    "name": hname,
                    "inventory": inv_id,
                    "variables": hvars,
                })
                host = j if s in (200, 201) and j.get("id") else find_in_results(client, host_path)
                if not host:
                    raise RuntimeError(f"ホスト '{hname}' の作成に失敗: {s} {j}")
                print(f"    ✨ ホスト '{hname}' を作成 (ID={host['id']})")
            else:
                print(f"    ✓ ホスト '{hname}' は既存 (ID={host['id']})")
                client._req("PATCH", f"/hosts/{host['id']}/", {"variables": hvars})

            host_id = host["id"]
            # グループに追加（既に所属済みの場合は無視）
            s, j = client._req("POST", f"/groups/{group_id}/hosts/", {"id": host_id}, ok=(200, 201, 204, 400))
    print()

    # 6. Job Template 作成
    print("▶ Job Template 作成")
    for jt_def in JOB_TEMPLATES:
        jt = find_or_none(client, "/job_templates/", jt_def["name"])
        if jt:
            print(f"  ✓ '{jt_def['name']}' は既存 (ID={jt['id']})")
            jt_id = jt["id"]
        else:
            jt_body = {
                "name": jt_def["name"],
                "job_type": "run",
                "inventory": inv_id,
                "project": project_id,
                "playbook": jt_def["playbook"],
                "limit": jt_def["limit"],
                "become_enabled": True,
                "description": jt_def["description"],
            }
            if jt_def.get("ask_variables_on_launch"):
                jt_body["ask_variables_on_launch"] = True
            jt = client.post("/job_templates/", jt_body)
            jt_id = jt["id"]
            print(f"  ✨ '{jt_def['name']}' を作成 (ID={jt_id})")

        # Credential を紐付け
        client._req("POST", f"/job_templates/{jt_id}/credentials/", {"id": cred_id})

        # Survey Spec を追加 (deploy-k8s 用)
        if jt_def["name"] == "deploy-k8s":
            survey_body = {
                "name": "Target Host Configuration",
                "description": "Specify target hosts or groups.",
                "spec": [
                    {
                        "question_name": "配布先ホストまたはグループ名",
                        "question_description": "Kubernetes をデプロイする対象グループ名（例: sub2api_targets, awx_controller）または個別のホスト名を入力してください。",
                        "variable": "target_hosts",
                        "type": "text",
                        "default": "sub2api_targets",
                        "required": True
                    }
                ]
            }
            client._req("POST", f"/job_templates/{jt_id}/survey_spec/", survey_body)
            client._req("PATCH", f"/job_templates/{jt_id}/", {"survey_enabled": True})
            print(f"    ✓ '{jt_def['name']}' の Survey (サーベイ) を設定しました")
    print()

    # 完了
    print("=" * 40)
    print(" セットアップ完了！")
    print("=" * 40)
    print(f"  Project    : {PROJECT_NAME} (ID={project_id})")
    print(f"  Credential : {CRED_NAME} (ID={cred_id})")
    print(f"  Inventory  : {INV_NAME} (ID={inv_id})")
    print(f"\n  AWX Web UI : {AWX_HOST}/#/templates")
    print("\n  次のステップ:")
    print("    1. 'build-awx-controller' を起動")
    print("    2. 完了後、'deploy-sub2api' を起動")


if __name__ == "__main__":
    main()
