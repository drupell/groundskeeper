#!/usr/bin/env python3
"""
Groundskeeper — guided AWS teardown.

The mirror image of ``deploy.py``: reads ``.groundskeeper-deploy-state.json``
to find every resource the deploy created, falls back to name-based discovery
for anything missing from state, shows you the full list, asks you to confirm,
and then deletes everything. Resource names match ``deploy.py`` exactly — if
you change one there, change it here too.

Re-runs are safe: each delete step swallows ``NotFound`` / ``NoSuchEntity`` so
a partial teardown can be resumed without errors.

Prerequisites:
  - Python 3.10+
  - AWS CLI configured (any of: env vars, ~/.aws/credentials, SSO)
"""

# ---------------------------------------------------------------------------
# BOOTSTRAP — stdlib only above this line. Third-party imports come after the
# venv re-exec below.
# ---------------------------------------------------------------------------
import json
import os
import subprocess
import sys
import venv
from pathlib import Path

PROJECT_NAME = "Groundskeeper"
PROJECT_TAG_KEY = "project"
PROJECT_TAG_VALUE = "groundskeeper"

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv-deploy"
REQUIREMENTS = ROOT / "requirements-deploy.txt"
STATE_FILE = ROOT / ".groundskeeper-deploy-state.json"

PY_MIN = (3, 10)


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
    if not VENV_DIR.exists():
        print(f"[bootstrap] Creating venv at {VENV_DIR.relative_to(ROOT)} …")
        venv.EnvBuilder(with_pip=True, clear=False, upgrade_deps=False).create(str(VENV_DIR))
    py = _venv_python()
    if not py.exists():
        sys.stderr.write(f"ERROR: venv python not found at {py}\n")
        sys.exit(1)
    print("[bootstrap] Installing teardown dependencies …")
    subprocess.check_call(
        [
            str(py),
            "-m",
            "pip",
            "install",
            "--quiet",
            "--disable-pip-version-check",
            "-r",
            str(REQUIREMENTS),
        ]
    )
    os.execv(str(py), [str(py), __file__, *sys.argv[1:]])


if not _running_in_venv():
    _bootstrap_and_reexec()


# ---------------------------------------------------------------------------
# IMPORTS available once running inside the venv
# ---------------------------------------------------------------------------
import argparse
import time

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
# RESOURCE NAMES — must match deploy.py exactly.
# ---------------------------------------------------------------------------
PREFIX = "groundskeeper"

LAMBDA_ROLE_NAME = f"{PREFIX}-lambda-role"
SCHEDULER_ROLE_NAME = f"{PREFIX}-scheduler-role"

DDB_TABLE_NAME = f"{PREFIX}-config"
PAT_SECRET_NAME = f"{PREFIX}/github-pat"

LAMBDA_NAMES = (
    f"{PREFIX}-orchestrator",
    f"{PREFIX}-executor",
    f"{PREFIX}-api",
)

ORCHESTRATOR_RULE_NAME = f"{PREFIX}-orchestrator-daily"
API_GATEWAY_NAME = f"{PREFIX}-api"
API_KEY_NAME = f"{PREFIX}-api-key"
USAGE_PLAN_NAME = f"{PREFIX}-usage-plan"

AMPLIFY_APP_NAME = f"{PREFIX}-dashboard"

# Orchestrator creates one-time schedules under these prefixes. We match both
# the inline orchestrator prefix and the helper-module prefix to be safe.
SCHEDULE_GROUP = "default"
SCHEDULE_PREFIXES = (f"{PREFIX}-commit-", f"{PREFIX}-exec-")

LOG_GROUP_PREFIX = f"/aws/lambda/{PREFIX}-"


# ---------------------------------------------------------------------------
# STATE
# ---------------------------------------------------------------------------
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            warn(f"{STATE_FILE.name} is unreadable — falling back to discovery.")
    return {}


# ---------------------------------------------------------------------------
# OUTPUT HELPERS — kept aligned with deploy.py's styling.
# ---------------------------------------------------------------------------
def banner(title: str, subtitle: str = "") -> None:
    body = f"[bold]{title}[/bold]"
    if subtitle:
        body += f"\n[dim]{subtitle}[/dim]"
    console.print(Panel(body, border_style="red", padding=(1, 2)))


def section(title: str) -> None:
    console.print()
    console.rule(f"[bold red]{title}[/bold red]", style="red")


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


def _is_not_found(exc: ClientError) -> bool:
    # Deliberately strict: BadRequestException is NOT here. Many APIs throw it
    # for genuine problems, and treating it as "already gone" would silently
    # skip a still-billable resource. Amplify's gone-app BadRequestException is
    # handled locally in delete_amplify_apps instead.
    code = exc.response["Error"].get("Code", "")
    return code in {
        "ResourceNotFoundException",
        "NoSuchEntity",
        "NoSuchBucket",
        "NotFoundException",
    }


# ---------------------------------------------------------------------------
# Account / region confirmation (mirrors deploy.py)
# ---------------------------------------------------------------------------
def confirm_account_and_region(
    state_region: str | None,
    expected_account: str | None = None,
    non_interactive: bool = False,
) -> tuple[str, str, dict]:
    region = (
        state_region
        or os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or "us-east-1"
    )
    cfg = BotoConfig(retries={"max_attempts": 10, "mode": "adaptive"}, region_name=region)
    sts = boto3.client("sts", config=cfg)
    try:
        caller = sts.get_caller_identity()
    except NoCredentialsError:
        fail(
            "No AWS credentials found. Run `aws configure` or set "
            "AWS_PROFILE / AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY."
        )
        sys.exit(1)
    except ClientError as e:
        fail(f"AWS credential check failed: {e}")
        sys.exit(1)
    account = caller["Account"]
    arn = caller["Arn"]

    # Hard guard for a DESTRUCTIVE script: if a target account was asserted,
    # the resolved identity MUST match it exactly — abort before discovering
    # or deleting anything. This makes "wrong profile + reflexive yes" (e.g.
    # nuking prod when you meant dev) impossible rather than merely unlikely.
    if expected_account and account != expected_account:
        fail(
            f"Account guard failed: credentials resolve to {account} but "
            f"--account asserted {expected_account}. Aborting before any "
            f"resource is touched."
        )
        sys.exit(1)

    t = Table(show_header=False, box=None, padding=(0, 2))
    t.add_column(style="dim")
    t.add_column()
    t.add_row("Account", account)
    t.add_row("Region", region)
    t.add_row("Caller", arn)
    console.print(t)

    if non_interactive:
        ok(f"Account {account} matches --account guard. Proceeding non-interactively.")
        return account, region, caller

    if not questionary.confirm(
        f"Tear down {PROJECT_NAME} in this account/region?", default=False
    ).ask():
        sys.exit(0)
    return account, region, caller


# ---------------------------------------------------------------------------
# AWS clients
# ---------------------------------------------------------------------------
def make_clients(region: str) -> dict:
    cfg = BotoConfig(retries={"max_attempts": 10, "mode": "adaptive"}, region_name=region)
    session = boto3.session.Session(region_name=region)
    return {
        "iam": session.client("iam", config=cfg),
        "dynamodb": session.client("dynamodb", config=cfg),
        "secrets": session.client("secretsmanager", config=cfg),
        "s3": session.client("s3", config=cfg),
        "s3_resource": boto3.resource("s3", config=cfg),
        "lambda": session.client("lambda", config=cfg),
        "apigw": session.client("apigateway", config=cfg),
        "events": session.client("events", config=cfg),
        "scheduler": session.client("scheduler", config=cfg),
        "amplify": session.client("amplify", config=cfg),
        "logs": session.client("logs", config=cfg),
    }


# ---------------------------------------------------------------------------
# Discovery — figure out what to delete using state file + name fallback.
# ---------------------------------------------------------------------------
def discover(clients: dict, state: dict, account: str, region: str) -> dict[str, list[str]]:
    """Return a dict of resources we plan to delete, keyed by category."""
    section("Discovery")

    inventory: dict[str, list[str]] = {
        "lambda_functions": [],
        "api_gateway_ids": [],
        "api_keys": [],
        "usage_plans": [],
        "eventbridge_rules": [],
        "scheduler_schedules": [],
        "dynamodb_tables": [],
        "secrets": [],
        "s3_buckets": [],
        "iam_roles": [],
        "amplify_apps": [],
        "log_groups": [],
    }

    # Lambda
    for name in LAMBDA_NAMES:
        try:
            clients["lambda"].get_function(FunctionName=name)
            inventory["lambda_functions"].append(name)
        except ClientError as e:
            if not _is_not_found(e):
                raise

    # API Gateway — state file first, then name-based fallback
    apigw_state = state.get("api_gateway", {})
    api_id = apigw_state.get("rest_api_id")
    if api_id:
        try:
            clients["apigw"].get_rest_api(restApiId=api_id)
            inventory["api_gateway_ids"].append(api_id)
        except ClientError as e:
            if not _is_not_found(e):
                raise
    if not inventory["api_gateway_ids"]:
        pos: str | None = None
        while True:
            kwargs: dict = {"limit": 500}
            if pos:
                kwargs["position"] = pos
            resp = clients["apigw"].get_rest_apis(**kwargs)
            for item in resp.get("items", []):
                if item["name"] == API_GATEWAY_NAME:
                    inventory["api_gateway_ids"].append(item["id"])
            pos = resp.get("position")
            if not pos:
                break

    # API key
    pos = None
    while True:
        kwargs = {"limit": 500, "includeValues": False}
        if pos:
            kwargs["position"] = pos
        resp = clients["apigw"].get_api_keys(**kwargs)
        for k in resp.get("items", []):
            if k["name"] == API_KEY_NAME:
                inventory["api_keys"].append(k["id"])
        pos = resp.get("position")
        if not pos:
            break

    # Usage plan
    pos = None
    while True:
        kwargs = {"limit": 500}
        if pos:
            kwargs["position"] = pos
        resp = clients["apigw"].get_usage_plans(**kwargs)
        for p in resp.get("items", []):
            if p["name"] == USAGE_PLAN_NAME:
                inventory["usage_plans"].append(p["id"])
        pos = resp.get("position")
        if not pos:
            break

    # EventBridge daily rule
    try:
        clients["events"].describe_rule(Name=ORCHESTRATOR_RULE_NAME)
        inventory["eventbridge_rules"].append(ORCHESTRATOR_RULE_NAME)
    except ClientError as e:
        if not _is_not_found(e):
            raise

    # EventBridge Scheduler one-time schedules
    for prefix in SCHEDULE_PREFIXES:
        next_token: str | None = None
        while True:
            kwargs = {
                "GroupName": SCHEDULE_GROUP,
                "NamePrefix": prefix,
                "MaxResults": 100,
            }
            if next_token:
                kwargs["NextToken"] = next_token
            try:
                resp = clients["scheduler"].list_schedules(**kwargs)
            except ClientError as e:
                if _is_not_found(e):
                    break
                raise
            for s in resp.get("Schedules", []):
                inventory["scheduler_schedules"].append(s["Name"])
            next_token = resp.get("NextToken")
            if not next_token:
                break

    # DynamoDB
    try:
        clients["dynamodb"].describe_table(TableName=DDB_TABLE_NAME)
        inventory["dynamodb_tables"].append(DDB_TABLE_NAME)
    except ClientError as e:
        if not _is_not_found(e):
            raise

    # Secrets
    try:
        clients["secrets"].describe_secret(SecretId=PAT_SECRET_NAME)
        inventory["secrets"].append(PAT_SECRET_NAME)
    except ClientError as e:
        if not _is_not_found(e):
            raise

    # S3 artifacts bucket
    bucket_name = state.get("s3_artifacts_bucket") or f"{PREFIX}-artifacts-{account}-{region}"
    try:
        clients["s3"].head_bucket(Bucket=bucket_name)
        inventory["s3_buckets"].append(bucket_name)
    except ClientError as e:
        if not _is_not_found(e) and e.response["Error"].get("Code") != "404":
            raise

    # IAM
    for role in (LAMBDA_ROLE_NAME, SCHEDULER_ROLE_NAME):
        try:
            clients["iam"].get_role(RoleName=role)
            inventory["iam_roles"].append(role)
        except ClientError as e:
            if not _is_not_found(e):
                raise

    # Amplify
    next_token = None
    while True:
        kwargs = {"maxResults": 100}
        if next_token:
            kwargs["nextToken"] = next_token
        resp = clients["amplify"].list_apps(**kwargs)
        for a in resp.get("apps", []):
            if a["name"] == AMPLIFY_APP_NAME:
                inventory["amplify_apps"].append(a["appId"])
        next_token = resp.get("nextToken")
        if not next_token:
            break

    # CloudWatch log groups
    next_token = None
    while True:
        kwargs = {"logGroupNamePrefix": LOG_GROUP_PREFIX, "limit": 50}
        if next_token:
            kwargs["nextToken"] = next_token
        resp = clients["logs"].describe_log_groups(**kwargs)
        for g in resp.get("logGroups", []):
            inventory["log_groups"].append(g["logGroupName"])
        next_token = resp.get("nextToken")
        if not next_token:
            break

    _print_inventory(inventory)
    return inventory


def _print_inventory(inventory: dict[str, list[str]]) -> None:
    t = Table(show_header=True, header_style="bold red", padding=(0, 1))
    t.add_column("Category")
    t.add_column("Items")
    labels = {
        "lambda_functions": "Lambda functions",
        "api_gateway_ids": "API Gateway REST APIs",
        "api_keys": "API keys",
        "usage_plans": "Usage plans",
        "eventbridge_rules": "EventBridge rules",
        "scheduler_schedules": "Scheduler one-time schedules",
        "dynamodb_tables": "DynamoDB tables",
        "secrets": "Secrets Manager secrets",
        "s3_buckets": "S3 buckets",
        "iam_roles": "IAM roles",
        "amplify_apps": "Amplify apps",
        "log_groups": "CloudWatch log groups",
    }
    any_found = False
    for key, label in labels.items():
        items = inventory.get(key) or []
        if not items:
            continue
        any_found = True
        display = "\n".join(items) if len(items) <= 10 else f"{len(items)} items"
        t.add_row(label, display)
    if not any_found:
        console.print("  [green]✓[/green] Nothing left to delete — already torn down.")
    else:
        console.print(t)


# ---------------------------------------------------------------------------
# Per-resource deletion
# ---------------------------------------------------------------------------
def delete_eventbridge_rules(clients: dict, names: list[str]) -> None:
    if not names:
        return
    section("EventBridge daily rules")
    events = clients["events"]
    for name in names:
        try:
            target_ids = [
                t["Id"] for t in events.list_targets_by_rule(Rule=name).get("Targets", [])
            ]
            if target_ids:
                events.remove_targets(Rule=name, Ids=target_ids)
            events.delete_rule(Name=name)
            ok(f"Deleted rule {name}")
        except ClientError as e:
            if _is_not_found(e):
                info(f"Rule {name} already gone")
            else:
                fail(f"Could not delete rule {name}: {e}")


def delete_scheduler_schedules(clients: dict, names: list[str]) -> None:
    if not names:
        return
    section("Scheduler one-time schedules")
    scheduler = clients["scheduler"]
    deleted = 0
    for name in names:
        try:
            scheduler.delete_schedule(Name=name, GroupName=SCHEDULE_GROUP)
            deleted += 1
        except ClientError as e:
            if not _is_not_found(e):
                fail(f"Could not delete schedule {name}: {e}")
    ok(f"Removed {deleted} schedule(s)")


def delete_lambdas(clients: dict, names: list[str]) -> None:
    if not names:
        return
    section("Lambda functions")
    lc = clients["lambda"]
    for name in names:
        try:
            lc.delete_function(FunctionName=name)
            ok(f"Deleted {name}")
        except ClientError as e:
            if _is_not_found(e):
                info(f"{name} already gone")
            else:
                fail(f"Could not delete {name}: {e}")


def delete_api_gateway(
    clients: dict,
    api_ids: list[str],
    api_key_ids: list[str],
    usage_plan_ids: list[str],
) -> None:
    if not (api_ids or api_key_ids or usage_plan_ids):
        return
    section("API Gateway")
    apigw = clients["apigw"]

    # Usage plans must be detached from APIs and keys before deletion.
    for plan_id in usage_plan_ids:
        try:
            # A usage plan with attached apiStages cannot be deleted — AWS
            # requires detaching every stage first. (Detaching keys alone,
            # which the old code did, is not enough and delete_usage_plan
            # then fails, orphaning the plan.)
            try:
                plan = apigw.get_usage_plan(usagePlanId=plan_id)
                for stage in plan.get("apiStages", []):
                    val = f"{stage['apiId']}:{stage['stage']}"
                    try:
                        apigw.update_usage_plan(
                            usagePlanId=plan_id,
                            patchOperations=[{"op": "remove", "path": "/apiStages", "value": val}],
                        )
                    except ClientError as e:
                        if not _is_not_found(e):
                            warn(f"Could not detach stage {val} from plan {plan_id}: {e}")
            except ClientError as e:
                if not _is_not_found(e):
                    warn(f"Could not read usage plan {plan_id} stages: {e}")

            keys_resp = apigw.get_usage_plan_keys(usagePlanId=plan_id, limit=500)
            for k in keys_resp.get("items", []):
                try:
                    apigw.delete_usage_plan_key(usagePlanId=plan_id, keyId=k["id"])
                except ClientError as e:
                    if not _is_not_found(e):
                        warn(f"Could not detach key {k['id']} from plan {plan_id}: {e}")
            apigw.delete_usage_plan(usagePlanId=plan_id)
            ok(f"Deleted usage plan {plan_id}")
        except ClientError as e:
            if _is_not_found(e):
                info(f"Usage plan {plan_id} already gone")
            else:
                fail(f"Could not delete usage plan {plan_id}: {e}")

    for key_id in api_key_ids:
        try:
            apigw.delete_api_key(apiKey=key_id)
            ok(f"Deleted API key {key_id}")
        except ClientError as e:
            if _is_not_found(e):
                info(f"API key {key_id} already gone")
            else:
                fail(f"Could not delete API key {key_id}: {e}")

    for api_id in api_ids:
        try:
            apigw.delete_rest_api(restApiId=api_id)
            ok(f"Deleted REST API {api_id}")
        except ClientError as e:
            if _is_not_found(e):
                info(f"REST API {api_id} already gone")
            elif e.response["Error"].get("Code") == "TooManyRequestsException":
                warn("Hit API Gateway rate limit — pausing 30s and retrying.")
                time.sleep(30)
                try:
                    apigw.delete_rest_api(restApiId=api_id)
                    ok(f"Deleted REST API {api_id}")
                except ClientError as e2:
                    fail(f"Could not delete REST API {api_id}: {e2}")
            else:
                fail(f"Could not delete REST API {api_id}: {e}")


def delete_dynamodb(clients: dict, names: list[str]) -> None:
    if not names:
        return
    section("DynamoDB")
    ddb = clients["dynamodb"]
    for name in names:
        try:
            ddb.delete_table(TableName=name)
            step(f"Deleting table {name} …")
        except ClientError as e:
            if _is_not_found(e):
                info(f"Table {name} already gone")
                continue
            fail(f"Could not delete {name}: {e}")
            continue
        waiter = ddb.get_waiter("table_not_exists")
        try:
            waiter.wait(TableName=name, WaiterConfig={"Delay": 3, "MaxAttempts": 60})
        except Exception as e:
            warn(f"Waiter timed out for {name}: {e}")
        ok(f"Deleted table {name}")


def delete_secrets(clients: dict, names: list[str], force: bool) -> None:
    if not names:
        return
    section("Secrets Manager")
    sm = clients["secrets"]
    for name in names:
        try:
            kwargs: dict = {"SecretId": name}
            if force:
                kwargs["ForceDeleteWithoutRecovery"] = True
            else:
                kwargs["RecoveryWindowInDays"] = 7
            sm.delete_secret(**kwargs)
            verb = "Force-deleted" if force else "Scheduled deletion of"
            ok(f"{verb} {name}")
        except ClientError as e:
            if _is_not_found(e):
                info(f"Secret {name} already gone")
            else:
                fail(f"Could not delete secret {name}: {e}")


def delete_s3_buckets(clients: dict, buckets: list[str]) -> None:
    if not buckets:
        return
    section("S3 artifacts")
    s3_resource = clients["s3_resource"]
    s3 = clients["s3"]
    for bucket_name in buckets:
        try:
            bucket = s3_resource.Bucket(bucket_name)
            with Progress(
                SpinnerColumn(),
                TextColumn(f"[bold cyan]Emptying {bucket_name}"),
                TimeElapsedColumn(),
                transient=True,
            ) as p:
                t = p.add_task("running", total=None)
                bucket.object_versions.all().delete()
                bucket.objects.all().delete()
                p.update(t, description="emptied")
            s3.delete_bucket(Bucket=bucket_name)
            ok(f"Deleted bucket {bucket_name}")
        except ClientError as e:
            if _is_not_found(e):
                info(f"Bucket {bucket_name} already gone")
            else:
                fail(f"Could not delete bucket {bucket_name}: {e}")


def delete_iam_roles(clients: dict, roles: list[str]) -> None:
    if not roles:
        return
    section("IAM roles")
    iam = clients["iam"]
    for role in roles:
        try:
            for p in iam.list_attached_role_policies(RoleName=role).get("AttachedPolicies", []):
                iam.detach_role_policy(RoleName=role, PolicyArn=p["PolicyArn"])
        except ClientError as e:
            if not _is_not_found(e):
                warn(f"Could not list managed policies on {role}: {e}")
        try:
            for pname in iam.list_role_policies(RoleName=role).get("PolicyNames", []):
                iam.delete_role_policy(RoleName=role, PolicyName=pname)
        except ClientError as e:
            if not _is_not_found(e):
                warn(f"Could not list inline policies on {role}: {e}")
        try:
            for ip in iam.list_instance_profiles_for_role(RoleName=role).get(
                "InstanceProfiles", []
            ):
                iam.remove_role_from_instance_profile(
                    InstanceProfileName=ip["InstanceProfileName"], RoleName=role
                )
        except ClientError as e:
            if not _is_not_found(e):
                warn(f"Could not list instance profiles for {role}: {e}")
        try:
            iam.delete_role(RoleName=role)
            ok(f"Deleted role {role}")
        except ClientError as e:
            if _is_not_found(e):
                info(f"Role {role} already gone")
            else:
                fail(f"Could not delete role {role}: {e}")


def delete_amplify_apps(clients: dict, app_ids: list[str]) -> None:
    if not app_ids:
        return
    section("Amplify Hosting")
    amplify = clients["amplify"]
    for app_id in app_ids:
        try:
            amplify.delete_app(appId=app_id)
            ok(f"Deleted Amplify app {app_id}")
        except ClientError as e:
            # Amplify reports a missing app as BadRequestException (handled
            # here, not in the global helper, so it can't mask real failures
            # elsewhere).
            code = e.response["Error"].get("Code", "")
            if _is_not_found(e) or code == "BadRequestException":
                info(f"Amplify app {app_id} already gone")
            else:
                fail(f"Could not delete Amplify app {app_id}: {e}")


def delete_log_groups(clients: dict, groups: list[str]) -> None:
    if not groups:
        return
    section("CloudWatch log groups")
    logs = clients["logs"]
    for name in groups:
        try:
            logs.delete_log_group(logGroupName=name)
            ok(f"Deleted {name}")
        except ClientError as e:
            if _is_not_found(e):
                info(f"{name} already gone")
            else:
                warn(f"Could not delete {name}: {e}")


def delete_state_file() -> None:
    if STATE_FILE.exists():
        STATE_FILE.unlink()
        ok(f"Removed {STATE_FILE.name}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description=f"Tear down {PROJECT_NAME} from AWS.")
    parser.add_argument(
        "--region", help="Override AWS region (otherwise: state file → env → us-east-1)."
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the final confirmation prompt (resource list is still shown).",
    )
    parser.add_argument(
        "--force-secret-delete",
        action="store_true",
        help=(
            "Permanently delete Secrets Manager secrets instead of scheduling a 7-day "
            "recovery window. Use this if you intend to recreate them immediately."
        ),
    )
    parser.add_argument(
        "--keep-logs",
        action="store_true",
        help="Leave CloudWatch log groups in place (they'd otherwise be deleted).",
    )
    parser.add_argument(
        "--account",
        help=(
            "Assert the target AWS account ID. Aborts before touching anything "
            "if the resolved credentials don't match. Required with --non-interactive. "
            "Strongly recommended always — this script deletes."
        ),
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help=(
            "Never prompt (implies the final confirmation). Requires --account "
            "and --region (or AWS_REGION)."
        ),
    )
    args = parser.parse_args()

    if args.non_interactive and not args.account:
        sys.stderr.write(
            "ERROR: --non-interactive requires --account <id> as an explicit "
            "target-account guard (this script is destructive).\n"
        )
        sys.exit(2)

    banner(
        f"{PROJECT_NAME} Teardown",
        "Reads .groundskeeper-deploy-state.json and removes every resource it created.",
    )

    state = load_state()
    initial_region = args.region or state.get("region")
    account, region, _caller = confirm_account_and_region(
        initial_region,
        expected_account=args.account,
        non_interactive=args.non_interactive,
    )

    clients = make_clients(region)
    inventory = discover(clients, state, account, region)

    total = sum(len(v) for v in inventory.values())
    if total == 0:
        console.print()
        console.print(
            Panel(
                "[bold green]Nothing to remove — your account is already clean.[/bold green]",
                border_style="green",
                padding=(1, 2),
            )
        )
        delete_state_file()
        return

    if not args.yes and not args.non_interactive:
        console.print()
        if not questionary.confirm(
            f"Delete all {total} resource(s) listed above? This cannot be undone.",
            default=False,
        ).ask():
            console.print("[dim]Aborted.[/dim]")
            sys.exit(0)

    # Order matters:
    #  1. Disable triggers first (EventBridge rule + one-time schedules) so
    #     nothing fires mid-teardown.
    #  2. Lambdas, then API Gateway (API GW's lambda permissions auto-clean).
    #  3. DynamoDB, Secrets — pure data resources, safe once nothing reads them.
    #  4. S3 bucket — Lambdas no longer reference its objects.
    #  5. IAM roles last so any resource that needed them is already gone.
    #  6. Amplify app — independent, but kept last so the dashboard stays
    #     reachable while we work.
    delete_eventbridge_rules(clients, inventory["eventbridge_rules"])
    delete_scheduler_schedules(clients, inventory["scheduler_schedules"])
    delete_lambdas(clients, inventory["lambda_functions"])
    delete_api_gateway(
        clients,
        inventory["api_gateway_ids"],
        inventory["api_keys"],
        inventory["usage_plans"],
    )
    delete_dynamodb(clients, inventory["dynamodb_tables"])
    delete_secrets(clients, inventory["secrets"], force=args.force_secret_delete)
    delete_s3_buckets(clients, inventory["s3_buckets"])
    delete_iam_roles(clients, inventory["iam_roles"])
    delete_amplify_apps(clients, inventory["amplify_apps"])
    if not args.keep_logs:
        delete_log_groups(clients, inventory["log_groups"])
    delete_state_file()

    console.print()
    console.print(
        Panel(
            f"[bold green]{PROJECT_NAME} teardown complete.[/bold green]\n"
            "[dim]CloudTrail entries are AWS-managed and outside this script's scope.[/dim]",
            border_style="green",
            padding=(1, 2),
        )
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[dim]Interrupted.[/dim]")
        sys.exit(130)
    except ClientError as e:
        console.print()
        fail(
            f"AWS error: {e.response['Error'].get('Code', '?')} — "
            f"{e.response['Error'].get('Message', e)}"
        )
        sys.exit(1)
