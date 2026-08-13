#!/usr/bin/env bash
# Smoke-test llama + gpt-oss (sync + stream).
# Usage:
#   export GATEWAY_URL='https://....run.app'
#   export API_KEY='...'
#   bash scripts/smoke.sh
set -euo pipefail

GATEWAY_URL="${GATEWAY_URL:?set GATEWAY_URL}"
API_KEY="${API_KEY:?set API_KEY}"
GATEWAY_URL="${GATEWAY_URL%/}"

chat() {
  local model="$1"
  local stream="${2:-false}"
  local body
  body="$(jq -nc \
    --arg model "${model}" \
    --argjson stream "${stream}" \
    '{
      model: $model,
      messages: [{role: "user", content: "Say hello in one short sentence."}],
      max_tokens: 64,
      temperature: 0,
      stream: $stream
    }')"
  echo "=== ${model} stream=${stream} ==="
  if [[ "${stream}" == "true" ]]; then
    curl -sS -N -X POST "${GATEWAY_URL}/v1/chat/completions" \
      -H "Content-Type: application/json" \
      -H "Authorization: Bearer ${API_KEY}" \
      -d "${body}"
  else
    curl -sS -X POST "${GATEWAY_URL}/v1/chat/completions" \
      -H "Content-Type: application/json" \
      -H "Authorization: Bearer ${API_KEY}" \
      -d "${body}" \
      | jq '{error, detail, model, answer: .choices[0].message.content, usage}'
  fi
  echo
}

echo "health: $(curl -sS "${GATEWAY_URL}/health")"
echo "version: $(curl -sS "${GATEWAY_URL}/version")"
echo

for MODEL in llama gpt-oss; do
  chat "${MODEL}" false
  chat "${MODEL}" true
done
