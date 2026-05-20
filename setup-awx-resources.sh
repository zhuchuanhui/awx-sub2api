#!/usr/bin/env bash
# AWX リソース初期セットアップスクリプト
# 使い方:
#   chmod +x setup-awx-resources.sh
#   ./setup-awx-resources.sh [SSH秘密鍵ファイルパス] [AWXパスワード]
#
# 引数を省略した場合は対話的に入力します。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLAYBOOK="${SCRIPT_DIR}/setup-awx-resources.yml"

# ── 引数 / 対話入力 ────────────────────────────────────────
SSH_KEY_FILE="${1:-}"
AWX_PASSWORD="${2:-}"

if [[ -z "${SSH_KEY_FILE}" ]]; then
  DEFAULT_KEY="${HOME}/.ssh/id_rsa"
  [[ -f "${HOME}/.ssh/id_ed25519" ]] && DEFAULT_KEY="${HOME}/.ssh/id_ed25519"
  read -rp "SSH 秘密鍵ファイルパス [${DEFAULT_KEY}]: " SSH_KEY_FILE
  SSH_KEY_FILE="${SSH_KEY_FILE:-${DEFAULT_KEY}}"
fi

if [[ -z "${AWX_PASSWORD}" ]]; then
  read -rsp "AWX admin パスワード: " AWX_PASSWORD
  echo
fi

# ── 事前チェック ────────────────────────────────────────────
if [[ ! -f "${SSH_KEY_FILE}" ]]; then
  echo "エラー: SSH 鍵ファイルが見つかりません: ${SSH_KEY_FILE}" >&2
  exit 1
fi

if ! command -v ansible-playbook &>/dev/null; then
  echo "エラー: ansible-playbook が見つかりません。Ansible をインストールしてください。" >&2
  exit 1
fi

# ── 一時ファイル作成（終了時に削除） ────────────────────────
VARS_TMP="$(mktemp /tmp/awx_setup_vars_XXXXXX.yml)"
CFG_TMP="$(mktemp /tmp/awx_ansible_XXXXXX.cfg)"
trap 'rm -f "${VARS_TMP}" "${CFG_TMP}"' EXIT

# SSH 秘密鍵（複数行）を YAML に安全に書き出す
python3 - "${SSH_KEY_FILE}" "${AWX_PASSWORD}" "${VARS_TMP}" <<'PYEOF'
import sys, yaml

ssh_key_path = sys.argv[1]
awx_password = sys.argv[2]
out_path     = sys.argv[3]

with open(ssh_key_path, 'r') as f:
    ssh_key = f.read()

data = {
    'awx_password': awx_password,
    'awx_ssh_private_key_content': ssh_key,
}

with open(out_path, 'w') as f:
    yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

print(f"変数ファイルを作成: {out_path}")
PYEOF

# ansible.cfg の become=True を無効化した一時設定ファイルを生成
# （localhost への URI/slurp モジュール実行で sudo が要求されるのを防ぐ）
cat > "${CFG_TMP}" <<EOF
[defaults]
inventory     = ${SCRIPT_DIR}/inventory/hosts.yml
roles_path    = ${SCRIPT_DIR}/roles
host_key_checking = False
retry_files_enabled = False
stdout_callback = yaml
interpreter_python = auto_silent

[privilege_escalation]
become      = False
become_method = sudo
become_ask_pass = False
EOF

# ── 実行 ────────────────────────────────────────────────────
echo ""
echo "=============================="
echo " AWX リソースセットアップ開始"
echo "=============================="
echo "  Playbook : ${PLAYBOOK}"
echo "  SSH key  : ${SSH_KEY_FILE}"
echo "  AWX host : http://140.83.58.183:30080"
echo ""

# ANSIBLE_CONFIG で一時 cfg（become=False）を使用
ANSIBLE_CONFIG="${CFG_TMP}" ansible-playbook "${PLAYBOOK}" \
  -e "@${VARS_TMP}"

echo ""
echo "セットアップ完了！"
echo "AWX Web UI: http://140.83.58.183:30080/#/templates"
