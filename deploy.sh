#!/usr/bin/env bash
# Build and deploy mvp-vertexAI to Cloud Run.
#
# Required env:
#   PROJECT_ID   GCP project
#   API_KEY      shared client secret (Bearer / x-api-key)
#
# Optional env:
#   LOCATION          default us-central1
#   SERVICE_NAME      default mvp-vertexai
#   AR_REPO           default mvp-vertexai
#   RUNTIME_SA        default vertex-gateway@$PROJECT_ID.iam.gserviceaccount.com
#   CREATE_SA         set to 1 to create runtime SA + bind aiplatform.user
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?set PROJECT_ID}"
API_KEY="${API_KEY:?set API_KEY}"
LOCATION="${LOCATION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-mvp-vertexai}"
AR_REPO="${AR_REPO:-mvp-vertexai}"
RUNTIME_SA="${RUNTIME_SA:-vertex-gateway@${PROJECT_ID}.iam.gserviceaccount.com}"
IMAGE="${LOCATION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}/${SERVICE_NAME}:latest"

echo "Project:  ${PROJECT_ID}"
echo "Region:   ${LOCATION}"
echo "Service:  ${SERVICE_NAME}"
echo "Image:    ${IMAGE}"
echo "Runtime:  ${RUNTIME_SA}"

gcloud config set project "${PROJECT_ID}" >/dev/null

gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  aiplatform.googleapis.com \
  cloudbuild.googleapis.com \
  --project "${PROJECT_ID}"

if ! gcloud artifacts repositories describe "${AR_REPO}" \
  --location="${LOCATION}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud artifacts repositories create "${AR_REPO}" \
    --repository-format=docker \
    --location="${LOCATION}" \
    --description="mvp-vertexAI images" \
    --project="${PROJECT_ID}"
fi

if [[ "${CREATE_SA:-0}" == "1" ]]; then
  SA_NAME="${RUNTIME_SA%%@*}"
  if ! gcloud iam service-accounts describe "${RUNTIME_SA}" \
    --project="${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud iam service-accounts create "${SA_NAME}" \
      --display-name="Vertex gateway Cloud Run" \
      --project="${PROJECT_ID}"
  fi
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${RUNTIME_SA}" \
    --role="roles/aiplatform.user" \
    --condition=None >/dev/null
fi

gcloud builds submit --tag "${IMAGE}" --project "${PROJECT_ID}"

ENV_FILE="$(mktemp)"
{
  echo "PROJECT_ID: '${PROJECT_ID}'"
  echo "LOCATION: '${LOCATION}'"
  echo "API_KEY: '${API_KEY}'"
  if [[ -n "${MODEL_ID:-}" ]]; then
    echo "MODEL_ID: '${MODEL_ID}'"
  fi
} > "${ENV_FILE}"

gcloud run deploy "${SERVICE_NAME}" \
  --image "${IMAGE}" \
  --region "${LOCATION}" \
  --platform managed \
  --allow-unauthenticated \
  --service-account "${RUNTIME_SA}" \
  --env-vars-file "${ENV_FILE}" \
  --memory 512Mi \
  --cpu 1 \
  --timeout 120 \
  --max-instances 10 \
  --project "${PROJECT_ID}"

rm -f "${ENV_FILE}"

URL="$(gcloud run services describe "${SERVICE_NAME}" \
  --region "${LOCATION}" \
  --project "${PROJECT_ID}" \
  --format='value(status.url)')"

echo
echo "Deployed: ${URL}"
echo "Smoke:"
echo "  export GATEWAY_URL='${URL}'"
echo "  export API_KEY='***'"
echo "  bash scripts/smoke.sh"
