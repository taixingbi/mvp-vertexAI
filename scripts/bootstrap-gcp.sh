#!/usr/bin/env bash
# One-time GCP bootstrap. Run as a project Owner (not the GitHub deploy SA).
#
#   export PROJECT_ID=kaden-api
#   export DEPLOY_SA=vertex-ai-map@kaden-api.iam.gserviceaccount.com
#   bash scripts/bootstrap-gcp.sh
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?set PROJECT_ID}"
LOCATION="${LOCATION:-us-central1}"
AR_REPO="${AR_REPO:-mvp-vertexai}"
RUNTIME_SA="${RUNTIME_SA:-vertex-gateway@${PROJECT_ID}.iam.gserviceaccount.com}"
DEPLOY_SA="${DEPLOY_SA:-vertex-ai-map@${PROJECT_ID}.iam.gserviceaccount.com}"

echo "Project:   ${PROJECT_ID}"
echo "Region:    ${LOCATION}"
echo "AR repo:   ${AR_REPO}"
echo "Runtime:   ${RUNTIME_SA}"
echo "Deploy SA: ${DEPLOY_SA}"

gcloud services enable \
  cloudresourcemanager.googleapis.com \
  serviceusage.googleapis.com \
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

RUNTIME_NAME="${RUNTIME_SA%%@*}"
if ! gcloud iam service-accounts describe "${RUNTIME_SA}" \
  --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${RUNTIME_NAME}" \
    --display-name="Vertex gateway Cloud Run" \
    --project="${PROJECT_ID}"
fi

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/aiplatform.user" \
  --condition=None >/dev/null

for ROLE in \
  roles/run.admin \
  roles/artifactregistry.admin \
  roles/cloudbuild.builds.editor \
  roles/iam.serviceAccountUser \
  roles/storage.admin
do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${DEPLOY_SA}" \
    --role="${ROLE}" \
    --condition=None >/dev/null
  echo "Granted ${ROLE} to ${DEPLOY_SA}"
done

gcloud iam service-accounts add-iam-policy-binding "${RUNTIME_SA}" \
  --member="serviceAccount:${DEPLOY_SA}" \
  --role="roles/iam.serviceAccountUser" \
  --project="${PROJECT_ID}" >/dev/null

echo
echo "Bootstrap done. Re-run GitHub Actions → Deploy."
