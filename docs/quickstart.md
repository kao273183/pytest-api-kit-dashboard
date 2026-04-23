# Quickstart — 45 minutes end-to-end

## Prerequisites

- AWS account with **admin-level credentials** (CloudFormation needs to create IAM roles)
- Your pytest repo(s) already on GitHub with workflows that accept `workflow_dispatch`
- An existing **S3 bucket + CloudFront distribution** hosting your dashboard HTML
  (if you don't have one yet, deploy [`pytest-api-kit-aws`](https://github.com/kao273183/pytest-api-kit-aws) first — it creates both)
- A VPC with private subnets that have NAT Gateway egress (Lambda needs this
  for a stable outbound IP)

## 1. Create a GitHub fine-grained PAT (5 min)

1. GitHub → Settings → Developer settings → Personal access tokens → **Fine-grained tokens**
2. Click **Generate new token**:
   - **Resource owner**: your org (or yourself)
   - **Repository access**: Only select repositories → pick the repos your dashboard can trigger
   - **Permissions** → **Repository permissions**:
     - Contents: **Read**
     - Actions: **Read and write**
     - Metadata: **Read** (auto-selected)
   - **Expiration**: 90 days (set a calendar reminder to rotate)
3. Copy the token (`ghp_...`)

## 2. Store the PAT in Secrets Manager (1 min)

```bash
aws secretsmanager create-secret \
  --name api-test-dashboard/github-pat \
  --secret-string '{"token":"ghp_xxx"}'
```

Copy the returned **ARN**. You'll pass it to `deploy.sh` next.

## 3. Deploy the stack (5 min)

Set env vars (put in `.envrc` or paste into shell):

```bash
export STACK_NAME=api-test-dashboard
export AWS_REGION=us-east-1
export PROJECT_NAME=api-test-dashboard

# GitHub config
export GITHUB_OWNER=acme-org
export ALLOWED_REPOS=api-tests,ui-tests
export WORKFLOW_MAP_JSON='{"api-tests":"smoke.yml","ui-tests":"e2e.yml"}'
export GITHUB_PAT_SECRET_ARN=arn:aws:secretsmanager:us-east-1:1234567890:secret:api-test-dashboard/github-pat-XXXXXX

# Dashboard hosting (from your existing CloudFront distribution)
export DASHBOARD_ROOT=https://d1abc.cloudfront.net/
export DASHBOARD_ORIGIN=https://d1abc.cloudfront.net
export CALLBACK_URL=https://d1abc.cloudfront.net/trigger-panel.html

# Cognito
export COGNITO_DOMAIN_PREFIX=acme-api-dashboard   # must be globally unique
export ADMIN_EMAILS=you@acme.com,alice@acme.com

# Network (Lambda attaches here for stable NAT egress IP)
export VPC_ID=vpc-0123456789abcdef
export LAMBDA_SUBNET_IDS=subnet-aaa,subnet-bbb,subnet-ccc

# Optional Slack webhook
export SLACK_WEBHOOK_URL='https://hooks.slack.com/services/...'
```

Then deploy:

```bash
./infra/deploy.sh
```

It prints a table of stack outputs at the end. Copy these three:

```
ApiEndpoint             : https://xxxxxxxxxx.execute-api.us-east-1.amazonaws.com
UserPoolClientId        : abcd1234efgh5678
CognitoHostedUiDomain   : acme-api-dashboard.auth.us-east-1.amazoncognito.com
```

## 4. Configure the frontend (3 min)

Edit `frontend/trigger-panel.html`, find the `CONFIG` block near line 720:

```js
const CONFIG = {
  apiEndpoint:        "REPLACE_ME_WITH_API_ENDPOINT",     ← paste ApiEndpoint
  cognitoDomain:      "REPLACE_ME_WITH_COGNITO_DOMAIN",    ← paste CognitoHostedUiDomain
  cognitoClientId:    "REPLACE_ME_WITH_CLIENT_ID",         ← paste UserPoolClientId
  redirectUri:        window.location.origin + window.location.pathname,
};
```

Also find these two placeholders (search for `MAIN_REPO` and `SECONDARY_REPO`):

```js
let currentRepo = "MAIN_REPO";
```

Change to your actual repo names matching `ALLOWED_REPOS`. The tabs in the UI
switch between them.

## 5. Upload + invalidate (2 min)

```bash
aws s3 cp frontend/trigger-panel.html s3://your-bucket/trigger-panel.html

# Optional but recommended — bust CloudFront cache so users see the new version
aws cloudfront create-invalidation \
  --distribution-id <YOUR_DIST_ID> \
  --paths "/trigger-panel.html"
```

## 6. Create your first admin (1 min)

Grab the `UserPoolId` from the stack outputs. Then:

```bash
export POOL_ID=us-east-1_AbCdEfGhI
export DASHBOARD_URL=https://d1abc.cloudfront.net/trigger-panel.html

./infra/add-user.sh you@acme.com '' admin
```

This prints credentials. Open the dashboard URL, sign in with those, and
you're live.

## 7. (Optional) Corporate SSO

The Cognito User Pool is ready to accept SAML or OIDC identity providers
(Azure AD, Google Workspace, Okta). Once IT provides the metadata:

1. Cognito console → **Sign-in experience** → **Federated identity providers** → Add
2. **App client** → **Hosted UI** → enable the new IdP
3. No CloudFormation change needed — IdP config lives in the console

After that, users authenticate with their corporate account and Cognito
issues the JWT the dashboard expects.

---

## Troubleshooting

### Login succeeds but every button says "403 Forbidden"

Your user is in no role group. Add them:

```bash
aws cognito-idp admin-add-user-to-group \
  --user-pool-id "$POOL_ID" \
  --username user@acme.com \
  --group-name tester
```

Or log in as admin and use the dashboard's user-management page.

### "Invalid redirect URI" on Cognito Hosted UI

The URL in the browser must **exactly match** a `CallbackURLs` entry in the
User Pool Client. Check `CALLBACK_URL`, `DASHBOARD_ROOT`, and the
corresponding index.html path were all set before deploy.

### Trigger button shows `network error`

Check browser DevTools Network tab for the exact response. Common causes:
- **CORS error**: `DASHBOARD_ORIGIN` env var doesn't match the browser URL
  scheme+host. Re-deploy with the correct value.
- **502 from API Gateway**: Lambda exception — check CloudWatch Logs group
  `/aws/lambda/<PROJECT_NAME>-trigger`
- **JWT expired**: sign out + back in (the HTML checks token expiry but may
  race on slow networks)

### GitHub dispatch returns 404

Either:
- `repo` isn't in `ALLOWED_REPOS`
- `workflow` filename doesn't match the real filename in `.github/workflows/`
- The PAT doesn't have access to the target repo

Check CloudWatch Logs — the audit line shows what was dispatched.

### "Cannot delete yourself"

Hard-coded safety. Have another admin delete you.

---

## Auditing

- **CloudWatch Logs** (`/aws/lambda/<PROJECT_NAME>-trigger`) — one line per
  dispatch or user-mgmt action, with the caller's email from the JWT claim
- **GitHub Actions** run page — shows the PAT identity as the triggering actor;
  the real human identity is in CloudWatch
- **CloudTrail** — every `secretsmanager:GetSecretValue` call

## Updating

```bash
# Pull latest, re-deploy
git pull
./infra/deploy.sh
```

The script re-zips the Lambda and calls `update-function-code`. CloudFormation
only applies what actually changed.

## Tearing down

```bash
aws cloudformation delete-stack --stack-name "$STACK_NAME" --region "$AWS_REGION"
aws secretsmanager delete-secret \
  --secret-id api-test-dashboard/github-pat \
  --force-delete-without-recovery
```

This removes the Lambda, API Gateway, Cognito pool, IAM roles, and log group.
It does **not** touch your S3 bucket or CloudFront distribution.
