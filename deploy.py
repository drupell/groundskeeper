#!/usr/bin/env python3
"""
Groundskeeper — guided AWS deploy.

Self-bootstrapping: on first run, uses `uv` to sync the project's dependencies
into a local `.venv`, then re-execs itself inside that venv. From there it
walks the user through every AWS resource it needs to create, shows the IAM
permissions before applying them, and writes a state file (.groundskeeper-
deploy-state.json) that teardown.py uses to clean up.

Re-running the script is idempotent — it reuses existing resources by name
when state is missing and updates in place otherwise.

Two modes:

  1. Default deploy mode — the full guided walk-through above. Just run
     `python deploy.py --environment {dev,prod}`.

  2. `--bootstrap-oidc <owner>/<repo>` — a separate one-shot that wires up the
     GitHub Actions OIDC trust into the current AWS account. Creates the
     OIDC provider (if missing) and the per-environment deploy role
     (default name: groundskeeper-ci-deploy) trusted to assume from the
     matching branch (dev→refs/heads/dev, prod→refs/heads/main). Does NOT
     touch any deploy resources — run it once per AWS account before CI
     can deploy. See RELEASE_CHECKLIST.md → "CI deploy bootstrap" for the
     hand-rolled alternative.

Prerequisites:
  - Python 3.12+
  - uv (https://docs.astral.sh/uv/#installation)
  - AWS CLI configured (any of: env vars, ~/.aws/credentials, SSO)
  - Node 18+ on PATH (only needed for the frontend build step)
"""

# ---------------------------------------------------------------------------
# BOOTSTRAP — stdlib only above this line. Third-party imports come after the
# uv sync + venv re-exec below.
# ---------------------------------------------------------------------------
import json
import os
import shutil as _bootstrap_shutil
import subprocess
import sys
from pathlib import Path

PROJECT_NAME = "Groundskeeper"
PROJECT_TAG_KEY = "project"
PROJECT_TAG_VALUE = "groundskeeper"
ENVIRONMENT_TAG_KEY = "environment"
ENVIRONMENT_CHOICES = ("dev", "prod")
DEFAULT_ENVIRONMENT = "prod"

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
PYPROJECT = ROOT / "pyproject.toml"
STATE_FILE = ROOT / ".groundskeeper-deploy-state.json"
BUILD_DIR = ROOT / ".groundskeeper-build"
FRONTEND_DIR = ROOT / "frontend"
BACKEND_DIR = ROOT / "backend"

PY_MIN = (3, 12)


def _venv_python() -> Path:
    return (
        VENV_DIR
        / ("Scripts" if os.name == "nt" else "bin")
        / ("python.exe" if os.name == "nt" else "python")
    )


def _running_in_venv() -> bool:
    try:
        return Path(sys.prefix).resolve() == VENV_DIR.resolve()
    except OSError:
        return False


def _bootstrap_and_reexec() -> None:
    if sys.version_info < PY_MIN:
        sys.stderr.write(
            f"ERROR: Python {PY_MIN[0]}.{PY_MIN[1]}+ required, found {sys.version.split()[0]}\n"
        )
        sys.exit(1)
    uv_path = _bootstrap_shutil.which("uv")
    if uv_path is None:
        sys.stderr.write(
            "ERROR: `uv` is required to bootstrap this script.\n"
            "       Install it from https://docs.astral.sh/uv/#installation, then re-run.\n"
        )
        sys.exit(1)
    print("[bootstrap] Syncing dependencies with uv …")
    subprocess.check_call([uv_path, "sync", "--quiet"], cwd=str(ROOT))
    py = _venv_python()
    if not py.exists():
        sys.stderr.write(f"ERROR: venv python not found at {py} after `uv sync`\n")
        sys.exit(1)
    os.execv(str(py), [str(py), __file__, *sys.argv[1:]])


if not _running_in_venv():
    _bootstrap_and_reexec()


# ---------------------------------------------------------------------------
# IMPORTS available once running inside the venv
# ---------------------------------------------------------------------------
import argparse
import base64
import shutil
import time
import urllib.error
import urllib.request
import zipfile

import boto3
import questionary
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError, NoCredentialsError
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

console = Console()


# ---------------------------------------------------------------------------
# RESOURCE NAMES — kept centralized so teardown.py mirrors them exactly.
# ---------------------------------------------------------------------------
PREFIX = "groundskeeper"

LAMBDA_ROLE_NAME = f"{PREFIX}-lambda-role"
SCHEDULER_ROLE_NAME = f"{PREFIX}-scheduler-role"

DDB_TABLE_NAME = f"{PREFIX}-config"

PAT_SECRET_NAME = f"{PREFIX}/github-pat"

LAMBDA_ORCHESTRATOR_NAME = f"{PREFIX}-orchestrator"
LAMBDA_EXECUTOR_NAME = f"{PREFIX}-executor"
LAMBDA_API_NAME = f"{PREFIX}-api"

ORCHESTRATOR_RULE_NAME = f"{PREFIX}-orchestrator-daily"
API_GATEWAY_NAME = f"{PREFIX}-api"
API_STAGE = "prod"
API_KEY_NAME = f"{PREFIX}-api-key"
USAGE_PLAN_NAME = f"{PREFIX}-usage-plan"

AMPLIFY_APP_NAME = f"{PREFIX}-dashboard"
AMPLIFY_BRANCH_NAME = "main"
AMPLIFY_BASIC_AUTH_USERNAME = "admin"

LAMBDA_RUNTIME = "python3.12"
LAMBDA_HANDLER = "handler.handler"
LAMBDA_TIMEOUT_SECONDS = 60
LAMBDA_MEMORY_MB = 256

BEDROCK_MODEL_ID = "amazon.nova-lite-v1:0"

# ---------------------------------------------------------------------------
# OIDC bootstrap — wires up GitHub Actions → AWS (one-shot, separate mode).
# ---------------------------------------------------------------------------
GITHUB_OIDC_URL = "https://token.actions.githubusercontent.com"
GITHUB_OIDC_HOST = "token.actions.githubusercontent.com"
GITHUB_OIDC_AUDIENCE = "sts.amazonaws.com"
# Thumbprint for token.actions.githubusercontent.com — see GitHub's docs at:
# https://docs.github.com/en/actions/deployment/security-hardening-your-deployments/configuring-openid-connect-in-amazon-web-services
GITHUB_OIDC_THUMBPRINT = "6938fd4d98bab03faadb97b34396831e3780aea1"
DEFAULT_CI_DEPLOY_ROLE_NAME = f"{PREFIX}-ci-deploy"
CI_DEPLOY_ROLE_POLICY_ARN = "arn:aws:iam::aws:policy/AdministratorAccess"
# dev → refs/heads/dev, prod → refs/heads/main (matches the CI workflows).
ENVIRONMENT_BRANCH = {"dev": "dev", "prod": "main"}
GITHUB_SECRET_NAME = {
    "dev": "AWS_DEV_DEPLOY_ROLE_ARN",
    "prod": "AWS_PROD_DEPLOY_ROLE_ARN",
}

# Orchestrator fires daily at 12:00 UTC — chosen so it lands in the morning
# for US/EU users (07:00 EST / 08:00 EDT / 12:00 GMT / 13:00 BST / 14:00 CEST)
# rather than at the *end* of the local day. The orchestrator's own code still
# picks the user's local date based on their configured timezone; this just
# avoids the surprise of "today's plan" being created at 8 PM local.
ORCHESTRATOR_CRON = "cron(0 12 * * ? *)"

# Files in dist/* that should be served with appropriate cache headers — we
# rely on Amplify defaults for now and only configure SPA routing.
AMPLIFY_SPA_REWRITE = {
    "source": "</^[^.]+$|\\.(?!(css|gif|ico|jpg|jpeg|js|png|txt|svg|woff|woff2|ttf|map|json|webp)$)([^.]+$)/>",
    "target": "/index.html",
    "status": "200",
}


# ---------------------------------------------------------------------------
# TAGGING — every resource carries BOTH project=groundskeeper AND
# environment=<env> so teardown.py can env-scope its discovery filter.
# ---------------------------------------------------------------------------
def tag_list(environment: str) -> list[dict]:
    """Return tags in the [{Key, Value}, ...] shape used by IAM, DDB, Secrets,
    EventBridge, and S3 PutBucketTagging."""
    return [
        {"Key": PROJECT_TAG_KEY, "Value": PROJECT_TAG_VALUE},
        {"Key": ENVIRONMENT_TAG_KEY, "Value": environment},
    ]


def tag_map(environment: str) -> dict:
    """Return tags as a flat {Key: Value} dict — the shape used by Lambda,
    API Gateway, and Amplify."""
    return {
        PROJECT_TAG_KEY: PROJECT_TAG_VALUE,
        ENVIRONMENT_TAG_KEY: environment,
    }


# ---------------------------------------------------------------------------
# STATE — local JSON file tracking every resource we've created.
# ---------------------------------------------------------------------------
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            console.print(
                f"[yellow]Warning:[/yellow] {STATE_FILE.name} is unreadable — starting fresh."
            )
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))


# ---------------------------------------------------------------------------
# OUTPUT HELPERS
# ---------------------------------------------------------------------------
def banner(title: str, subtitle: str = "") -> None:
    body = f"[bold]{title}[/bold]"
    if subtitle:
        body += f"\n[dim]{subtitle}[/dim]"
    console.print(Panel(body, border_style="cyan", padding=(1, 2)))


def section(title: str) -> None:
    console.print()
    console.rule(f"[bold cyan]{title}[/bold cyan]", style="cyan")


def step(msg: str) -> None:
    console.print(f"  [cyan]→[/cyan] {msg}")


def ok(msg: str) -> None:
    console.print(f"  [green]✓[/green] {msg}")


def warn(msg: str) -> None:
    console.print(f"  [yellow]![/yellow] {msg}")


def fail(msg: str) -> None:
    console.print(f"  [red]×[/red] {msg}")


def info(msg: str) -> None:
    console.print(f"  [dim]·[/dim] {msg}")


# ---------------------------------------------------------------------------
# AWS CLIENT FACTORY
# ---------------------------------------------------------------------------
def make_clients(region: str) -> dict:
    cfg = BotoConfig(retries={"max_attempts": 10, "mode": "adaptive"}, region_name=region)
    session = boto3.session.Session(region_name=region)
    return {
        "sts": session.client("sts", config=cfg),
        "iam": session.client("iam", config=cfg),
        "dynamodb": session.client("dynamodb", config=cfg),
        "secrets": session.client("secretsmanager", config=cfg),
        "s3": session.client("s3", config=cfg),
        "lambda": session.client("lambda", config=cfg),
        "apigw": session.client("apigateway", config=cfg),
        "events": session.client("events", config=cfg),
        "scheduler": session.client("scheduler", config=cfg),
        "amplify": session.client("amplify", config=cfg),
        "bedrock": session.client("bedrock", config=cfg),
    }


# ---------------------------------------------------------------------------
# PREFLIGHT — confirm AWS account/region, check Node, warn about Bedrock.
# ---------------------------------------------------------------------------
def confirm_account_and_region(
    initial_region: str | None,
    expected_account: str | None = None,
    non_interactive: bool = False,
) -> tuple[str, str, str]:
    section("AWS context")
    try:
        sts = boto3.client("sts")
        ident = sts.get_caller_identity()
    except NoCredentialsError:
        fail(
            "No AWS credentials found. Configure the AWS CLI first (aws configure / aws sso login)."
        )
        sys.exit(1)
    except ClientError as e:
        fail(f"Couldn't check AWS credentials: {e}")
        sys.exit(1)

    account = ident["Account"]
    caller = ident["Arn"]

    # Hard guard: if a target account was asserted, the resolved identity must
    # match it exactly. This is stronger than a "press y" confirm — it makes
    # deploying into the wrong account impossible rather than merely unlikely.
    if expected_account and account != expected_account:
        fail(
            f"Account guard caught a mismatch: your credentials resolve to "
            f"{account}, but --account asked for {expected_account}. Aborting "
            f"before anything is created."
        )
        sys.exit(1)

    # Region: explicit arg → state → env → ask
    region = initial_region or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not region and non_interactive:
        fail("--non-interactive needs --region (or AWS_REGION) — can't prompt for it.")
        sys.exit(2)
    if not region:
        region = questionary.select(
            "Which AWS region should Groundskeeper deploy into?",
            choices=[
                "us-east-1",
                "us-west-2",
                "eu-west-1",
                "eu-central-1",
                "ap-southeast-1",
                "ap-northeast-1",
            ],
            default="us-east-1",
        ).ask()
        if not region:
            sys.exit(1)

    tbl = Table(show_header=False, box=None, padding=(0, 2))
    tbl.add_column(style="dim")
    tbl.add_column()
    tbl.add_row("Account", account)
    tbl.add_row("Caller", caller)
    tbl.add_row("Region", region)
    console.print(tbl)

    if non_interactive:
        ok(f"Account {account} matches the --account guard. Proceeding non-interactively.")
        return account, region, caller

    if not questionary.confirm("Deploy into this account and region?", default=True).ask():
        console.print("[dim]Aborted.[/dim]")
        sys.exit(0)
    return account, region, caller


def preflight_tooling() -> None:
    section("Preflight")
    # Node
    node = shutil.which("node")
    if not node:
        warn(
            "Node isn't on PATH — we'll skip the frontend build. Install Node 18+ and re-run when you're ready to ship the dashboard."
        )
    else:
        try:
            out = subprocess.run(
                [node, "--version"], capture_output=True, text=True, timeout=5
            ).stdout.strip()
            ok(f"Node detected: {out}")
        except Exception:
            warn("Node is on PATH but the version check didn't return cleanly.")
    # Frontend project
    if (FRONTEND_DIR / "package.json").exists():
        ok("Frontend project found.")
    else:
        warn(
            f"No {FRONTEND_DIR.name}/package.json yet — we'll skip the dashboard deploy step this run."
        )


def warn_about_bedrock(clients: dict) -> None:
    try:
        resp = clients["bedrock"].list_foundation_models()
        ids = {m.get("modelId") for m in resp.get("modelSummaries", [])}
        if BEDROCK_MODEL_ID not in ids:
            warn(
                f"Bedrock model {BEDROCK_MODEL_ID} isn't visible in this region. The executor Lambda will fail until you "
                f"turn on model access (Bedrock console → Model access)."
            )
        else:
            ok(f"Bedrock model {BEDROCK_MODEL_ID} is available.")
    except ClientError as e:
        warn(
            f"Couldn't verify Bedrock access ({e.response['Error']['Code']}). Double-check model access manually."
        )


# ---------------------------------------------------------------------------
# DEPLOYMENT PLAN — shown to the user before any AWS calls.
# ---------------------------------------------------------------------------
def show_deployment_plan(
    account: str, region: str, environment: str, non_interactive: bool = False
) -> None:
    section("What this script will create")
    rows = [
        (
            "IAM role",
            LAMBDA_ROLE_NAME,
            "Lambda execution role — least-privilege access to DynamoDB, Secrets Manager, EventBridge Scheduler, Bedrock Nova Lite, and Amplify (for in-app password rotation).",
        ),
        (
            "IAM role",
            SCHEDULER_ROLE_NAME,
            "EventBridge Scheduler assume role — lets one-time schedules invoke the executor Lambda.",
        ),
        (
            "DynamoDB",
            DDB_TABLE_NAME,
            "Single on-demand table — holds your config, repo metadata, and the commit log.",
        ),
        (
            "Secret",
            PAT_SECRET_NAME,
            "Empty placeholder — you'll paste your GitHub token into the dashboard, and the API Lambda saves it here.",
        ),
        (
            "S3 bucket",
            f"{PREFIX}-artifacts-{account}-{region}",
            "Versioned and private — holds Lambda zips and the Amplify deployment bundle.",
        ),
        (
            "Lambda",
            LAMBDA_ORCHESTRATOR_NAME,
            "Nightly planner. Reads your config, samples the distribution, and creates one-time schedules.",
        ),
        (
            "Lambda",
            LAMBDA_EXECUTOR_NAME,
            "Per-commit worker. Picks a file, calls Bedrock, and writes the commit back through the GitHub API.",
        ),
        (
            "Lambda",
            LAMBDA_API_NAME,
            "Dashboard backend behind API Gateway. Handles config reads/writes, token verification, vacation toggle, and so on.",
        ),
        (
            "API Gateway",
            API_GATEWAY_NAME,
            "REST API with an API key and usage plan. The key gets baked into the dashboard at build time.",
        ),
        (
            "EventBridge",
            ORCHESTRATOR_RULE_NAME,
            "Cron rule that fires the orchestrator daily at 12:00 UTC.",
        ),
        (
            "Amplify",
            AMPLIFY_APP_NAME,
            "Manually-deployed hosting with built-in basic auth. You'll pick the dashboard password.",
        ),
    ]
    table = Table(show_lines=False, padding=(0, 2))
    table.add_column("Kind", style="cyan", no_wrap=True)
    table.add_column("Name", style="bold")
    table.add_column("Why")
    for k, n, w in rows:
        table.add_row(k, n, w)
    console.print(table)
    console.print(
        f"\n  Every resource is tagged "
        f"[bold]{PROJECT_TAG_KEY}={PROJECT_TAG_VALUE}[/bold] + "
        f"[bold]{ENVIRONMENT_TAG_KEY}={environment}[/bold] so "
        f"[bold]teardown.py[/bold] can find and remove only this environment's resources."
    )
    if non_interactive:
        return
    if not questionary.confirm("Continue?", default=True).ask():
        console.print("[dim]Aborted.[/dim]")
        sys.exit(0)


# ---------------------------------------------------------------------------
# IAM
# ---------------------------------------------------------------------------
def _trust_policy(service: str) -> str:
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {"Effect": "Allow", "Principal": {"Service": service}, "Action": "sts:AssumeRole"}
            ],
        }
    )


def _lambda_inline_policy(account: str, region: str, amplify_app_id: str | None = None) -> dict:
    scheduler_role_arn = f"arn:aws:iam::{account}:role/{SCHEDULER_ROLE_NAME}"
    # AmplifyPasswordRotation is only added once we know the app id, so the
    # resource ARN can target the single Groundskeeper app rather than every
    # Amplify app in the account.
    amplify_resource = (
        f"arn:aws:amplify:{region}:{account}:apps/{amplify_app_id}" if amplify_app_id else None
    )
    statements = [
        {
            "Sid": "Logs",
            "Effect": "Allow",
            "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
            "Resource": f"arn:aws:logs:{region}:{account}:log-group:/aws/lambda/{PREFIX}-*",
        },
        {
            "Sid": "DynamoDB",
            "Effect": "Allow",
            "Action": [
                "dynamodb:GetItem",
                "dynamodb:PutItem",
                "dynamodb:UpdateItem",
                "dynamodb:DeleteItem",
                "dynamodb:Query",
                "dynamodb:Scan",
                "dynamodb:BatchGetItem",
                "dynamodb:BatchWriteItem",
            ],
            "Resource": [
                f"arn:aws:dynamodb:{region}:{account}:table/{DDB_TABLE_NAME}",
                f"arn:aws:dynamodb:{region}:{account}:table/{DDB_TABLE_NAME}/index/*",
            ],
        },
        {
            "Sid": "Secrets",
            "Effect": "Allow",
            "Action": [
                "secretsmanager:GetSecretValue",
                "secretsmanager:PutSecretValue",
                "secretsmanager:DescribeSecret",
            ],
            "Resource": f"arn:aws:secretsmanager:{region}:{account}:secret:{PREFIX}/*",
        },
        {
            "Sid": "SchedulerManage",
            "Effect": "Allow",
            "Action": [
                "scheduler:CreateSchedule",
                "scheduler:UpdateSchedule",
                "scheduler:DeleteSchedule",
                "scheduler:GetSchedule",
            ],
            "Resource": f"arn:aws:scheduler:{region}:{account}:schedule/default/{PREFIX}-*",
        },
        {
            # ListSchedules is a discovery action evaluated against the
            # whole namespace, not individual schedule ARNs — a narrowly
            # scoped Resource causes AccessDenied on every real orchestrator
            # run (it cleans up orphans by listing first).
            "Sid": "SchedulerList",
            "Effect": "Allow",
            "Action": ["scheduler:ListSchedules"],
            "Resource": "*",
        },
        {
            "Sid": "SchedulerPassRole",
            "Effect": "Allow",
            "Action": ["iam:PassRole"],
            "Resource": scheduler_role_arn,
            "Condition": {"StringEquals": {"iam:PassedToService": "scheduler.amazonaws.com"}},
        },
        {
            "Sid": "Bedrock",
            "Effect": "Allow",
            "Action": ["bedrock:InvokeModel"],
            "Resource": [
                f"arn:aws:bedrock:{region}::foundation-model/{BEDROCK_MODEL_ID}",
                f"arn:aws:bedrock:*::foundation-model/{BEDROCK_MODEL_ID}",
            ],
        },
        {
            "Sid": "AmplifyDiscovery",
            "Effect": "Allow",
            "Action": ["amplify:ListApps"],
            "Resource": "*",
        },
        {
            "Sid": "InvokeWorkers",
            "Effect": "Allow",
            "Action": ["lambda:InvokeFunction"],
            "Resource": [
                f"arn:aws:lambda:{region}:{account}:function:{LAMBDA_EXECUTOR_NAME}",
                f"arn:aws:lambda:{region}:{account}:function:{LAMBDA_ORCHESTRATOR_NAME}",
            ],
        },
    ]
    if amplify_resource:
        statements.append(
            {
                "Sid": "AmplifyPasswordRotation",
                "Effect": "Allow",
                "Action": ["amplify:UpdateApp"],
                "Resource": amplify_resource,
            }
        )
    return {"Version": "2012-10-17", "Statement": statements}


def _scheduler_inline_policy(account: str, region: str) -> dict:
    executor_arn = f"arn:aws:lambda:{region}:{account}:function:{LAMBDA_EXECUTOR_NAME}"
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "InvokeExecutor",
                "Effect": "Allow",
                "Action": ["lambda:InvokeFunction"],
                "Resource": [executor_arn, f"{executor_arn}:*"],
            }
        ],
    }


def _show_policy_preview(name: str, policy: dict) -> None:
    console.print(
        Panel(
            json.dumps(policy, indent=2),
            title=f"[bold]{name}[/bold]",
            border_style="dim",
            padding=(0, 1),
        )
    )


def ensure_iam_roles(
    clients: dict,
    account: str,
    region: str,
    environment: str,
    state: dict,
    yes: bool,
    amplify_app_id: str | None = None,
) -> None:
    section("IAM roles")
    iam = clients["iam"]
    lambda_policy = _lambda_inline_policy(account, region, amplify_app_id=amplify_app_id)
    scheduler_policy = _scheduler_inline_policy(account, region)

    if not yes:
        console.print("Here are the inline policies we'll attach. Have a look before continuing:")
        _show_policy_preview(f"{LAMBDA_ROLE_NAME} → inline policy", lambda_policy)
        _show_policy_preview(f"{SCHEDULER_ROLE_NAME} → inline policy", scheduler_policy)
        if not questionary.confirm("Apply these policies?", default=True).ask():
            sys.exit(0)

    state.setdefault("iam", {})
    state["iam"]["lambda_role_arn"] = _ensure_role(
        iam,
        LAMBDA_ROLE_NAME,
        _trust_policy("lambda.amazonaws.com"),
        "groundskeeper-lambda-inline",
        lambda_policy,
        environment,
    )
    ok(f"{LAMBDA_ROLE_NAME} ready.")
    state["iam"]["scheduler_role_arn"] = _ensure_role(
        iam,
        SCHEDULER_ROLE_NAME,
        _trust_policy("scheduler.amazonaws.com"),
        "groundskeeper-scheduler-inline",
        scheduler_policy,
        environment,
    )
    ok(f"{SCHEDULER_ROLE_NAME} ready.")
    save_state(state)


def refine_lambda_role_for_amplify(
    clients: dict, account: str, region: str, amplify_app_id: str
) -> None:
    """Second-pass IAM update: now that the Amplify app id is known, replace
    the Lambda inline policy with one whose AmplifyPasswordRotation statement
    targets exactly that app rather than every Amplify app in the account."""
    iam = clients["iam"]
    policy = _lambda_inline_policy(account, region, amplify_app_id=amplify_app_id)
    iam.put_role_policy(
        RoleName=LAMBDA_ROLE_NAME,
        PolicyName="groundskeeper-lambda-inline",
        PolicyDocument=json.dumps(policy),
    )
    ok(f"Narrowed amplify:UpdateApp to app {amplify_app_id}.")


def _ensure_role(
    iam,
    role_name: str,
    trust_policy: str,
    inline_name: str,
    inline_policy: dict,
    environment: str,
) -> str:
    desired_tags = tag_list(environment)
    try:
        iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=trust_policy,
            Description=f"{PROJECT_NAME} - {role_name}",
            Tags=desired_tags,
        )
        step(f"Created role {role_name}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "EntityAlreadyExists":
            raise
        step(f"Reusing role {role_name}")
        # Keep the trust policy current in case it drifted
        iam.update_assume_role_policy(RoleName=role_name, PolicyDocument=trust_policy)
        # Re-tag in place — covers legacy roles that were created with only the
        # `project` tag, and keeps the environment tag in sync if it drifted.
        iam.tag_role(RoleName=role_name, Tags=desired_tags)

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=inline_name,
        PolicyDocument=json.dumps(inline_policy),
    )
    role = iam.get_role(RoleName=role_name)["Role"]
    return role["Arn"]


# ---------------------------------------------------------------------------
# DynamoDB
# ---------------------------------------------------------------------------
def ensure_dynamodb(clients: dict, environment: str, state: dict) -> None:
    section("DynamoDB")
    ddb = clients["dynamodb"]
    desired_tags = tag_list(environment)
    try:
        ddb.create_table(
            TableName=DDB_TABLE_NAME,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            Tags=desired_tags,
        )
        step(f"Creating table {DDB_TABLE_NAME} …")
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceInUseException":
            raise
        step(f"Reusing table {DDB_TABLE_NAME}")

    waiter = ddb.get_waiter("table_exists")
    waiter.wait(TableName=DDB_TABLE_NAME, WaiterConfig={"Delay": 3, "MaxAttempts": 40})

    # Always retag — covers legacy tables that pre-date the `environment` tag
    # and keeps things consistent on re-runs.
    table_arn = ddb.describe_table(TableName=DDB_TABLE_NAME)["Table"]["TableArn"]
    ddb.tag_resource(ResourceArn=table_arn, Tags=desired_tags)

    state["dynamodb_table"] = DDB_TABLE_NAME
    save_state(state)
    ok(f"Table {DDB_TABLE_NAME} is active.")


# ---------------------------------------------------------------------------
# Secrets Manager
# ---------------------------------------------------------------------------
def ensure_secrets(clients: dict, environment: str, state: dict) -> None:
    section("Secrets")
    sm = clients["secrets"]
    arn = _ensure_secret(
        sm,
        PAT_SECRET_NAME,
        "GitHub token — set from the dashboard. Placeholder until then.",
        environment,
    )
    state.setdefault("secrets", {})["github_pat"] = arn
    save_state(state)
    ok(f"{PAT_SECRET_NAME} ready.")


def _ensure_secret(sm, name: str, description: str, environment: str) -> str:
    desired_tags = tag_list(environment)
    try:
        resp = sm.create_secret(
            Name=name,
            Description=description,
            SecretString=json.dumps({"value": "", "set": False}),
            Tags=desired_tags,
        )
        step(f"Created secret {name}")
        return resp["ARN"]
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "ResourceExistsException":
            step(f"Reusing secret {name}")
            arn = sm.describe_secret(SecretId=name)["ARN"]
            # Retag existing secret to add `environment` to legacy resources.
            sm.tag_resource(SecretId=name, Tags=desired_tags)
            return arn
        if code == "InvalidRequestException" and "scheduled for deletion" in str(e):
            warn(f"Secret {name} was scheduled for deletion — restoring it …")
            sm.restore_secret(SecretId=name)
            sm.tag_resource(SecretId=name, Tags=desired_tags)
            return sm.describe_secret(SecretId=name)["ARN"]
        raise


# ---------------------------------------------------------------------------
# S3 artifacts bucket
# ---------------------------------------------------------------------------
def ensure_artifacts_bucket(
    clients: dict, account: str, region: str, environment: str, state: dict
) -> str:
    section("S3 artifacts")
    s3 = clients["s3"]
    bucket = f"{PREFIX}-artifacts-{account}-{region}"
    try:
        if region == "us-east-1":
            s3.create_bucket(Bucket=bucket)
        else:
            s3.create_bucket(
                Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": region}
            )
        step(f"Created bucket {bucket}")
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            step(f"Reusing bucket {bucket}")
        else:
            raise

    s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    s3.put_public_access_block(
        Bucket=bucket,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    s3.put_bucket_tagging(
        Bucket=bucket,
        Tagging={"TagSet": tag_list(environment)},
    )
    state["s3_artifacts_bucket"] = bucket
    save_state(state)
    ok(f"Bucket {bucket} ready.")
    return bucket


# ---------------------------------------------------------------------------
# Lambda packaging + deployment
# ---------------------------------------------------------------------------
def _zip_handler_dir(src_dir: Path, out_path: Path, shared_dir: Path | None = None) -> None:
    """Package a Lambda handler.

    Lays the handler module flat at the zip root (so the runtime can import
    ``handler``) and, if ``shared_dir`` is given, nests it under ``shared/`` so
    handlers can ``from shared.foo import bar``.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for child in src_dir.iterdir():
            if child.name.startswith(".") or child.name == "__pycache__":
                continue
            if child.is_dir():
                for f in child.rglob("*"):
                    if f.is_file() and "__pycache__" not in f.parts:
                        zf.write(f, f.relative_to(src_dir))
            else:
                zf.write(child, child.name)
        if shared_dir and shared_dir.is_dir():
            for f in shared_dir.rglob("*"):
                if not f.is_file() or "__pycache__" in f.parts:
                    continue
                zf.write(f, Path("shared") / f.relative_to(shared_dir))


def _upload(s3, bucket: str, key: str, path: Path) -> str:
    s3.upload_file(str(path), bucket, key)
    head = s3.head_object(Bucket=bucket, Key=key)
    return head.get("VersionId", "null")


def _ensure_lambda(
    lc,
    name: str,
    role_arn: str,
    bucket: str,
    key: str,
    env: dict,
    description: str,
    environment: str,
) -> str:
    common = dict(
        FunctionName=name,
        Runtime=LAMBDA_RUNTIME,
        Role=role_arn,
        Handler=LAMBDA_HANDLER,
        Timeout=LAMBDA_TIMEOUT_SECONDS,
        MemorySize=LAMBDA_MEMORY_MB,
        Environment={"Variables": env},
        Description=description,
    )
    desired_tags = tag_map(environment)

    # IAM eventual consistency: new roles aren't immediately usable by Lambda.
    # Retry a handful of times on the specific assume-role failure.
    def _create():
        resp = lc.create_function(
            **common,
            Code={"S3Bucket": bucket, "S3Key": key},
            Tags=desired_tags,
            Publish=True,
        )
        lc.get_waiter("function_active_v2").wait(FunctionName=name)
        return resp

    def _update():
        lc.update_function_configuration(**common)
        lc.get_waiter("function_updated_v2").wait(FunctionName=name)
        resp = lc.update_function_code(FunctionName=name, S3Bucket=bucket, S3Key=key, Publish=True)
        lc.get_waiter("function_updated_v2").wait(FunctionName=name)
        # Retag — covers legacy functions that pre-date `environment`.
        unqualified_arn = (
            resp["FunctionArn"].rsplit(":", 1)[0]
            if resp["FunctionArn"].count(":") == 7
            else resp["FunctionArn"]
        )
        lc.tag_resource(Resource=unqualified_arn, Tags=desired_tags)
        return resp

    last_err = None
    for attempt in range(10):
        try:
            try:
                lc.get_function(FunctionName=name)
                resp = _update()
                step(f"Updated {name}")
            except ClientError as e:
                if e.response["Error"]["Code"] != "ResourceNotFoundException":
                    raise
                resp = _create()
                step(f"Created {name}")
            return (
                resp["FunctionArn"].rsplit(":", 1)[0]
                if resp["FunctionArn"].count(":") == 7
                else resp["FunctionArn"]
            )
        except ClientError as e:
            code = e.response["Error"]["Code"]
            msg = str(e)
            if code == "InvalidParameterValueException" and (
                "cannot be assumed" in msg
                or "role defined for the function cannot be assumed" in msg
            ):
                last_err = e
                time.sleep(3 + attempt)
                continue
            raise
    raise RuntimeError(f"Lambda role propagation timed out for {name}: {last_err}")


def ensure_lambdas(
    clients: dict, account: str, region: str, environment: str, state: dict, bucket: str
) -> None:
    section("Lambda functions")
    s3 = clients["s3"]
    lc = clients["lambda"]
    lambda_role_arn = state["iam"]["lambda_role_arn"]
    scheduler_role_arn = state["iam"]["scheduler_role_arn"]

    BUILD_DIR.mkdir(exist_ok=True)

    common_env = {
        "CONFIG_TABLE": DDB_TABLE_NAME,
        "PAT_SECRET_NAME": PAT_SECRET_NAME,
        "PROJECT_TAG": PROJECT_TAG_VALUE,
        "ENVIRONMENT": environment,
        "BEDROCK_MODEL_ID": BEDROCK_MODEL_ID,
    }

    plans = [
        (
            LAMBDA_ORCHESTRATOR_NAME,
            BACKEND_DIR / "lambdas" / "orchestrator",
            {
                **common_env,
                "EXECUTOR_ARN": f"arn:aws:lambda:{region}:{account}:function:{LAMBDA_EXECUTOR_NAME}",
                "SCHEDULER_ROLE_ARN": scheduler_role_arn,
            },
            "Groundskeeper nightly orchestrator.",
        ),
        (
            LAMBDA_EXECUTOR_NAME,
            BACKEND_DIR / "lambdas" / "executor",
            {**common_env},
            "Groundskeeper commit executor.",
        ),
        (
            LAMBDA_API_NAME,
            BACKEND_DIR / "lambdas" / "api",
            {
                **common_env,
                "ORCHESTRATOR_NAME": LAMBDA_ORCHESTRATOR_NAME,
                "EXECUTOR_NAME": LAMBDA_EXECUTOR_NAME,
                "AMPLIFY_APP_NAME": AMPLIFY_APP_NAME,
                "AMPLIFY_BASIC_AUTH_USERNAME": AMPLIFY_BASIC_AUTH_USERNAME,
            },
            "Groundskeeper dashboard API.",
        ),
    ]

    state.setdefault("lambda", {})
    for name, src_dir, env, description in plans:
        if not src_dir.exists():
            warn(f"Skipping {name} — source directory {src_dir} isn't there.")
            continue
        zip_path = BUILD_DIR / f"{name}.zip"
        _zip_handler_dir(src_dir, zip_path, shared_dir=BACKEND_DIR / "shared")
        key = f"lambda/{name}.zip"
        _upload(s3, bucket, key, zip_path)
        arn = _ensure_lambda(lc, name, lambda_role_arn, bucket, key, env, description, environment)
        state["lambda"][name] = arn
        save_state(state)
    ok("Lambda functions ready.")


def _ensure_lambda_permission(
    lc, function_name: str, statement_id: str, principal: str, source_arn: str
) -> None:
    """Add a Lambda resource-policy invoke permission AND verify it's readable.

    Lambda's policy store can land in a split state where ``add_permission``
    reports the Sid already exists but ``get_policy`` returns nothing — and the
    grantee (EventBridge / API Gateway) then silently can't invoke the
    function. We add, verify with a few retries (to absorb mere propagation
    lag), and if it still isn't visible, force consistency with remove + re-add.
    """

    def _present() -> bool:
        try:
            policy = lc.get_policy(FunctionName=function_name)["Policy"]
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                return False
            raise
        doc = json.loads(policy)
        return any(s.get("Sid") == statement_id for s in doc.get("Statement", []))

    def _add() -> None:
        try:
            lc.add_permission(
                FunctionName=function_name,
                StatementId=statement_id,
                Action="lambda:InvokeFunction",
                Principal=principal,
                SourceArn=source_arn,
            )
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceConflictException":
                raise

    def _verify(attempts: int) -> bool:
        for _ in range(attempts):
            if _present():
                return True
            time.sleep(2)
        return False

    _add()
    if _verify(5):
        return

    # Stuck/split policy — force it consistent with a clean remove + re-add.
    step(f"Lambda policy statement {statement_id} not readable; repairing …")
    try:
        lc.remove_permission(FunctionName=function_name, StatementId=statement_id)
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
    _add()
    if not _verify(8):
        raise RuntimeError(
            f"Lambda permission {statement_id} on {function_name} could not be "
            f"made consistent — {principal} may be unable to invoke it. Re-run "
            f"deploy.py, or add it manually."
        )


# ---------------------------------------------------------------------------
# API Gateway
# ---------------------------------------------------------------------------
def ensure_api_gateway(
    clients: dict, account: str, region: str, environment: str, state: dict
) -> tuple[str, str]:
    section("API Gateway")
    apigw = clients["apigw"]
    lc = clients["lambda"]
    api_lambda_arn = state["lambda"][LAMBDA_API_NAME]
    desired_tags = tag_map(environment)

    def rest_api_arn_for(api_id: str) -> str:
        return f"arn:aws:apigateway:{region}::/restapis/{api_id}"

    # Find or create REST API
    api_id = None
    pos = None
    while True:
        kwargs = {"limit": 500}
        if pos:
            kwargs["position"] = pos
        resp = apigw.get_rest_apis(**kwargs)
        for item in resp.get("items", []):
            if item["name"] == API_GATEWAY_NAME:
                api_id = item["id"]
                break
        if api_id or "position" not in resp:
            break
        pos = resp["position"]

    if not api_id:
        api = apigw.create_rest_api(
            name=API_GATEWAY_NAME,
            description=f"{PROJECT_NAME} dashboard API",
            endpointConfiguration={"types": ["REGIONAL"]},
            apiKeySource="HEADER",
            tags=desired_tags,
        )
        api_id = api["id"]
        step(f"Created REST API {API_GATEWAY_NAME}")
    else:
        step(f"Reusing REST API {API_GATEWAY_NAME}")
        # Retag — covers legacy APIs that pre-date `environment`.
        try:
            apigw.tag_resource(resourceArn=rest_api_arn_for(api_id), tags=desired_tags)
        except ClientError as e:
            warn(f"Couldn't retag REST API {api_id}: {e.response['Error'].get('Code', '?')}")

    # Find root resource
    resources = apigw.get_resources(restApiId=api_id, limit=500)["items"]
    root_id = next(r["id"] for r in resources if r["path"] == "/")
    proxy_id = next((r["id"] for r in resources if r["path"] == "/{proxy+}"), None)
    if not proxy_id:
        proxy_id = apigw.create_resource(restApiId=api_id, parentId=root_id, pathPart="{proxy+}")[
            "id"
        ]

    integration_uri = (
        f"arn:aws:apigateway:{region}:lambda:path/2015-03-31/functions/{api_lambda_arn}/invocations"
    )

    # ANY method on proxy → Lambda proxy integration
    try:
        apigw.put_method(
            restApiId=api_id,
            resourceId=proxy_id,
            httpMethod="ANY",
            authorizationType="NONE",
            apiKeyRequired=True,
            requestParameters={"method.request.path.proxy": True},
        )
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConflictException":
            raise
    apigw.put_integration(
        restApiId=api_id,
        resourceId=proxy_id,
        httpMethod="ANY",
        type="AWS_PROXY",
        integrationHttpMethod="POST",
        uri=integration_uri,
    )

    # OPTIONS method on proxy → MOCK with CORS headers
    _put_cors_mock(apigw, api_id, proxy_id)

    # Same for root (so GET /foo and OPTIONS / work without /proxy+)
    try:
        apigw.put_method(
            restApiId=api_id,
            resourceId=root_id,
            httpMethod="ANY",
            authorizationType="NONE",
            apiKeyRequired=True,
        )
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConflictException":
            raise
    apigw.put_integration(
        restApiId=api_id,
        resourceId=root_id,
        httpMethod="ANY",
        type="AWS_PROXY",
        integrationHttpMethod="POST",
        uri=integration_uri,
    )
    _put_cors_mock(apigw, api_id, root_id)

    # Lambda invoke permission for API Gateway (verified + self-healing)
    _ensure_lambda_permission(
        lc,
        LAMBDA_API_NAME,
        f"{PREFIX}-apigw-invoke",
        "apigateway.amazonaws.com",
        f"arn:aws:execute-api:{region}:{account}:{api_id}/*/*/*",
    )

    # Deploy
    apigw.create_deployment(restApiId=api_id, stageName=API_STAGE)
    step(f"Deployed stage '{API_STAGE}'")

    # API key + usage plan
    api_key_value = _ensure_api_key_and_usage_plan(apigw, api_id, region, environment, state)

    url = f"https://{api_id}.execute-api.{region}.amazonaws.com/{API_STAGE}"
    state.setdefault("api_gateway", {})
    state["api_gateway"]["rest_api_id"] = api_id
    state["api_gateway"]["url"] = url
    save_state(state)
    ok(f"API URL is {url}")
    return url, api_key_value


CORS_ALLOW_METHODS = "GET,POST,PUT,PATCH,DELETE,OPTIONS"
CORS_ALLOW_HEADERS = "Content-Type,X-Api-Key,Authorization"


def _put_cors_mock(apigw, api_id: str, resource_id: str) -> None:
    # Rebuild the OPTIONS method from scratch every run. put_method_response /
    # put_integration_response raise ConflictException if they already exist
    # *and* won't update in place — so a re-deploy must delete first, both to
    # stay idempotent and to actually apply changed CORS values.
    try:
        apigw.delete_method(restApiId=api_id, resourceId=resource_id, httpMethod="OPTIONS")
    except ClientError as e:
        if e.response["Error"]["Code"] != "NotFoundException":
            raise

    apigw.put_method(
        restApiId=api_id,
        resourceId=resource_id,
        httpMethod="OPTIONS",
        authorizationType="NONE",
        apiKeyRequired=False,
    )
    apigw.put_method_response(
        restApiId=api_id,
        resourceId=resource_id,
        httpMethod="OPTIONS",
        statusCode="200",
        responseParameters={
            "method.response.header.Access-Control-Allow-Headers": False,
            "method.response.header.Access-Control-Allow-Methods": False,
            "method.response.header.Access-Control-Allow-Origin": False,
        },
        responseModels={"application/json": "Empty"},
    )
    apigw.put_integration(
        restApiId=api_id,
        resourceId=resource_id,
        httpMethod="OPTIONS",
        type="MOCK",
        requestTemplates={"application/json": '{"statusCode": 200}'},
    )
    apigw.put_integration_response(
        restApiId=api_id,
        resourceId=resource_id,
        httpMethod="OPTIONS",
        statusCode="200",
        responseParameters={
            "method.response.header.Access-Control-Allow-Headers": f"'{CORS_ALLOW_HEADERS}'",
            "method.response.header.Access-Control-Allow-Methods": f"'{CORS_ALLOW_METHODS}'",
            "method.response.header.Access-Control-Allow-Origin": "'*'",
        },
        responseTemplates={"application/json": ""},
    )


def _ensure_api_key_and_usage_plan(
    apigw, api_id: str, region: str, environment: str, state: dict
) -> str:
    desired_tags = tag_map(environment)
    # API key
    existing_key = None
    pos = None
    while True:
        kwargs = {"includeValues": True, "limit": 500}
        if pos:
            kwargs["position"] = pos
        resp = apigw.get_api_keys(**kwargs)
        for k in resp.get("items", []):
            if k["name"] == API_KEY_NAME:
                existing_key = k
                break
        if existing_key or "position" not in resp:
            break
        pos = resp["position"]
    if existing_key:
        key_id, key_value = existing_key["id"], existing_key["value"]
        step(f"Reusing API key {API_KEY_NAME}")
        # Retag — covers legacy keys that pre-date `environment`.
        try:
            apigw.tag_resource(
                resourceArn=f"arn:aws:apigateway:{region}::/apikeys/{key_id}",
                tags=desired_tags,
            )
        except ClientError as e:
            warn(f"Couldn't retag API key {key_id}: {e.response['Error'].get('Code', '?')}")
    else:
        created = apigw.create_api_key(
            name=API_KEY_NAME,
            enabled=True,
            tags=desired_tags,
        )
        key_id, key_value = created["id"], created["value"]
        step(f"Created API key {API_KEY_NAME}")

    # Usage plan
    plan_id = None
    pos = None
    while True:
        kwargs = {"limit": 500}
        if pos:
            kwargs["position"] = pos
        resp = apigw.get_usage_plans(**kwargs)
        for p in resp.get("items", []):
            if p["name"] == USAGE_PLAN_NAME:
                plan_id = p["id"]
                break
        if plan_id or "position" not in resp:
            break
        pos = resp["position"]
    if not plan_id:
        plan = apigw.create_usage_plan(
            name=USAGE_PLAN_NAME,
            apiStages=[{"apiId": api_id, "stage": API_STAGE}],
            throttle={"burstLimit": 20, "rateLimit": 10.0},
            tags=desired_tags,
        )
        plan_id = plan["id"]
        step(f"Created usage plan {USAGE_PLAN_NAME}")
    else:
        # Retag — covers legacy plans that pre-date `environment`.
        try:
            apigw.tag_resource(
                resourceArn=f"arn:aws:apigateway:{region}::/usageplans/{plan_id}",
                tags=desired_tags,
            )
        except ClientError as e:
            warn(f"Couldn't retag usage plan {plan_id}: {e.response['Error'].get('Code', '?')}")
        try:
            apigw.update_usage_plan(
                usagePlanId=plan_id,
                patchOperations=[
                    {"op": "add", "path": "/apiStages", "value": f"{api_id}:{API_STAGE}"},
                ],
            )
        except ClientError as e:
            # Already attached
            if e.response["Error"]["Code"] not in ("ConflictException", "BadRequestException"):
                raise

    try:
        apigw.create_usage_plan_key(usagePlanId=plan_id, keyId=key_id, keyType="API_KEY")
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConflictException":
            raise

    state.setdefault("api_gateway", {})
    state["api_gateway"]["api_key_id"] = key_id
    state["api_gateway"]["usage_plan_id"] = plan_id
    save_state(state)
    return key_value


# ---------------------------------------------------------------------------
# EventBridge — daily orchestrator rule
# ---------------------------------------------------------------------------
def ensure_orchestrator_schedule(
    clients: dict, account: str, region: str, environment: str, state: dict
) -> None:
    section("EventBridge daily rule")
    events = clients["events"]
    lc = clients["lambda"]
    orch_arn = state["lambda"][LAMBDA_ORCHESTRATOR_NAME]
    desired_tags = tag_list(environment)

    events.put_rule(
        Name=ORCHESTRATOR_RULE_NAME,
        ScheduleExpression=ORCHESTRATOR_CRON,
        State="ENABLED",
        Description=f"{PROJECT_NAME} daily orchestrator trigger.",
        Tags=desired_tags,
    )
    # put_rule's Tags= is create-only — it does not retag an existing rule. Use
    # tag_resource to bring legacy rules onto both tags.
    try:
        rule_arn = events.describe_rule(Name=ORCHESTRATOR_RULE_NAME)["Arn"]
        events.tag_resource(ResourceARN=rule_arn, Tags=desired_tags)
    except ClientError as e:
        warn(
            f"Couldn't retag rule {ORCHESTRATOR_RULE_NAME}: {e.response['Error'].get('Code', '?')}"
        )
    events.put_targets(
        Rule=ORCHESTRATOR_RULE_NAME,
        Targets=[{"Id": "orchestrator", "Arn": orch_arn}],
    )
    _ensure_lambda_permission(
        lc,
        LAMBDA_ORCHESTRATOR_NAME,
        f"{PREFIX}-events-invoke",
        "events.amazonaws.com",
        f"arn:aws:events:{region}:{account}:rule/{ORCHESTRATOR_RULE_NAME}",
    )
    state.setdefault("eventbridge", {})["orchestrator_rule"] = ORCHESTRATOR_RULE_NAME
    save_state(state)
    ok(
        f"Rule {ORCHESTRATOR_RULE_NAME} fires {LAMBDA_ORCHESTRATOR_NAME} (cron: {ORCHESTRATOR_CRON})"
    )


# ---------------------------------------------------------------------------
# Frontend build + Amplify deploy
# ---------------------------------------------------------------------------
DASHBOARD_PASSWORD_ENV = "GROUNDSKEEPER_DASHBOARD_PASSWORD"


def prompt_dashboard_password(state: dict, non_interactive: bool = False) -> str:
    already_set = bool(state.get("amplify", {}).get("password_set"))

    if non_interactive:
        # Never prompt. Take the password from the environment so it never
        # lands in shell history or `ps` output. If none is given and one is
        # already set, keep the existing one.
        env_pw = os.environ.get(DASHBOARD_PASSWORD_ENV, "")
        if not env_pw:
            if already_set:
                info(f"{DASHBOARD_PASSWORD_ENV} isn't set — keeping the existing password.")
                return ""
            fail(
                f"--non-interactive needs a dashboard password. Set {DASHBOARD_PASSWORD_ENV} "
                f"(at least 8 characters), or pass --skip-frontend for an infra-only run."
            )
            sys.exit(2)
        if len(env_pw) < 8:
            fail(f"{DASHBOARD_PASSWORD_ENV} needs to be at least 8 characters.")
            sys.exit(2)
        return env_pw

    if already_set:
        if questionary.confirm("Dashboard password is already set — keep it?", default=True).ask():
            return ""  # Sentinel: don't rotate
    while True:
        p1 = questionary.password("Choose a dashboard password (8+ characters)").ask()
        if not p1 or len(p1) < 8:
            warn("That's too short — try again with at least 8 characters.")
            continue
        p2 = questionary.password("One more time, to confirm").ask()
        if p1 != p2:
            warn("Those didn't match — try again.")
            continue
        return p1


def build_frontend(api_url: str, api_key: str, region: str) -> Path | None:
    if not (FRONTEND_DIR / "package.json").exists():
        warn("Frontend isn't here yet — skipping the build.")
        return None
    node = shutil.which("node")
    npm = shutil.which("npm")
    if not (node and npm):
        warn("Can't find Node and npm — skipping the frontend build.")
        return None

    env_file = FRONTEND_DIR / ".env.production"
    env_file.write_text(
        f"VITE_API_URL={api_url}\nVITE_API_KEY={api_key}\nVITE_AWS_REGION={region}\n"
    )
    step("Wrote frontend/.env.production")

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.description}"),
        TimeElapsedColumn(),
        transient=False,
    ) as p:
        t = p.add_task("npm install …", total=None)
        subprocess.check_call(
            [npm, "install", "--silent", "--no-fund", "--no-audit"], cwd=FRONTEND_DIR
        )
        p.update(t, description="npm run build …")
        subprocess.check_call([npm, "run", "build"], cwd=FRONTEND_DIR)
        p.update(t, description="build complete")

    dist = FRONTEND_DIR / "dist"
    if not dist.exists():
        fail("The build didn't produce frontend/dist/.")
        return None
    return dist


def _zip_dist(dist: Path) -> Path:
    BUILD_DIR.mkdir(exist_ok=True)
    out = BUILD_DIR / "frontend.zip"
    if out.exists():
        out.unlink()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in dist.rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(dist))
    return out


def ensure_amplify(
    clients: dict,
    region: str,
    environment: str,
    state: dict,
    dist: Path | None,
    password: str,
) -> str | None:
    section("Amplify Hosting")
    amplify = clients["amplify"]
    desired_tags = tag_map(environment)

    # Find existing app
    app_id = state.get("amplify", {}).get("app_id")
    if not app_id:
        token = None
        while True:
            kwargs = {"maxResults": 100}
            if token:
                kwargs["nextToken"] = token
            resp = amplify.list_apps(**kwargs)
            for a in resp.get("apps", []):
                if a["name"] == AMPLIFY_APP_NAME:
                    app_id = a["appId"]
                    break
            if app_id or "nextToken" not in resp:
                break
            token = resp.get("nextToken")

    creds_b64 = (
        base64.b64encode(f"{AMPLIFY_BASIC_AUTH_USERNAME}:{password}".encode()).decode()
        if password
        else None
    )

    if not app_id:
        kwargs = dict(
            name=AMPLIFY_APP_NAME,
            description=f"{PROJECT_NAME} dashboard",
            platform="WEB",
            customRules=[AMPLIFY_SPA_REWRITE],
            enableBasicAuth=True,
            tags=desired_tags,
        )
        if creds_b64:
            kwargs["basicAuthCredentials"] = creds_b64
        app = amplify.create_app(**kwargs)
        app_id = app["app"]["appId"]
        step(f"Created Amplify app {AMPLIFY_APP_NAME}")
    else:
        step(f"Reusing Amplify app {AMPLIFY_APP_NAME}")
        update = dict(appId=app_id, customRules=[AMPLIFY_SPA_REWRITE], enableBasicAuth=True)
        if creds_b64:
            update["basicAuthCredentials"] = creds_b64
        amplify.update_app(**update)
        # Retag — covers legacy apps that pre-date `environment`.
        try:
            app_arn = amplify.get_app(appId=app_id)["app"]["appArn"]
            amplify.tag_resource(resourceArn=app_arn, tags=desired_tags)
        except ClientError as e:
            warn(f"Couldn't retag Amplify app {app_id}: {e.response['Error'].get('Code', '?')}")

    # Branch
    try:
        amplify.create_branch(
            appId=app_id,
            branchName=AMPLIFY_BRANCH_NAME,
            stage="PRODUCTION",
            enableAutoBuild=False,
            tags=desired_tags,
        )
        step(f"Created branch {AMPLIFY_BRANCH_NAME}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "BadRequestException":
            raise
        step(f"Reusing branch {AMPLIFY_BRANCH_NAME}")
        # Retag — covers legacy branches that pre-date `environment`.
        try:
            branch_arn = amplify.get_branch(appId=app_id, branchName=AMPLIFY_BRANCH_NAME)["branch"][
                "branchArn"
            ]
            amplify.tag_resource(resourceArn=branch_arn, tags=desired_tags)
        except ClientError as e2:
            warn(
                f"Couldn't retag branch {AMPLIFY_BRANCH_NAME}: "
                f"{e2.response['Error'].get('Code', '?')}"
            )

    state.setdefault("amplify", {})
    state["amplify"]["app_id"] = app_id
    state["amplify"]["branch_name"] = AMPLIFY_BRANCH_NAME
    if password:
        state["amplify"]["password_set"] = True
    save_state(state)

    dashboard_url = f"https://{AMPLIFY_BRANCH_NAME}.{app_id}.amplifyapp.com"
    state["amplify"]["url"] = dashboard_url
    save_state(state)

    if dist is None:
        warn("No build artifacts to upload — skipping the frontend deploy.")
        return dashboard_url

    zip_path = _zip_dist(dist)
    dep = amplify.create_deployment(appId=app_id, branchName=AMPLIFY_BRANCH_NAME)
    upload_url = dep["zipUploadUrl"]
    job_id = dep["jobId"]

    step(f"Uploading {zip_path.name} to Amplify …")
    with open(zip_path, "rb") as f:
        req = urllib.request.Request(upload_url, data=f.read(), method="PUT")
        req.add_header("Content-Type", "application/zip")
        try:
            # bandit B310: upload_url is an https pre-signed URL minted seconds
            # ago by amplify.create_deployment; not user-supplied or
            # attacker-influenced.
            urllib.request.urlopen(req, timeout=120)  # nosec B310
        except urllib.error.HTTPError as e:
            fail(f"Upload failed: {e.code} {e.read().decode(errors='ignore')}")
            raise

    amplify.start_deployment(appId=app_id, branchName=AMPLIFY_BRANCH_NAME, jobId=job_id)
    step(f"Started deployment job {job_id}")

    with Progress(
        SpinnerColumn(), TextColumn("[bold cyan]Amplify deployment"), TimeElapsedColumn()
    ) as p:
        t = p.add_task("running", total=None)
        terminal = {"SUCCEED", "FAILED", "CANCELLED"}
        while True:
            job = amplify.get_job(appId=app_id, branchName=AMPLIFY_BRANCH_NAME, jobId=job_id)
            status = job["job"]["summary"]["status"]
            p.update(t, description=f"status: {status}")
            if status in terminal:
                break
            time.sleep(4)
    if status != "SUCCEED":
        fail(f"Amplify deployment finished as {status}. Check the Amplify console for details.")
    else:
        ok(f"Dashboard is live at {dashboard_url}")
    return dashboard_url


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
def print_summary(state: dict, environment: str, dashboard_url: str | None) -> None:
    section("Summary")
    t = Table(show_header=False, box=None, padding=(0, 2))
    t.add_column(style="dim")
    t.add_column()
    t.add_row("Account", state.get("account_id", "?"))
    t.add_row("Region", state.get("region", "?"))
    t.add_row("Environment", environment)
    t.add_row("DynamoDB table", state.get("dynamodb_table", "—"))
    t.add_row("S3 bucket", state.get("s3_artifacts_bucket", "—"))
    t.add_row("Orchestrator", state.get("lambda", {}).get(LAMBDA_ORCHESTRATOR_NAME, "—"))
    t.add_row("Executor", state.get("lambda", {}).get(LAMBDA_EXECUTOR_NAME, "—"))
    t.add_row("API Lambda", state.get("lambda", {}).get(LAMBDA_API_NAME, "—"))
    t.add_row("API URL", state.get("api_gateway", {}).get("url", "—"))
    t.add_row("Cron", ORCHESTRATOR_CRON)
    t.add_row("Dashboard", dashboard_url or "—")
    console.print(t)
    console.print()
    console.print(
        Panel(
            "[bold]Next steps[/bold]\n"
            f"  1. Open the dashboard URL above — that's where the rest happens.\n"
            f"  2. Log in with username [bold]{AMPLIFY_BASIC_AUTH_USERNAME}[/bold] and the password you chose.\n"
            f"  3. Paste your GitHub token (scope: [bold]repo[/bold]) — the app verifies it and stores it in Secrets Manager.\n"
            f"  4. Pick a repo, sculpt your schedule, and set the commit style.\n\n"
            f"  Resource state lives in [bold]{STATE_FILE.name}[/bold] — hold onto it for [bold]teardown.py[/bold].",
            border_style="green",
            padding=(1, 2),
        )
    )


# ---------------------------------------------------------------------------
# OIDC bootstrap mode (--bootstrap-oidc) — separate one-shot.
# ---------------------------------------------------------------------------
def _parse_repo_slug(slug: str) -> tuple[str, str]:
    """Validate and split an `owner/repo` GitHub slug."""
    parts = slug.strip().split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise argparse.ArgumentTypeError(f"--bootstrap-oidc expects OWNER/REPO (got {slug!r}).")
    owner, repo = parts
    # GitHub repo + owner names use [A-Za-z0-9._-]; we don't try to be exhaustive
    # here — just catch obvious mistakes like embedded spaces or empty segments.
    bad = set(" \t\n\r")
    if bad & set(owner) or bad & set(repo):
        raise argparse.ArgumentTypeError(
            f"--bootstrap-oidc OWNER/REPO can't contain whitespace (got {slug!r})."
        )
    return owner, repo


def _oidc_provider_arn(account: str) -> str:
    return f"arn:aws:iam::{account}:oidc-provider/{GITHUB_OIDC_HOST}"


def _ensure_oidc_provider(iam, account: str) -> tuple[str, bool]:
    """Idempotently create the GitHub OIDC provider. Returns (arn, created)."""
    desired_arn = _oidc_provider_arn(account)
    existing = iam.list_open_id_connect_providers().get("OpenIDConnectProviderList", [])
    for entry in existing:
        if entry.get("Arn") == desired_arn:
            step(f"Reusing OIDC provider {GITHUB_OIDC_HOST}")
            return desired_arn, False
    resp = iam.create_open_id_connect_provider(
        Url=GITHUB_OIDC_URL,
        ClientIDList=[GITHUB_OIDC_AUDIENCE],
        ThumbprintList=[GITHUB_OIDC_THUMBPRINT],
    )
    step(f"Created OIDC provider {GITHUB_OIDC_HOST}")
    return resp["OpenIDConnectProviderArn"], True


def _ci_deploy_trust_policy(account: str, owner: str, repo: str, branch: str) -> dict:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Federated": _oidc_provider_arn(account)},
                "Action": "sts:AssumeRoleWithWebIdentity",
                "Condition": {
                    "StringEquals": {
                        f"{GITHUB_OIDC_HOST}:aud": GITHUB_OIDC_AUDIENCE,
                    },
                    "StringLike": {
                        f"{GITHUB_OIDC_HOST}:sub": (f"repo:{owner}/{repo}:ref:refs/heads/{branch}"),
                    },
                },
            }
        ],
    }


def _ensure_ci_deploy_role(
    iam, role_name: str, trust_policy: dict, environment: str
) -> tuple[str, bool]:
    """Create or update the CI deploy role. Returns (arn, created)."""
    desired_tags = tag_list(environment)
    trust_doc = json.dumps(trust_policy)
    created = False
    try:
        iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=trust_doc,
            Description=(
                f"{PROJECT_NAME} CI deploy role — assumed by GitHub Actions "
                f"via OIDC for the {environment} environment."
            ),
            Tags=desired_tags,
        )
        step(f"Created role {role_name}")
        created = True
    except ClientError as e:
        if e.response["Error"]["Code"] != "EntityAlreadyExists":
            raise
        step(f"Reusing role {role_name}")
        iam.update_assume_role_policy(RoleName=role_name, PolicyDocument=trust_doc)
        iam.tag_role(RoleName=role_name, Tags=desired_tags)

    # AdministratorAccess attach is idempotent — re-attaching the same managed
    # policy is a no-op and does not raise.
    iam.attach_role_policy(RoleName=role_name, PolicyArn=CI_DEPLOY_ROLE_POLICY_ARN)
    role_arn = iam.get_role(RoleName=role_name)["Role"]["Arn"]
    return role_arn, created


def _print_oidc_summary(
    account: str,
    region: str,
    environment: str,
    owner: str,
    repo: str,
    branch: str,
    role_name: str,
    role_arn: str,
    provider_arn: str,
) -> None:
    section("Summary")
    t = Table(show_header=False, box=None, padding=(0, 2))
    t.add_column(style="dim")
    t.add_column()
    t.add_row("Account", account)
    t.add_row("Region", region)
    t.add_row("Environment", environment)
    t.add_row("Repo", f"{owner}/{repo}")
    t.add_row("Trusted branch", f"refs/heads/{branch}")
    t.add_row("OIDC provider ARN", provider_arn)
    t.add_row("Role name", role_name)
    t.add_row("Role ARN", role_arn)
    t.add_row("Managed policy", CI_DEPLOY_ROLE_POLICY_ARN)
    console.print(t)
    secret_name = GITHUB_SECRET_NAME[environment]
    console.print()
    console.print(
        Panel(
            "[bold]Next step[/bold]\n"
            f"  In the [bold]{owner}/{repo}[/bold] GitHub repo settings, "
            f"add the role ARN as a repo secret named:\n"
            f"    [bold]{secret_name}[/bold] = {role_arn}\n\n"
            f"  See [bold]RELEASE_CHECKLIST.md[/bold] → "
            f"'CI deploy bootstrap' → step C for the full secret table.",
            border_style="green",
            padding=(1, 2),
        )
    )


def bootstrap_oidc(args: argparse.Namespace) -> None:
    """`--bootstrap-oidc` entry point. Sets up GitHub OIDC + the CI deploy role
    in the current AWS account. Stays entirely off the regular deploy path —
    no state file, no Lambda/DDB/API touch."""
    owner, repo = _parse_repo_slug(args.bootstrap_oidc)
    environment: str = args.environment
    branch = ENVIRONMENT_BRANCH[environment]
    role_name = args.role_name or DEFAULT_CI_DEPLOY_ROLE_NAME

    banner(
        f"{PROJECT_NAME} OIDC bootstrap",
        f"Wires GitHub Actions for [bold]{owner}/{repo}[/bold] "
        f"(branch [bold]{branch}[/bold]) into the current AWS account so "
        f"the [bold]{environment}[/bold] CI workflow can deploy.",
    )

    account, region, _caller = confirm_account_and_region(
        args.region,
        expected_account=args.account,
        non_interactive=args.yes,
    )

    section("Plan")
    rows = [
        (
            "OIDC provider",
            GITHUB_OIDC_HOST,
            "Lets GitHub Actions vend short-lived AWS credentials. "
            "Idempotent — reused if already present.",
        ),
        (
            "IAM role",
            role_name,
            f"Trusts {owner}/{repo}@refs/heads/{branch} via OIDC. "
            f"Attaches {CI_DEPLOY_ROLE_POLICY_ARN}.",
        ),
    ]
    plan = Table(show_lines=False, padding=(0, 2))
    plan.add_column("Kind", style="cyan", no_wrap=True)
    plan.add_column("Name", style="bold")
    plan.add_column("Why")
    for k, n, w in rows:
        plan.add_row(k, n, w)
    console.print(plan)
    console.print(
        f"\n  Role tags: [bold]{PROJECT_TAG_KEY}={PROJECT_TAG_VALUE}[/bold] + "
        f"[bold]{ENVIRONMENT_TAG_KEY}={environment}[/bold]."
    )

    if not args.yes:
        if not questionary.confirm(
            "Create / update these IAM resources in this account?", default=True
        ).ask():
            console.print("[dim]Aborted.[/dim]")
            sys.exit(0)

    section("IAM")
    iam = boto3.session.Session(region_name=region).client(
        "iam",
        config=BotoConfig(retries={"max_attempts": 10, "mode": "adaptive"}),
    )

    provider_arn, _ = _ensure_oidc_provider(iam, account)
    trust_policy = _ci_deploy_trust_policy(account, owner, repo, branch)
    role_arn, _ = _ensure_ci_deploy_role(iam, role_name, trust_policy, environment)
    ok(f"{role_name} ready.")

    _print_oidc_summary(
        account,
        region,
        environment,
        owner,
        repo,
        branch,
        role_name,
        role_arn,
        provider_arn,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description=f"Deploy {PROJECT_NAME} to AWS.")
    parser.add_argument(
        "--region", help="Override AWS region (otherwise: state file → env → prompt)."
    )
    parser.add_argument("--yes", action="store_true", help="Skip IAM policy preview confirmation.")
    parser.add_argument(
        "--skip-frontend", action="store_true", help="Skip the npm build / Amplify deploy step."
    )
    parser.add_argument(
        "--account",
        help=(
            "Assert the target AWS account ID. Aborts before creating anything "
            "if the resolved credentials don't match. Required with --non-interactive."
        ),
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help=(
            "Never prompt. Requires --account and --region (or AWS_REGION). "
            f"Dashboard password is read from ${DASHBOARD_PASSWORD_ENV}."
        ),
    )
    parser.add_argument(
        "--environment",
        choices=list(ENVIRONMENT_CHOICES),
        default=DEFAULT_ENVIRONMENT,
        help=(
            "Environment to tag resources with — written as `environment=<env>` "
            "alongside `project=groundskeeper`. Used by teardown.py to scope "
            "discovery to a single environment. Resource names are unchanged. "
            "With --bootstrap-oidc, controls which branch the deploy role "
            "trusts (dev→refs/heads/dev, prod→refs/heads/main)."
        ),
    )
    parser.add_argument(
        "--bootstrap-oidc",
        metavar="OWNER/REPO",
        help=(
            "One-shot mode: create the GitHub OIDC provider + the per-account "
            "CI deploy role in the current AWS account. Mutually exclusive "
            "with the regular deploy — no Lambda/DDB/API resources are touched."
        ),
    )
    parser.add_argument(
        "--role-name",
        help=(
            f"With --bootstrap-oidc, name of the IAM role to create / update "
            f"(default: {DEFAULT_CI_DEPLOY_ROLE_NAME}). Ignored otherwise."
        ),
    )
    args = parser.parse_args()
    environment: str = args.environment

    # OIDC bootstrap is its own mode — branch off before any regular deploy
    # work runs. It does not touch deploy state and does not create any
    # Lambda/DDB/API resources.
    if args.bootstrap_oidc:
        if args.skip_frontend or args.non_interactive:
            sys.stderr.write(
                "ERROR: --bootstrap-oidc is a separate one-shot mode; it does "
                "not accept --skip-frontend or --non-interactive. Pass --yes "
                "to skip the confirmation prompt.\n"
            )
            sys.exit(2)
        bootstrap_oidc(args)
        return

    if args.role_name:
        sys.stderr.write("ERROR: --role-name only applies with --bootstrap-oidc.\n")
        sys.exit(2)

    if args.non_interactive and not args.account:
        sys.stderr.write(
            "ERROR: --non-interactive requires --account <id> as an explicit "
            "target-account guard.\n"
        )
        sys.exit(2)

    banner(
        f"{PROJECT_NAME} Deploy",
        f"Environment: [bold]{environment}[/bold] — "
        "walks you through every AWS resource we'll create. Safe to re-run anytime.",
    )
    # Migration note: existing deploys (project=groundskeeper only) will be
    # retagged on the same create/update calls — no manual fix-up needed.
    info(f"Re-tagging existing resources with environment={environment}")

    state = load_state()
    initial_region = args.region or state.get("region")
    account, region, caller = confirm_account_and_region(
        initial_region,
        expected_account=args.account,
        non_interactive=args.non_interactive,
    )
    state["account_id"] = account
    state["region"] = region
    state["caller"] = caller
    state["environment"] = environment
    save_state(state)

    preflight_tooling()
    show_deployment_plan(account, region, environment, non_interactive=args.non_interactive)

    clients = make_clients(region)
    warn_about_bedrock(clients)

    # Re-runs: pull the Amplify app id from state so the first-pass policy is
    # already narrowed. On the very first deploy this is None and the
    # AmplifyPasswordRotation statement is added by the post-amplify pass.
    known_amplify_app_id = state.get("amplify", {}).get("app_id")
    ensure_iam_roles(
        clients,
        account,
        region,
        environment,
        state,
        yes=args.yes,
        amplify_app_id=known_amplify_app_id,
    )
    ensure_dynamodb(clients, environment, state)
    ensure_secrets(clients, environment, state)
    bucket = ensure_artifacts_bucket(clients, account, region, environment, state)
    ensure_lambdas(clients, account, region, environment, state, bucket)
    api_url, api_key = ensure_api_gateway(clients, account, region, environment, state)
    ensure_orchestrator_schedule(clients, account, region, environment, state)

    dashboard_url = None
    if not args.skip_frontend:
        password = prompt_dashboard_password(state, non_interactive=args.non_interactive)
        dist = build_frontend(api_url, api_key, region)
        dashboard_url = ensure_amplify(clients, region, environment, state, dist, password)
        # Now that the Amplify app id is known, tighten amplify:UpdateApp from
        # apps/* down to this single app's ARN.
        amplify_app_id = state.get("amplify", {}).get("app_id")
        if amplify_app_id:
            refine_lambda_role_for_amplify(clients, account, region, amplify_app_id)
    else:
        warn("Skipping the frontend deploy (--skip-frontend).")
        # Even when the frontend deploy is skipped, fold in the narrowed
        # AmplifyPasswordRotation statement if we already know the app id from
        # a prior run — otherwise leave the statement out entirely.
        amplify_app_id = state.get("amplify", {}).get("app_id")
        if amplify_app_id:
            refine_lambda_role_for_amplify(clients, account, region, amplify_app_id)

    print_summary(state, environment, dashboard_url)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[dim]Interrupted.[/dim]")
        sys.exit(130)
    except ClientError as e:
        console.print()
        fail(
            f"AWS returned an error: {e.response['Error'].get('Code', '?')} — {e.response['Error'].get('Message', e)}"
        )
        sys.exit(1)
