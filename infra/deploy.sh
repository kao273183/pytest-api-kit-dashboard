#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Deploy the dashboard CloudFormation stack + upload real Lambda code.
#
# Required env vars (export or put in a .envrc):
#   STACK_NAME              CloudFormation stack name (default: api-test-dashboard)
#   AWS_REGION              (default: us-east-1)
#   PROJECT_NAME            Resource prefix (default: api-test-dashboard)
#   GITHUB_OWNER            GitHub org/user that owns the workflow repos
#   ALLOWED_REPOS           e.g. "api-tests,ui-tests"
#   WORKFLOW_MAP_JSON       e.g. '{"api-tests":"smoke.yml","ui-tests":"e2e.yml"}'
#   GITHUB_PAT_SECRET_ARN   Secrets Manager ARN of {"token":"ghp_..."}
#   COGNITO_DOMAIN_PREFIX   globally unique; forms <prefix>.auth.<region>.amazoncognito.com
#   DASHBOARD_ROOT          e.g. https://d1abc.cloudfront.net/
#   DASHBOARD_ORIGIN        scheme+host, no trailing slash
#   CALLBACK_URL            trigger panel URL
#   ADMIN_EMAILS            comma-separated list
#   VPC_ID                  VPC the Lambda attaches to (stable NAT egress)
#   LAMBDA_SUBNET_IDS       comma-separated private subnet IDs
#   SLACK_WEBHOOK_URL       (optional) notify on new user creation
#
# Optional:
#   WORKFLOW_INPUTS_MAP_JSON  per-repo allowed input keys; default "{}" = pass-through
# ---------------------------------------------------------------------------
set -euo pipefail

STACK_NAME="${STACK_NAME:-api-test-dashboard}"
REGION="${AWS_REGION:-us-east-1}"
PROJECT_NAME="${PROJECT_NAME:-api-test-dashboard}"

: "${GITHUB_OWNER:?set GITHUB_OWNER}"
: "${ALLOWED_REPOS:?set ALLOWED_REPOS (comma-separated)}"
: "${WORKFLOW_MAP_JSON:?set WORKFLOW_MAP_JSON}"
: "${GITHUB_PAT_SECRET_ARN:?set GITHUB_PAT_SECRET_ARN}"
: "${COGNITO_DOMAIN_PREFIX:?set COGNITO_DOMAIN_PREFIX}"
: "${DASHBOARD_ROOT:?set DASHBOARD_ROOT (e.g. https://d1abc.cloudfront.net/)}"
: "${DASHBOARD_ORIGIN:?set DASHBOARD_ORIGIN (scheme+host, no trailing slash)}"
: "${CALLBACK_URL:?set CALLBACK_URL}"
: "${ADMIN_EMAILS:?set ADMIN_EMAILS (comma-separated)}"
: "${VPC_ID:?set VPC_ID}"
: "${LAMBDA_SUBNET_IDS:?set LAMBDA_SUBNET_IDS (comma-separated)}"

WORKFLOW_INPUTS_MAP_JSON="${WORKFLOW_INPUTS_MAP_JSON:-{}}"
SLACK_WEBHOOK_URL="${SLACK_WEBHOOK_URL:-}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LAMBDA_DIR="$ROOT_DIR/lambda"
BUILD_DIR="$(mktemp -d)"
ZIP_PATH="$BUILD_DIR/trigger.zip"

echo "==> Packaging Lambda"
( cd "$LAMBDA_DIR" && zip -qr "$ZIP_PATH" trigger_test.py )

echo "==> Deploying CloudFormation stack: $STACK_NAME"
aws cloudformation deploy \
  --region "$REGION" \
  --stack-name "$STACK_NAME" \
  --template-file "$SCRIPT_DIR/cloudformation.yaml" \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
      ProjectName="$PROJECT_NAME" \
      GithubOwner="$GITHUB_OWNER" \
      AllowedRepos="$ALLOWED_REPOS" \
      WorkflowMapJson="$WORKFLOW_MAP_JSON" \
      WorkflowInputsMapJson="$WORKFLOW_INPUTS_MAP_JSON" \
      GithubPatSecretArn="$GITHUB_PAT_SECRET_ARN" \
      CognitoDomainPrefix="$COGNITO_DOMAIN_PREFIX" \
      DashboardRoot="$DASHBOARD_ROOT" \
      DashboardOrigin="$DASHBOARD_ORIGIN" \
      CallbackUrl="$CALLBACK_URL" \
      AdminEmails="$ADMIN_EMAILS" \
      VpcId="$VPC_ID" \
      LambdaSubnetIds="$LAMBDA_SUBNET_IDS" \
      SlackWebhookUrl="$SLACK_WEBHOOK_URL"

FN_NAME="$(aws cloudformation describe-stack-resources \
  --region "$REGION" --stack-name "$STACK_NAME" \
  --query "StackResources[?LogicalResourceId=='TriggerFunction'].PhysicalResourceId" \
  --output text)"

echo "==> Uploading real Lambda code to $FN_NAME"
aws lambda update-function-code \
  --region "$REGION" \
  --function-name "$FN_NAME" \
  --zip-file "fileb://$ZIP_PATH" >/dev/null

echo ""
echo "==> Stack outputs"
aws cloudformation describe-stacks \
  --region "$REGION" --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs" --output table

cat <<EOF

==> Next
  1) Open frontend/trigger-panel.html and fill in CONFIG from the outputs above:
       apiEndpoint      -> ApiEndpoint
       cognitoClientId  -> UserPoolClientId
       cognitoDomain    -> CognitoHostedUiDomain (full hostname)
  2) Upload the modified HTML to your dashboard S3 bucket:
       aws s3 cp frontend/trigger-panel.html s3://<bucket>/trigger-panel.html
  3) Create the first admin user with ./infra/add-user.sh <email>
EOF
