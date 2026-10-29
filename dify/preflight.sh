#!/usr/bin/env bash
# Refuse to start with the placeholders copied from env.example.
set -euo pipefail

env_file="${1:-.env}"
if [[ ! -f "$env_file" ]]; then
  echo "missing $env_file" >&2
  exit 2
fi

failed=0
for name in INIT_PASSWORD OCEANBASE_VECTOR_PASSWORD DIFY_AGENT_LOCAL_SANDBOX_AUTH_TOKEN INN_SERVICE_TOKEN INN_ADMIN_TOKEN; do
  value="$(grep -E "^${name}=" "$env_file" | tail -n 1 | cut -d= -f2- || true)"
  if [[ -z "$value" || "$value" =~ (CHANGE_ME|CHANGEME|CHANGE-ME|ChangeMe|change-me|placeholder|PLACEHOLDER) ]]; then
    echo "refuse placeholder or empty $name in $env_file" >&2
    failed=1
  fi
done

exit "$failed"
