#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Add a dashboard user to the Cognito pool and set a permanent password.
#
# Usage:
#   POOL_ID=... ./add-user.sh alice@example.com [password] [role]
#
# role: admin | tester | viewer  (default: tester)
#
# Prints credentials at the end — deliver to the user via a secure channel.
# ---------------------------------------------------------------------------
set -euo pipefail

EMAIL="${1:?usage: $0 <email> [password] [role]}"
PASSWORD="${2:-}"
ROLE="${3:-tester}"

: "${POOL_ID:?set POOL_ID (User Pool Id from CloudFormation outputs)}"
REGION="${AWS_REGION:-us-east-1}"
DASHBOARD_URL="${DASHBOARD_URL:-(set DASHBOARD_URL env to surface it below)}"

case "$ROLE" in
  admin|tester|viewer) ;;
  *) echo "role must be admin|tester|viewer (got: $ROLE)"; exit 1 ;;
esac

if [[ -z "$PASSWORD" ]]; then
  # 12 chars: upper + lower + digits + symbol (satisfies Cognito default policy)
  PASSWORD="$(LC_ALL=C tr -dc 'A-HJ-NP-Za-km-z2-9' </dev/urandom | head -c 10)!A"
fi

echo "==> Creating user: $EMAIL ($ROLE)"
aws cognito-idp admin-create-user \
  --user-pool-id "$POOL_ID" \
  --username "$EMAIL" \
  --user-attributes Name=email,Value="$EMAIL" Name=email_verified,Value=true \
  --message-action SUPPRESS \
  --region "$REGION" >/dev/null

echo "==> Setting permanent password"
aws cognito-idp admin-set-user-password \
  --user-pool-id "$POOL_ID" \
  --username "$EMAIL" \
  --password "$PASSWORD" \
  --permanent \
  --region "$REGION"

echo "==> Adding to group: $ROLE"
aws cognito-idp admin-add-user-to-group \
  --user-pool-id "$POOL_ID" \
  --username "$EMAIL" \
  --group-name "$ROLE" \
  --region "$REGION"

cat <<EOF

========================================================
  User created — deliver these credentials securely:

  Dashboard : ${DASHBOARD_URL}
  Username  : ${EMAIL}
  Password  : ${PASSWORD}
  Role      : ${ROLE}

  The user can change their password via the Cognito
  Hosted UI after first login.
========================================================
EOF
