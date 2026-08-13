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
#   ENABLE_APIS       set to 1 to enable Cloud Run / AR / Vertex / Cloud Build APIs
#   CREATE_AR         default 1: create the Artifact Registry repo if missing
#   CREATE_SA         set to 1 to create runtime SA + bind aiplatform.user
#
# CI deploy SAs usually cannot enable APIs or mutate IAM. Enable those once
# with a project Owner, then let Actions only build and deploy.
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

if [[ "${ENABLE_APIS:-0}" == "1" ]]; then
  gcloud services enable \
    cloudresourcemanager.googleapis.com \
    run.googleapis.com \
    artifactregistry.googleapis.com \
    aiplatform.googleapis.com \
    cloudbuild.googleapis.com \
    --project "${PROJECT_ID}"
fi

if ! gcloud artifacts repositories describe "${AR_REPO}" \
  --location="${LOCATION}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  if [[ "${CREATE_AR:-1}" == "1" ]]; then
    echo "Creating Artifact Registry repo ${AR_REPO} in ${LOCATION}..."
    if ! gcloud artifacts repositories create "${AR_REPO}" \
      --repository-format=docker \
      --location="${LOCATION}" \
      --description="mvp-vertexAI images" \
      --project="${PROJECT_ID}"; then
      echo >&2
      echo "The GitHub deploy SA cannot create Artifact Registry repos." >&2
      echo "Run this once as a GCP Owner, then re-run Actions:" >&2
      echo >&2
      echo "  export PROJECT_ID=${PROJECT_ID}" >&2
      echo "  bash scripts/bootstrap-gcp.sh" >&2
      echo >&2
      echo "  member: vertex-ai-map@${PROJECT_ID}.iam.gserviceaccount.com" >&2
      echo "  role:   Artifact Registry Administrator" >&2
      echo "  plus:   Cloud Run Admin, Cloud Build Editor, Service Account User" >&2
      exit 1
    fi
  else
    echo "Artifact Registry repo '${AR_REPO}' not found in ${LOCATION}." >&2
    echo "Create it once as Owner: bash scripts/bootstrap-gcp.sh" >&2
    exit 1
  fi
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
