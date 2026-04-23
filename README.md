# pytest-api-kit-dashboard

> Cognito-gated self-service trigger panel for `pytest-api-kit` test runs.
> Non-engineers (PMs, QA, managers) sign in with SSO, click a button, watch a
> GitHub Actions workflow dispatch + tail the live log — no GitHub access
> required.

**[繁體中文](#中文版)** | **English**

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
│   ├── index.html             ← landing page template (optional; see "Landing page" below)
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

## Landing page (optional)

`frontend/index.html` is a **template** you can upload to the CloudFront root
to give users a friendly entry point with two cards — "Trigger tests" and
"View reports".

👁 **[Live preview](https://kao273183.github.io/pytest-api-kit-dashboard/)** — the template rendered via GitHub Pages (no clone needed).

The trigger panel itself also renders standalone in demo mode:
👁 **[Live preview — trigger-panel.html](https://kao273183.github.io/pytest-api-kit-dashboard/trigger-panel.html)** (shows the UI; real auth requires your own Cognito setup).

Use it if **either** is true:

- You're **not** using [`pytest-api-kit-aws`](https://github.com/kao273183/pytest-api-kit-aws)
  (which writes its own `index.html` listing every historical run)
- You want a branded landing page between users and both the trigger panel + report index

```bash
# Edit links + brand name in frontend/index.html first
aws s3 cp frontend/index.html s3://your-bucket/index.html
aws cloudfront create-invalidation --distribution-id <DIST_ID> --paths "/index.html"
```

⚠ If you **do** use `pytest-api-kit-aws`, its `generate_index_html.py`
overwrites `s3://bucket/index.html` after every test run. Either:

1. **Pick one** — upload only the dashboard repo's `index.html` if you prefer
   a static landing page
2. **Or rename** — upload this file as `s3://bucket/home.html` and the
   trigger panel's Cognito callback still lands on the dynamic report index

## Not-in-this-repo

- **Actual test code** — see [`pytest-api-kit`](https://github.com/kao273183/pytest-api-kit)
- **ECS deployment** — see [`pytest-api-kit-aws`](https://github.com/kao273183/pytest-api-kit-aws)
- **Custom domain / ACM cert** for the dashboard — add to CloudFormation as needed

---

## Licence

MIT. Extracted from a production QA dashboard that's been onboarding
non-engineer testers for 2+ months without a single support ticket.

---

## 中文版

### 這是什麼

**`pytest-api-kit-dashboard`** 是給 [`pytest-api-kit`](https://github.com/kao273183/pytest-api-kit) 配的「自助觸發面板」。讓 **非工程同事**（PM、QA、主管）用公司 SSO 登入網頁，按一個按鈕就能觸發測試、看即時 log、看報告 — 完全不用 GitHub 帳號。

### 六個核心功能

- **Cognito User Pool** 內建三種角色：`admin` / `tester` / `viewer`
- **API Gateway HTTP API** 配 JWT authorizer — 零自訂 auth 程式碼
- **Lambda** 橋接網頁點擊 → GitHub Actions `workflow_dispatch`
- **單檔 HTML 網頁** — 登入、觸發、即時 log、使用者管理一頁搞定
- **Slack 通知** — 管理員新增使用者時自動把帳密發到 Slack
- **安全設計** — GitHub token 只在 Lambda 端 Secrets Manager，瀏覽器端拿不到；Lambda 綁定 VPC NAT egress IP，可加入公司 GitHub IP 白名單

### 架構

```
瀏覽器 (CloudFront trigger-panel.html)
  └─ Cognito Hosted UI (SSO)
       └─ id_token (JWT)
            └─ API Gateway (JWT authorizer)
                 └─ Lambda (trigger_test.py)
                      ├─ Secrets Manager (GitHub PAT)
                      ├─ GitHub API (workflow_dispatch)
                      └─ Cognito admin API (使用者管理)
                           └─ Slack webhook (新使用者通知)
```

### 快速開始（約 45 分鐘）

完整教學：[docs/quickstart.md](docs/quickstart.md)

```bash
# 1. 建 GitHub fine-grained PAT 存到 Secrets Manager
aws secretsmanager create-secret \
  --name api-test-dashboard/github-pat \
  --secret-string '{"token":"ghp_xxx"}'

# 2. 部署 stack（約 3 分鐘）
export GITHUB_OWNER=acme-org
export ALLOWED_REPOS=api-tests,ui-tests
export WORKFLOW_MAP_JSON='{"api-tests":"smoke.yml","ui-tests":"e2e.yml"}'
export GITHUB_PAT_SECRET_ARN=arn:aws:secretsmanager:...
export COGNITO_DOMAIN_PREFIX=acme-api-dashboard
# ...等（完整清單見 docs/quickstart.md）

./infra/deploy.sh

# 3. 把 stack output 的三個值貼進 frontend/trigger-panel.html 的 CONFIG，上傳 S3
aws s3 cp frontend/trigger-panel.html s3://your-bucket/trigger-panel.html

# 4. 建第一個管理員
POOL_ID=<從 stack output 取得> ./infra/add-user.sh you@acme.com '' admin
```

### 角色權限

| 角色 | 可觸發測試 | 可看報告 | 可管理使用者 |
|---|---|---|---|
| `admin` | ✅ | ✅ | ✅ |
| `tester` | ✅ | ✅ | ❌ |
| `viewer` | ❌ | ✅ | ❌ |

### 適用場景

- **QA 團隊有非工程的測試員** — 他們不需要學 Git / GitHub / 命令列
- **主管想自己跑 release smoke 看結果** — 比寫信叫 QA 跑快多了
- **公司 SSO (Azure AD / Google Workspace / Okta)** 要整合 — Cognito 只要在 console 加 IdP 就能接上，不用改 CloudFormation

### 線上預覽（不用 clone）

👁 **[範本 Landing 頁面預覽](https://kao273183.github.io/pytest-api-kit-dashboard/)** — GitHub Pages live

👁 **[觸發面板預覽](https://kao273183.github.io/pytest-api-kit-dashboard/trigger-panel.html)** — 展示 UI（不能真的登入，需要你自己部署 Cognito）

### 這裡沒有的東西

- **實際測試程式碼** — 見 [`pytest-api-kit`](https://github.com/kao273183/pytest-api-kit)
- **ECS 部署基礎設施** — 見 [`pytest-api-kit-aws`](https://github.com/kao273183/pytest-api-kit-aws)

### 授權

MIT。歡迎 fork 使用。
