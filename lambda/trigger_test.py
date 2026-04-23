"""
Lambda handler — dashboard test trigger + user management.

Fronted by API Gateway with a Cognito JWT authorizer. Every request's caller
identity comes from the JWT claims already validated at the gateway layer.

Routes:
  POST   /trigger                    dispatch a GitHub Actions workflow
  GET    /runs?repo=...              list recent runs for a repo
  GET    /runs/{id}?repo=...         get a single run's status
  GET    /runs/{id}/logs?repo=...    tail of job logs
  GET    /admin/users                list Cognito users (admin only)
  POST   /admin/users                create user + set role (admin only)
  PUT    /admin/users/{email}/role   change a user's role (admin only)
  DELETE /admin/users/{email}        remove a user (admin only)

Env vars (set by CloudFormation):
  GITHUB_PAT_SECRET_ARN   Secrets Manager ARN holding { "token": "ghp_..." }
  GITHUB_OWNER            e.g. "acme-org"
  ALLOWED_REPOS           comma-separated whitelist, e.g. "api-tests,ui-tests"
  WORKFLOW_MAP            JSON {"api-tests":"smoke.yml","ui-tests":"e2e.yml"}
  WORKFLOW_INPUTS_MAP     JSON (optional) per-repo input shape.
                          Shape: {"<repo>": ["environment","pytest_args",...]}
                          When a repo is listed here, only those body keys are
                          forwarded as workflow_dispatch inputs. When missing,
                          the request body minus {repo, ref} is forwarded
                          verbatim — simplest common case.
  USER_POOL_ID            Cognito User Pool Id
  ADMIN_EMAILS            comma-separated emails always treated as admin
                          (even if their `cognito:groups` claim is missing)
  SLACK_WEBHOOK_URL       (optional) post a message when a new user is created
  DASHBOARD_URL           (optional) included in the Slack message
  PROJECT_NAME            (optional) shown in Slack message title
"""

import json
import os
import secrets as _pyrand
import string
import urllib.request
import urllib.error
import urllib.parse

import boto3

_secrets = boto3.client("secretsmanager")
_cognito = boto3.client("cognito-idp")
_cached_token = None


# ---------------------------------------------------------------------------
# GitHub API helpers
# ---------------------------------------------------------------------------

def _get_token() -> str:
    global _cached_token
    if _cached_token:
        return _cached_token
    arn = os.environ["GITHUB_PAT_SECRET_ARN"]
    raw = _secrets.get_secret_value(SecretId=arn)["SecretString"]
    _cached_token = json.loads(raw)["token"]
    return _cached_token


def _gh(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    url = f"https://api.github.com{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {_get_token()}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode() or "{}"
            return resp.status, (json.loads(text) if text.strip() else {})
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode(errors="replace")}


# ---------------------------------------------------------------------------
# Response + auth helpers
# ---------------------------------------------------------------------------

def _resp(status: int, payload: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "authorization,content-type",
            "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
        },
        "body": json.dumps(payload),
    }


def _actor(event: dict) -> str:
    claims = (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
    )
    return claims.get("email") or claims.get("cognito:username") or "unknown"


ROLES = ("admin", "tester", "viewer")


def _groups(event: dict) -> list[str]:
    claims = (
        event.get("requestContext", {}).get("authorizer", {})
        .get("jwt", {}).get("claims", {})
    )
    raw = claims.get("cognito:groups", "")
    if isinstance(raw, list):
        return [str(g).strip() for g in raw if g]
    s = str(raw).strip()
    if s.startswith("["):
        s = s.strip("[]")
    return [g.strip() for g in s.replace(" ", ",").split(",") if g.strip()]


def _is_admin(event: dict) -> bool:
    admins = [e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()]
    actor = _actor(event).lower()
    groups = _groups(event)
    return (actor in admins) or ("admin" in groups)


def _can_trigger(event: dict) -> bool:
    if _is_admin(event):
        return True
    return "tester" in _groups(event)


def _allowed_repo(repo: str) -> bool:
    return repo in [r.strip() for r in os.environ.get("ALLOWED_REPOS", "").split(",") if r.strip()]


def _workflow_for(repo: str) -> str | None:
    return json.loads(os.environ.get("WORKFLOW_MAP", "{}")).get(repo)


def _workflow_inputs(repo: str, body: dict) -> dict:
    """Shape the workflow_dispatch inputs for a repo.

    If WORKFLOW_INPUTS_MAP lists allowed keys for the repo, pick only those
    from the request body. Otherwise forward every field except the meta keys.
    """
    allowed = json.loads(os.environ.get("WORKFLOW_INPUTS_MAP", "{}")).get(repo)
    if allowed is not None:
        return {k: body[k] for k in allowed if k in body}
    return {k: v for k, v in body.items() if k not in {"repo", "ref"}}


# ---------------------------------------------------------------------------
# Password generation + Slack notification
# ---------------------------------------------------------------------------

def _gen_password() -> str:
    alpha = string.ascii_letters
    digits = string.digits
    sym = "!@#%&"
    pool = alpha + digits + sym
    while True:
        pw = "".join(_pyrand.choice(pool) for _ in range(12))
        if (any(c.islower() for c in pw) and any(c.isupper() for c in pw)
                and any(c in digits for c in pw) and any(c in sym for c in pw)):
            return pw


def _notify_slack(email: str, password: str, role: str, actor: str) -> None:
    """Fire-and-forget Slack webhook post. Never raises — failure just logs."""
    webhook = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if not webhook:
        return
    dashboard = os.environ.get("DASHBOARD_URL", "")
    project = os.environ.get("PROJECT_NAME", "API test dashboard")
    text = (
        f":new: *{project} — new user*\n"
        f"• *Email*: `{email}`\n"
        f"• *Role*: `{role}`\n"
        f"• *Temporary password*: `{password}`\n"
        f"• *Dashboard*: {dashboard}\n"
        f"• *Created by*: {actor}\n"
        "Please DM the credentials to the user and remind them to change the password after first login."
    )
    try:
        req = urllib.request.Request(
            webhook,
            data=json.dumps({"text": text}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=4).read()
    except Exception as e:
        print(f"[slack] notify failed: {e}")


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

def handler(event, _ctx):
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = event.get("rawPath", "/")

    if method == "OPTIONS":
        return _resp(204, {})

    owner = os.environ["GITHUB_OWNER"]
    actor = _actor(event)

    # --- POST /trigger --------------------------------------------------------
    if method == "POST" and path.endswith("/trigger"):
        if not _can_trigger(event):
            return _resp(403, {"error": "forbidden: need 'tester' or 'admin' role"})

        body = json.loads(event.get("body") or "{}")
        repo = body.get("repo", "")
        ref = body.get("ref", "main")

        if not _allowed_repo(repo):
            return _resp(403, {"error": f"repo '{repo}' not in ALLOWED_REPOS"})

        workflow = _workflow_for(repo)
        if not workflow:
            return _resp(400, {"error": f"no workflow mapped for '{repo}'"})

        inputs = _workflow_inputs(repo, body)
        status, data = _gh(
            "POST",
            f"/repos/{owner}/{repo}/actions/workflows/{workflow}/dispatches",
            {"ref": ref, "inputs": inputs},
        )
        print(f"[audit] {actor} dispatched {repo}/{workflow} inputs={inputs} -> {status}")
        if status >= 300:
            return _resp(status, data)
        return _resp(202, {"dispatched": True, "repo": repo, "workflow": workflow, "by": actor})

    # --- GET /runs, /runs/{id}, /runs/{id}/logs -------------------------------
    if method == "GET" and "/runs" in path:
        qs = event.get("queryStringParameters") or {}
        repo = qs.get("repo", "")
        if not _allowed_repo(repo):
            return _resp(403, {"error": f"repo '{repo}' not allowed"})

        parts = [p for p in path.split("/") if p]

        # /runs/{id}/logs
        if len(parts) >= 3 and parts[-1] == "logs" and parts[-2].isdigit():
            run_id = parts[-2]
            st, run = _gh("GET", f"/repos/{owner}/{repo}/actions/runs/{run_id}")
            if st >= 300:
                return _resp(st, run)
            jobs_st, jobs = _gh("GET", f"/repos/{owner}/{repo}/actions/runs/{run_id}/jobs")
            lines: list[str] = []
            if jobs_st < 300 and jobs.get("jobs"):
                first_job_id = jobs["jobs"][0]["id"]
                url = f"https://api.github.com/repos/{owner}/{repo}/actions/jobs/{first_job_id}/logs"
                req = urllib.request.Request(url, method="GET")
                req.add_header("Authorization", f"Bearer {_get_token()}")
                req.add_header("Accept", "application/vnd.github+json")
                try:
                    with urllib.request.urlopen(req, timeout=15) as r:
                        text = r.read().decode(errors="replace")
                        lines = text.splitlines()[-500:]
                except urllib.error.HTTPError as e:
                    lines = [f"[logs unavailable: {e.code}]"]
            return _resp(200, {
                "lines": lines,
                "status": run.get("status"),
                "conclusion": run.get("conclusion"),
            })

        # /runs/{id}
        run_id = parts[-1] if parts[-1].isdigit() else None
        if run_id:
            status, data = _gh("GET", f"/repos/{owner}/{repo}/actions/runs/{run_id}")
            return _resp(status, data)

        # /runs
        status, data = _gh(
            "GET",
            f"/repos/{owner}/{repo}/actions/runs?event=workflow_dispatch&per_page=10",
        )
        if status >= 300:
            return _resp(status, data)
        trimmed = [
            {
                "id": r["id"],
                "name": r.get("name"),
                "status": r.get("status"),
                "conclusion": r.get("conclusion"),
                "created_at": r.get("created_at"),
                "html_url": r.get("html_url"),
                "actor": r.get("triggering_actor", {}).get("login"),
            }
            for r in data.get("workflow_runs", [])
        ]
        return _resp(200, {"runs": trimmed})

    # --- /admin/* (user management) -------------------------------------------
    if path.startswith("/admin/"):
        if not _is_admin(event):
            return _resp(403, {"error": "admin only"})

        pool_id = os.environ["USER_POOL_ID"]

        if method == "GET" and path == "/admin/users":
            try:
                r = _cognito.list_users(UserPoolId=pool_id, Limit=60)
                users = []
                for u in r.get("Users", []):
                    attrs = {a["Name"]: a["Value"] for a in u.get("Attributes", [])}
                    uname = u["Username"]
                    role = "viewer"
                    try:
                        gr = _cognito.admin_list_groups_for_user(
                            UserPoolId=pool_id, Username=uname, Limit=10,
                        )
                        role_groups = [g["GroupName"] for g in gr.get("Groups", []) if g["GroupName"] in ROLES]
                        if "admin" in role_groups:
                            role = "admin"
                        elif "tester" in role_groups:
                            role = "tester"
                    except Exception as ge:
                        print(f"[groups-lookup] {uname} failed: {ge}")
                    users.append({
                        "email": attrs.get("email"),
                        "status": u.get("UserStatus"),
                        "enabled": u.get("Enabled"),
                        "created": u.get("UserCreateDate").isoformat() if u.get("UserCreateDate") else None,
                        "role": role,
                    })
                return _resp(200, {"users": users})
            except Exception as e:
                return _resp(500, {"error": str(e)})

        if method == "POST" and path == "/admin/users":
            body = json.loads(event.get("body") or "{}")
            email = (body.get("email") or "").strip().lower()
            if "@" not in email:
                return _resp(400, {"error": "invalid email"})
            role = (body.get("role") or "viewer").strip()
            if role not in ROLES:
                return _resp(400, {"error": f"invalid role '{role}'"})
            password = body.get("password") or _gen_password()
            try:
                _cognito.admin_create_user(
                    UserPoolId=pool_id,
                    Username=email,
                    UserAttributes=[
                        {"Name": "email", "Value": email},
                        {"Name": "email_verified", "Value": "true"},
                    ],
                    TemporaryPassword=password,
                    DesiredDeliveryMediums=["EMAIL"],
                )
                # Mark permanent to skip FORCE_CHANGE_PASSWORD state (hosted-UI
                # has a known quirk reporting "User does not exist" in that state).
                _cognito.admin_set_user_password(
                    UserPoolId=pool_id, Username=email, Password=password, Permanent=True,
                )
                _cognito.admin_add_user_to_group(UserPoolId=pool_id, Username=email, GroupName=role)
                print(f"[audit] {actor} created user {email} role={role}")
                _notify_slack(email, password, role, actor)
                return _resp(201, {"email": email, "password": password, "role": role})
            except _cognito.exceptions.UsernameExistsException:
                return _resp(409, {"error": "user already exists"})
            except Exception as e:
                return _resp(500, {"error": str(e)})

        if method == "PUT" and path.startswith("/admin/users/") and path.endswith("/role"):
            email_part = path[len("/admin/users/"):-len("/role")]
            email = urllib.parse.unquote(email_part)
            body = json.loads(event.get("body") or "{}")
            new_role = (body.get("role") or "").strip()
            if new_role not in ROLES:
                return _resp(400, {"error": f"invalid role '{new_role}'"})
            try:
                current = _cognito.admin_list_groups_for_user(UserPoolId=pool_id, Username=email, Limit=10)
                for g in current.get("Groups", []):
                    if g["GroupName"] in ROLES and g["GroupName"] != new_role:
                        _cognito.admin_remove_user_from_group(
                            UserPoolId=pool_id, Username=email, GroupName=g["GroupName"],
                        )
                _cognito.admin_add_user_to_group(UserPoolId=pool_id, Username=email, GroupName=new_role)
                print(f"[audit] {actor} set role for {email} -> {new_role}")
                return _resp(200, {"email": email, "role": new_role})
            except Exception as e:
                return _resp(500, {"error": str(e)})

        if method == "DELETE" and path.startswith("/admin/users/"):
            email = path.split("/admin/users/", 1)[1]
            if email.lower() == actor.lower():
                return _resp(400, {"error": "cannot delete yourself"})
            try:
                _cognito.admin_delete_user(UserPoolId=pool_id, Username=email)
                print(f"[audit] {actor} deleted user {email}")
                return _resp(204, {})
            except Exception as e:
                return _resp(500, {"error": str(e)})

        return _resp(404, {"error": f"no admin route for {method} {path}"})

    return _resp(404, {"error": f"no route for {method} {path}"})
