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
  - Python 3.12+
  - uv (https://docs.astral.sh/uv/#installation)
  - AWS CLI configured (any of: env vars, ~/.aws/credentials, SSO)
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
            warn(f"Couldn't read {STATE_FILE.name} — falling back to name-based discovery.")
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


def _env_filter_verdict(tags: dict[str, str], environment: str) -> tuple[bool, bool]:
    """Decide whether a resource bearing ``tags`` should be torn down.

    Returns ``(matches, is_legacy)``:
      * matches    — True if the resource belongs to this environment OR is
                     a legacy resource (project=groundskeeper, no environment
                     tag set yet).
      * is_legacy  — True only for the legacy case; the caller prints a note
                     so the user knows a re-deploy will add the env tag.

    A resource missing the ``project`` tag entirely never matches — even if
    its name collides — so cross-project name reuse can't cause an
    accidental deletion.
    """
    if tags.get(PROJECT_TAG_KEY) != PROJECT_TAG_VALUE:
        return False, False
    env_value = tags.get(ENVIRONMENT_TAG_KEY)
    if env_value is None:
        return True, True
    return env_value == environment, False


def _tags_from_pairs(pairs) -> dict[str, str]:
    """Normalize various list-of-pairs tag shapes to a flat dict."""
    out: dict[str, str] = {}
    for item in pairs or []:
        key = item.get("Key") or item.get("key")
        if key is None:
            continue
        value = item.get("Value") if "Value" in item else item.get("value", "")
        out[key] = value or ""
    return out


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
            "No AWS credentials found. Run `aws configure`, or set "
            "AWS_PROFILE (or AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY)."
        )
        sys.exit(1)
    except ClientError as e:
        fail(f"Couldn't check AWS credentials: {e}")
        sys.exit(1)
    account = caller["Account"]
    arn = caller["Arn"]

    # Hard guard for a DESTRUCTIVE script: if a target account was asserted,
    # the resolved identity MUST match it exactly — abort before discovering
    # or deleting anything. This makes "wrong profile + reflexive yes" (e.g.
    # nuking prod when you meant dev) impossible rather than merely unlikely.
    if expected_account and account != expected_account:
        fail(
            f"Account guard caught a mismatch: your credentials resolve to "
            f"{account}, but --account asked for {expected_account}. Aborting "
            f"before anything is touched."
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
        ok(f"Account {account} matches the --account guard. Proceeding non-interactively.")
        return account, region, caller

    if not questionary.confirm(
        f"Tear down {PROJECT_NAME} in this account and region?",
        default=False,
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
def discover(
    clients: dict, state: dict, account: str, region: str, environment: str
) -> dict[str, list[str]]:
    """Return a dict of resources we plan to delete, keyed by category.

    Only resources tagged ``project=groundskeeper`` AND
    ``environment=<environment>`` are included. As a migration affordance,
    resources missing the ``environment`` tag entirely (legacy deploys from
    before the env-scoping change) are still included — with a note printed
    once, since the next deploy will retag them.
    """
    section("Discovery")
    legacy_seen: list[str] = []

    def _check(tags: dict[str, str], label: str) -> bool:
        matches, is_legacy = _env_filter_verdict(tags, environment)
        if matches and is_legacy:
            legacy_seen.append(label)
        return matches

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
            fn = clients["lambda"].get_function(FunctionName=name)
            tags = fn.get("Tags") or {}
            if _check(tags, f"lambda/{name}"):
                inventory["lambda_functions"].append(name)
        except ClientError as e:
            if not _is_not_found(e):
                raise

    # API Gateway — state file first, then name-based fallback.
    # The REST-API summary doesn't include tags, so we look them up explicitly
    # via tag_resource's GET counterpart.
    def _apigw_tags(arn: str) -> dict[str, str]:
        try:
            return dict(clients["apigw"].get_tags(resourceArn=arn).get("tags", {}))
        except ClientError as e:
            if _is_not_found(e):
                return {}
            raise

    apigw_state = state.get("api_gateway", {})
    state_api_id = apigw_state.get("rest_api_id")
    candidate_api_ids: list[str] = []
    if state_api_id:
        try:
            clients["apigw"].get_rest_api(restApiId=state_api_id)
            candidate_api_ids.append(state_api_id)
        except ClientError as e:
            if not _is_not_found(e):
                raise
    if not candidate_api_ids:
        pos: str | None = None
        while True:
            kwargs: dict = {"limit": 500}
            if pos:
                kwargs["position"] = pos
            resp = clients["apigw"].get_rest_apis(**kwargs)
            for item in resp.get("items", []):
                if item["name"] == API_GATEWAY_NAME:
                    candidate_api_ids.append(item["id"])
            pos = resp.get("position")
            if not pos:
                break
    for cid in candidate_api_ids:
        arn = f"arn:aws:apigateway:{region}::/restapis/{cid}"
        if _check(_apigw_tags(arn), f"apigateway/{cid}"):
            inventory["api_gateway_ids"].append(cid)

    # API key
    pos = None
    while True:
        kwargs = {"limit": 500, "includeValues": False}
        if pos:
            kwargs["position"] = pos
        resp = clients["apigw"].get_api_keys(**kwargs)
        for k in resp.get("items", []):
            if k["name"] != API_KEY_NAME:
                continue
            arn = f"arn:aws:apigateway:{region}::/apikeys/{k['id']}"
            if _check(_apigw_tags(arn), f"apikey/{k['id']}"):
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
            if p["name"] != USAGE_PLAN_NAME:
                continue
            arn = f"arn:aws:apigateway:{region}::/usageplans/{p['id']}"
            if _check(_apigw_tags(arn), f"usageplan/{p['id']}"):
                inventory["usage_plans"].append(p["id"])
        pos = resp.get("position")
        if not pos:
            break

    # EventBridge daily rule
    try:
        rule = clients["events"].describe_rule(Name=ORCHESTRATOR_RULE_NAME)
        try:
            tag_resp = clients["events"].list_tags_for_resource(ResourceARN=rule["Arn"])
            tags = _tags_from_pairs(tag_resp.get("Tags", []))
        except ClientError as e:
            if _is_not_found(e):
                tags = {}
            else:
                raise
        if _check(tags, f"rule/{ORCHESTRATOR_RULE_NAME}"):
            inventory["eventbridge_rules"].append(ORCHESTRATOR_RULE_NAME)
    except ClientError as e:
        if not _is_not_found(e):
            raise

    # EventBridge Scheduler one-time schedules.
    # These are created at runtime by the orchestrator (not by deploy.py) so
    # they don't carry our tags — we match them by name prefix only. If you
    # run dev + prod in the same account, schedule names collide; clear them
    # by running teardown from the matching env's deploy.
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
        table = clients["dynamodb"].describe_table(TableName=DDB_TABLE_NAME)["Table"]
        try:
            tag_resp = clients["dynamodb"].list_tags_of_resource(ResourceArn=table["TableArn"])
            tags = _tags_from_pairs(tag_resp.get("Tags", []))
        except ClientError as e:
            if _is_not_found(e):
                tags = {}
            else:
                raise
        if _check(tags, f"dynamodb/{DDB_TABLE_NAME}"):
            inventory["dynamodb_tables"].append(DDB_TABLE_NAME)
    except ClientError as e:
        if not _is_not_found(e):
            raise

    # Secrets
    try:
        secret = clients["secrets"].describe_secret(SecretId=PAT_SECRET_NAME)
        tags = _tags_from_pairs(secret.get("Tags", []))
        if _check(tags, f"secret/{PAT_SECRET_NAME}"):
            inventory["secrets"].append(PAT_SECRET_NAME)
    except ClientError as e:
        if not _is_not_found(e):
            raise

    # S3 artifacts bucket
    bucket_name = state.get("s3_artifacts_bucket") or f"{PREFIX}-artifacts-{account}-{region}"
    try:
        clients["s3"].head_bucket(Bucket=bucket_name)
        try:
            tag_resp = clients["s3"].get_bucket_tagging(Bucket=bucket_name)
            tags = _tags_from_pairs(tag_resp.get("TagSet", []))
        except ClientError as e:
            # NoSuchTagSet means the bucket has no tags at all — treat as
            # empty so legacy buckets without our tags are skipped.
            code = e.response["Error"].get("Code", "")
            if code in {"NoSuchTagSet", "NoSuchTagSetError"}:
                tags = {}
            elif _is_not_found(e):
                tags = {}
            else:
                raise
        if _check(tags, f"s3/{bucket_name}"):
            inventory["s3_buckets"].append(bucket_name)
    except ClientError as e:
        if not _is_not_found(e) and e.response["Error"].get("Code") != "404":
            raise

    # IAM
    for role in (LAMBDA_ROLE_NAME, SCHEDULER_ROLE_NAME):
        try:
            clients["iam"].get_role(RoleName=role)
            try:
                tag_resp = clients["iam"].list_role_tags(RoleName=role)
                tags = _tags_from_pairs(tag_resp.get("Tags", []))
            except ClientError as e:
                if _is_not_found(e):
                    tags = {}
                else:
                    raise
            if _check(tags, f"iam/{role}"):
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
            if a["name"] != AMPLIFY_APP_NAME:
                continue
            tags = dict(a.get("tags") or {})
            if not tags:
                # list_apps omits tags on some API versions; fall back to
                # list_tags_for_resource against the app ARN.
                try:
                    tr = clients["amplify"].list_tags_for_resource(resourceArn=a["appArn"])
                    tags = dict(tr.get("tags") or {})
                except ClientError as e:
                    if not _is_not_found(e):
                        raise
            if _check(tags, f"amplify/{a['appId']}"):
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
    if legacy_seen:
        # One-line note per the migration plan: a re-deploy will add the
        # environment tag to these in-place.
        info(
            f"Legacy match (no environment tag yet): {', '.join(legacy_seen)} "
            f"— they'll be retagged on the next deploy."
        )
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
        console.print("  [green]✓[/green] Nothing left to delete — your account is already clean.")
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
                info(f"Rule {name} was already gone")
            else:
                fail(f"Couldn't delete rule {name}: {e}")


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
                fail(f"Couldn't delete schedule {name}: {e}")
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
                info(f"{name} was already gone")
            else:
                fail(f"Couldn't delete {name}: {e}")


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
                            warn(f"Couldn't detach stage {val} from plan {plan_id}: {e}")
            except ClientError as e:
                if not _is_not_found(e):
                    warn(f"Couldn't read the stages on usage plan {plan_id}: {e}")

            keys_resp = apigw.get_usage_plan_keys(usagePlanId=plan_id, limit=500)
            for k in keys_resp.get("items", []):
                try:
                    apigw.delete_usage_plan_key(usagePlanId=plan_id, keyId=k["id"])
                except ClientError as e:
                    if not _is_not_found(e):
                        warn(f"Couldn't detach key {k['id']} from plan {plan_id}: {e}")
            apigw.delete_usage_plan(usagePlanId=plan_id)
            ok(f"Deleted usage plan {plan_id}")
        except ClientError as e:
            if _is_not_found(e):
                info(f"Usage plan {plan_id} was already gone")
            else:
                fail(f"Couldn't delete usage plan {plan_id}: {e}")

    for key_id in api_key_ids:
        try:
            apigw.delete_api_key(apiKey=key_id)
            ok(f"Deleted API key {key_id}")
        except ClientError as e:
            if _is_not_found(e):
                info(f"API key {key_id} was already gone")
            else:
                fail(f"Couldn't delete API key {key_id}: {e}")

    for api_id in api_ids:
        try:
            apigw.delete_rest_api(restApiId=api_id)
            ok(f"Deleted REST API {api_id}")
        except ClientError as e:
            if _is_not_found(e):
                info(f"REST API {api_id} was already gone")
            elif e.response["Error"].get("Code") == "TooManyRequestsException":
                warn("Hit the API Gateway rate limit — pausing 30s and retrying.")
                time.sleep(30)
                try:
                    apigw.delete_rest_api(restApiId=api_id)
                    ok(f"Deleted REST API {api_id}")
                except ClientError as e2:
                    fail(f"Couldn't delete REST API {api_id}: {e2}")
            else:
                fail(f"Couldn't delete REST API {api_id}: {e}")


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
                info(f"Table {name} was already gone")
                continue
            fail(f"Couldn't delete {name}: {e}")
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
                info(f"Secret {name} was already gone")
            else:
                fail(f"Couldn't delete secret {name}: {e}")


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
                info(f"Bucket {bucket_name} was already gone")
            else:
                fail(f"Couldn't delete bucket {bucket_name}: {e}")


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
                warn(f"Couldn't list managed policies on {role}: {e}")
        try:
            for pname in iam.list_role_policies(RoleName=role).get("PolicyNames", []):
                iam.delete_role_policy(RoleName=role, PolicyName=pname)
        except ClientError as e:
            if not _is_not_found(e):
                warn(f"Couldn't list inline policies on {role}: {e}")
        try:
            for ip in iam.list_instance_profiles_for_role(RoleName=role).get(
                "InstanceProfiles", []
            ):
                iam.remove_role_from_instance_profile(
                    InstanceProfileName=ip["InstanceProfileName"], RoleName=role
                )
        except ClientError as e:
            if not _is_not_found(e):
                warn(f"Couldn't list instance profiles for {role}: {e}")
        try:
            iam.delete_role(RoleName=role)
            ok(f"Deleted role {role}")
        except ClientError as e:
            if _is_not_found(e):
                info(f"Role {role} was already gone")
            else:
                fail(f"Couldn't delete role {role}: {e}")


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
                info(f"Amplify app {app_id} was already gone")
            else:
                fail(f"Couldn't delete Amplify app {app_id}: {e}")


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
                info(f"{name} was already gone")
            else:
                warn(f"Couldn't delete {name}: {e}")


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
    parser.add_argument(
        "--environment",
        choices=list(ENVIRONMENT_CHOICES),
        default=DEFAULT_ENVIRONMENT,
        help=(
            "Environment to tear down. Discovery is scoped to resources tagged "
            "`project=groundskeeper` AND `environment=<env>`, so a dev teardown "
            "can't take out prod resources (and vice versa). Legacy resources "
            "without an `environment` tag are still included — they'll be "
            "retagged on the next deploy."
        ),
    )
    args = parser.parse_args()
    environment: str = args.environment

    if args.non_interactive and not args.account:
        sys.stderr.write(
            "ERROR: --non-interactive requires --account <id> as an explicit "
            "target-account guard (this script is destructive).\n"
        )
        sys.exit(2)

    banner(
        f"{PROJECT_NAME} Teardown",
        f"Environment: [bold]{environment}[/bold] — reads "
        ".groundskeeper-deploy-state.json and removes every resource tagged for "
        "this environment.",
    )

    state = load_state()
    initial_region = args.region or state.get("region")
    account, region, _caller = confirm_account_and_region(
        initial_region,
        expected_account=args.account,
        non_interactive=args.non_interactive,
    )

    clients = make_clients(region)
    inventory = discover(clients, state, account, region, environment)

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
            f"Delete all {total} resource(s) above? This can't be undone.",
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
            f"AWS returned an error: {e.response['Error'].get('Code', '?')} — "
            f"{e.response['Error'].get('Message', e)}"
        )
        sys.exit(1)
