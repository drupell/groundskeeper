"""AWS cleanup utilities for teardown.py."""

import os

import boto3
from botocore.exceptions import ClientError

from shared import log

PROJECT_TAG = "groundskeeper"
ORCHESTRATOR_CRON = os.environ.get("ORCHESTRATOR_CRON", "cron(0 12 * * ? *)")


def _get_region(region: str | None) -> str:
    """Get the AWS region, preferring explicit arg over default."""
    if region:
        return region
    return os.environ.get("AWS_REGION", "us-east-1")


def cleanup_lambdas(region: str | None = None, verbose: bool = False) -> int:
    """Delete all Groundskeeper Lambda functions."""
    region = _get_region(region)
    client = boto3.client("lambda", region_name=region)
    deleted = 0

    try:
        for name in ["groundskeeper-orchestrator", "groundskeeper-executor", "groundskeeper-api"]:
            try:
                client.delete_function(FunctionName=name)
                deleted += 1
                if verbose:
                    log.info("deleted_lambda", name=name)
            except ClientError as e:
                if e.response["Error"]["Code"] != "ResourceNotFoundException":
                    raise
    except Exception as e:
        log.warn("lambda_cleanup_error", error=str(e))

    return deleted


def cleanup_api_gateway(region: str | None = None, verbose: bool = False) -> int:
    """Delete the Groundskeeper API Gateway REST API."""
    region = _get_region(region)
    client = boto3.client("apigateway", region_name=region)
    deleted = 0

    try:
        apis = client.get_rest_apis()
        for api in apis.get("items", []):
            if api["name"] == "groundskeeper":
                try:
                    client.delete_rest_api(restApiId=api["id"])
                    deleted += 1
                    if verbose:
                        log.info("deleted_api_gateway", id=api["id"])
                except ClientError as e:
                    if e.response["Error"]["Code"] != "ResourceNotFoundException":
                        raise
    except Exception as e:
        log.warn("api_gateway_cleanup_error", error=str(e))

    return deleted


def cleanup_eventbridge(region: str | None = None, verbose: bool = False) -> int:
    """Delete the EventBridge daily rule and all commit schedules."""
    region = _get_region(region)
    rules_client = boto3.client("events", region_name=region)
    scheduler_client = boto3.client("scheduler", region_name=region)
    deleted = 0

    try:
        # Delete EventBridge rule
        try:
            rules_client.remove_targets(
                Rule="groundskeeper-orchestrator-daily",
                Ids=["1"],
            )
            rules_client.delete_rule(Name="groundskeeper-orchestrator-daily")
            deleted += 1
            if verbose:
                log.info("deleted_eventbridge_rule", name="groundskeeper-orchestrator-daily")
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceNotFoundException":
                raise

        # Delete EventBridge Scheduler schedules
        try:
            next_token = None
            while True:
                kwargs = {
                    "GroupName": "default",
                    "NamePrefix": "groundskeeper-commit",
                    "MaxResults": 100,
                }
                if next_token:
                    kwargs["NextToken"] = next_token

                resp = scheduler_client.list_schedules(**kwargs)
                for schedule in resp.get("Schedules", []):
                    try:
                        scheduler_client.delete_schedule(
                            Name=schedule["Name"],
                            GroupName="default",
                        )
                        deleted += 1
                        if verbose:
                            log.info("deleted_schedule", name=schedule["Name"])
                    except ClientError as e:
                        if e.response["Error"]["Code"] != "ResourceNotFoundException":
                            raise

                next_token = resp.get("NextToken")
                if not next_token:
                    break
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceNotFoundException":
                raise

    except Exception as e:
        log.warn("eventbridge_cleanup_error", error=str(e))

    return deleted


def cleanup_ddb(region: str | None = None, verbose: bool = False) -> int:
    """Delete the DynamoDB config table."""
    region = _get_region(region)
    client = boto3.client("dynamodb", region_name=region)
    deleted = 0

    try:
        client.delete_table(TableName="groundskeeper-config")
        deleted = 1
        if verbose:
            log.info("deleted_ddb_table", name="groundskeeper-config")
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            log.warn("ddb_cleanup_error", error=str(e))

    return deleted


def cleanup_secrets(region: str | None = None, verbose: bool = False) -> int:
    """Delete Secrets Manager entries for GitHub PAT and credentials."""
    region = _get_region(region)
    client = boto3.client("secretsmanager", region_name=region)
    deleted = 0

    try:
        for secret_name in ["groundskeeper/github-pat", "groundskeeper/amplify-password"]:
            try:
                client.delete_secret(
                    SecretId=secret_name,
                    ForceDeleteWithoutRecovery=True,
                )
                deleted += 1
                if verbose:
                    log.info("deleted_secret", name=secret_name)
            except ClientError as e:
                if e.response["Error"]["Code"] != "ResourceNotFoundException":
                    raise
    except Exception as e:
        log.warn("secrets_cleanup_error", error=str(e))

    return deleted


def cleanup_iam(region: str | None = None, verbose: bool = False) -> int:
    """Delete IAM role and attached policies."""
    region = _get_region(region)
    client = boto3.client("iam", region_name=region)
    deleted = 0

    try:
        role_name = "groundskeeper-lambda-role"

        # Detach inline policies
        try:
            policies = client.list_role_policies(RoleName=role_name)
            for policy_name in policies.get("PolicyNames", []):
                client.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
                deleted += 1
                if verbose:
                    log.info("deleted_inline_policy", name=policy_name, role=role_name)
        except ClientError as e:
            if e.response["Error"]["Code"] != "NoSuchEntity":
                raise

        # Delete the role itself
        try:
            client.delete_role(RoleName=role_name)
            deleted += 1
            if verbose:
                log.info("deleted_iam_role", name=role_name)
        except ClientError as e:
            if e.response["Error"]["Code"] != "NoSuchEntity":
                raise

        # Delete scheduler role
        scheduler_role = "groundskeeper-scheduler-role"
        try:
            sched_policies = client.list_role_policies(RoleName=scheduler_role)
            for policy_name in sched_policies.get("PolicyNames", []):
                client.delete_role_policy(RoleName=scheduler_role, PolicyName=policy_name)
        except ClientError:
            pass

        try:
            client.delete_role(RoleName=scheduler_role)
            if verbose:
                log.info("deleted_iam_role", name=scheduler_role)
        except ClientError as e:
            if e.response["Error"]["Code"] != "NoSuchEntity":
                raise

    except Exception as e:
        log.warn("iam_cleanup_error", error=str(e))

    return deleted


def cleanup_logs(region: str | None = None, verbose: bool = False) -> int:
    """Delete CloudWatch log groups."""
    region = _get_region(region)
    client = boto3.client("logs", region_name=region)
    deleted = 0

    try:
        resp = client.describe_log_groups(logGroupNamePrefix="/aws/lambda/groundskeeper")
        for log_group in resp.get("logGroups", []):
            try:
                client.delete_log_group(logGroupName=log_group["logGroupName"])
                deleted += 1
                if verbose:
                    log.info("deleted_log_group", name=log_group["logGroupName"])
            except ClientError as e:
                if e.response["Error"]["Code"] != "ResourceNotFoundException":
                    raise
    except Exception as e:
        log.warn("logs_cleanup_error", error=str(e))

    return deleted


def cleanup_amplify(region: str | None = None, verbose: bool = False) -> int:
    """Delete the Amplify app."""
    region = _get_region(region)
    client = boto3.client("amplify", region_name=region)
    deleted = 0

    try:
        apps = client.list_apps()
        for app in apps.get("apps", []):
            if app.get("name") == "groundskeeper-dashboard":
                try:
                    client.delete_app(appId=app["appId"])
                    deleted = 1
                    if verbose:
                        log.info("deleted_amplify_app", id=app["appId"])
                except ClientError as e:
                    if e.response["Error"]["Code"] != "ResourceNotFoundException":
                        raise
    except Exception as e:
        log.warn("amplify_cleanup_error", error=str(e))

    return deleted
