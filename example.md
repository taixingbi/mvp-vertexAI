#!/usr/bin/env bash
# Smoke-test Vertex gateway (sync + stream).
# Usage:
#   export API_KEY='your-shared-secret'
#   bash example.md
set -euo pipefail

export GATEWAY_URL="${GATEWAY_URL:-https://mvp-vertexai-sbeecmlmza-uc.a.run.app}"
export API_KEY="1234"
GATEWAY_URL="${GATEWAY_URL%/}"

curl -sS "${GATEWAY_URL}/health"
echo
curl -sS "${GATEWAY_URL}/version"
echo
echo

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
      max_tokens: 256,
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

# Alias → Vertex MaaS ID (us-central1):
#   llama     meta/llama-3.3-70b-instruct-maas
#   gpt-oss   openai/gpt-oss-20b-maas

chat llama false
chat llama true
chat gpt-oss false
chat gpt-oss true
