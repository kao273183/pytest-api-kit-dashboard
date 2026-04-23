# pytest-api-kit-dashboard

> Cognito-gated self-service trigger panel for `pytest-api-kit` test runs.
> Non-engineers (PMs, QA, managers) sign in with SSO, click a button, watch a
> GitHub Actions workflow dispatch + tail the live log — no GitHub access
> required.

**Companion to [`pytest-api-kit`](https://github.com/kao273183/pytest-api-kit) and [`pytest-api-kit-aws`](https://github.com/kao273183/pytest-api-kit-aws).**
Install order recommended: kit → aws → dashboard.

---

## What you get

- **Cognito User Pool** with three built-in roles (`admin` / `tester` / `viewer`)
- **API Gateway HTTP API** with JWT authorizer — zero custom auth code
- **Lambda function** bridging dashboard clicks to GitHub Actions `workflow_dispatch`
- **Web panel** (single-file HTML) — login, trigger test, watch live log, manage users, all in one page
- **Slack integration** — notify admins when a new user is created

Admin delegates: create a user, pick a role, Lambda:
1. Generates a strong temporary password
2. Creates the Cognito user + adds them to the role group
3. Sets the password permanent (skips `FORCE_CHANGE_PASSWORD` state which has
   known bugs with the Hosted UI)
4. Posts the credentials to Slack so the admin can DM the user

---

## Architecture

```
Browser (trigger-panel.html on S3/CloudFront)
  └─ Cognito Hosted UI (implicit flow)
       └─ id_token (JWT)
            └─ API Gateway (HTTP API, JWT authorizer)
                 └─ Lambda (trigger_test.py)
                      ├─ Secrets Manager (GitHub fine-grained PAT)
                      ├─ GitHub API (workflow_dispatch)
                      └─ Cognito admin API (user management)
                           └─ Slack webhook (new-user notify)
```

---

## Quickstart (45 minutes)

Full walkthrough: [docs/quickstart.md](docs/quickstart.md)

```bash
# 1. Create GitHub fine-grained PAT + save to Secrets Manager
aws secretsmanager create-secret \
  --name api-test-dashboard/github-pat \
  --secret-string '{"token":"ghp_xxx"}'

# 2. Deploy stack (takes ~3 min)
export GITHUB_OWNER=acme-org
export ALLOWED_REPOS=api-tests,ui-tests
export WORKFLOW_MAP_JSON='{"api-tests":"smoke.yml","ui-tests":"e2e.yml"}'
export GITHUB_PAT_SECRET_ARN=arn:aws:secretsmanager:...:secret:api-test-dashboard/github-pat-XXXXXX
export COGNITO_DOMAIN_PREFIX=acme-api-dashboard
export DASHBOARD_ROOT=https://d1abc.cloudfront.net/
export DASHBOARD_ORIGIN=https://d1abc.cloudfront.net
export CALLBACK_URL=https://d1abc.cloudfront.net/trigger-panel.html
export ADMIN_EMAILS=you@acme.com
export VPC_ID=vpc-xxxx
export LAMBDA_SUBNET_IDS=subnet-aaa,subnet-bbb
export AWS_REGION=us-east-1

./infra/deploy.sh

# 3. Paste the 3 CONFIG values into frontend/trigger-panel.html then upload
aws s3 cp frontend/trigger-panel.html s3://your-bucket/trigger-panel.html

# 4. Create your first admin
POOL_ID=<from stack output> ./infra/add-user.sh you@acme.com '' admin
```

---

## Directory layout

```
pytest-api-kit-dashboard/
├── lambda/
│   ├── trigger_test.py        ← Lambda handler (routes + GitHub dispatch + user mgmt)
│   └── requirements.txt       ← empty — boto3 comes with runtime
├── infra/
│   ├── cloudformation.yaml    ← one-shot stack: Cognito + API GW + Lambda + IAM
│   ├── deploy.sh              ← package Lambda, deploy stack, print outputs
│   └── add-user.sh            ← admin util: `./add-user.sh alice@acme.com '' tester`
├── frontend/
│   └── trigger-panel.html     ← single-file web UI (HTML + CSS + vanilla JS)
└── docs/
    └── quickstart.md          ← 45-min end-to-end walkthrough
```

---

## Roles

| Role | Can trigger tests | Can view reports | Can manage users |
|---|---|---|---|
| `admin` | ✅ | ✅ | ✅ |
| `tester` | ✅ | ✅ | ❌ |
| `viewer` | ❌ | ✅ | ❌ |

The three Cognito groups are created by CloudFormation. Add users to a group
via `add-user.sh <email> '' <role>` or via the dashboard's built-in user
management page (admin only).

`ADMIN_EMAILS` env var is a **belt-and-braces** override — listed emails are
always treated as admin even if the `cognito:groups` claim is missing. Useful
for bootstrapping before you've added yourself to the `admin` group.

---

## Workflow input shapes

Different GitHub repos accept different `workflow_dispatch` inputs. The Lambda
supports two modes:

### Mode A — pass-through (default)

If `WORKFLOW_INPUTS_MAP` is empty (`"{}"`), the Lambda forwards every key of
the POST body to `workflow_dispatch.inputs`, except `repo` and `ref`. Works
great when all your workflows accept the same input shape.

### Mode B — per-repo allow-list

Set `WORKFLOW_INPUTS_MAP` to restrict which keys a repo accepts:

```json
{
  "api-tests":  ["environment", "pytest_args"],
  "ui-tests":   ["platform", "environment", "pytest_args"]
}
```

Now a POST to `/trigger` with `{"repo":"api-tests","platform":"ios","pytest_args":"-m smoke"}`
will **drop** `platform` (not in the `api-tests` list) before calling GitHub.

---

## Security model

- **No GitHub credentials in the browser** — only on Lambda, read from Secrets Manager
- **JWT audience = Cognito User Pool Client ID** enforced at API Gateway
- **JWT issuer = Cognito User Pool** enforced at API Gateway
- **Lambda in VPC private subnet** — stable NAT egress IP so you can allow-list
  the function in your GitHub org's IP allow list (if you have one)
- **Audit log**: every dispatch + user-mgmt action writes one line to CloudWatch
  Logs with the actor's email

---

## Not-in-this-repo

- **Actual test code** — see [`pytest-api-kit`](https://github.com/kao273183/pytest-api-kit)
- **ECS deployment** — see [`pytest-api-kit-aws`](https://github.com/kao273183/pytest-api-kit-aws)
- **Custom domain / ACM cert** for the dashboard — add to CloudFormation as needed

---

## Licence

MIT. Extracted from a production QA dashboard that's been onboarding
non-engineer testers for 2+ months without a single support ticket.
