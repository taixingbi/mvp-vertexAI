# mvp-vertexAI

Cloud Run FastAPI gateway over **Vertex AI Model Garden MaaS**, with an OpenAI-compatible chat API aligned to [mvp-bedrock](../mvp-bedrock) for multi-cloud benchmarks.

**v0.1:** `/v1/chat/completions` + Llama 3.3 70B + GPT-OSS 20B + sync/stream + usage.

## Endpoints

| Method | Path | Auth |
| --- | --- | --- |
| `POST` | `/v1/chat/completions` | `Authorization: Bearer <API_KEY>` or `x-api-key` |
| `GET` | `/health` | none |
| `GET` | `/version` | none |

### Model aliases

| Request `model` | Vertex MaaS ID | Region |
| --- | --- | --- |
| `llama` | `meta/llama-3.3-70b-instruct-maas` | `us-central1` |
| `gpt-oss` / `gpt-oss-20b` | `openai/gpt-oss-20b-maas` | `us-central1` |

Note: Bedrock alias `gpt-oss` maps to **120B**; this Vertex MVP maps `gpt-oss` to **20B**.

### Example

```bash
curl -sS -X POST "${GATEWAY_URL}/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${API_KEY}" \
  -d '{
    "model": "llama",
    "messages": [{"role": "user", "content": "Say hello in one short sentence."}],
    "max_tokens": 64,
    "temperature": 0,
    "stream": false
  }'
```

## Secrets & config

Use **`API_KEY`** everywhere (GitHub secret, Cloud Run env, local, smoke). Do not commit keys.

GitHub → **Settings → Secrets and variables → Actions**:

**Repository secrets**

| Name | Purpose |
| --- | --- |
| `API_KEY` | Client auth (`Bearer` / `x-api-key`) |
| `GCP_SA_KEY` | Deploy service-account JSON (Cloud Build + Artifact Registry + Cloud Run) |

**Repository variables**

| Name | Example | Purpose |
| --- | --- | --- |
| `GCP_PROJECT_ID` | `my-vertex-mvp` | GCP project |
| `GCP_REGION` | `us-central1` | Cloud Run + MaaS region |
| `GCP_AR_REPO` | `mvp-vertexai` | Artifact Registry repo (optional) |
| `RUNTIME_SA` | `vertex-gateway@PROJECT.iam.gserviceaccount.com` | Cloud Run runtime SA (optional) |

The runtime SA needs `roles/aiplatform.user`. The deploy SA (JSON in `GCP_SA_KEY`) needs permission to build, push, and deploy Cloud Run **as** the runtime SA (`roles/run.admin`, Artifact Registry writer, Cloud Build, `iam.serviceAccountUser` on the runtime SA).

Optional: `MODEL_MAP` JSON to add aliases; `MODEL_ID` default alias (default `llama`).

## GCP setup

Do this once with a project Owner (not the GitHub deploy SA):

1. Create a GCP project and enable billing.
2. Enable APIs: `cloudresourcemanager.googleapis.com`, `run.googleapis.com`, `artifactregistry.googleapis.com`, `aiplatform.googleapis.com`, `cloudbuild.googleapis.com`.
3. In Model Garden, **Enable** Llama 3.3 70B (MaaS) and GPT-OSS 20B (MaaS).
4. Create Artifact Registry Docker repo `mvp-vertexai` in `us-central1`.
5. Create runtime SA `vertex-gateway@PROJECT.iam.gserviceaccount.com` and grant `roles/aiplatform.user`.
6. Grant the deploy SA (`GCP_SA_KEY`) Cloud Run / Artifact Registry / Cloud Build plus `iam.serviceAccountUser` on the runtime SA.
7. Push `main` or run **Actions → Deploy**. GitHub Actions does **not** enable APIs or create IAM.

First-time local bootstrap (Owner account):

```bash
export ENABLE_APIS=1 CREATE_AR=1 CREATE_SA=1
./deploy.sh
```

## GitHub Actions

Push to `main` or **workflow_dispatch** runs [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml): auth with `GCP_SA_KEY`, then `./deploy.sh`.

## Deploy

```bash
export PROJECT_ID='your-gcp-project'
export API_KEY='your-shared-secret'
export LOCATION='us-central1'

chmod +x deploy.sh scripts/smoke.sh
./deploy.sh
```

App-layer auth still requires `API_KEY` even if Cloud Run allows unauthenticated invoke.

## Local run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export API_KEY=local-dev-key
export PROJECT_ID='your-gcp-project'
export LOCATION=us-central1
gcloud auth application-default login

uvicorn app.main:app --host 127.0.0.1 --port 8080
```

## Smoke & benchmark

```bash
export GATEWAY_URL='https://....run.app'
export API_KEY='...'

bash scripts/smoke.sh
# chat llama false / true
# chat gpt-oss false / true

python scripts/benchmark.py \
  --model llama \
  --concurrency 8 \
  --requests 32 \
  --max-tokens 64 \
  --stream
```

## Layout

```
app/
  main.py
  models.py
  vertex_client.py
  auth.py
scripts/
  smoke.sh
  benchmark.py
Dockerfile
requirements.txt
deploy.sh
README.md
```

## Out of scope (v0.1)

Terraform, GKE, vLLM, Redis, DB, observability stack, router, RAG, Gemini.
