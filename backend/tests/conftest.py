"""Pytest bootstrap.

Several `shared.*` modules construct boto3 clients at import time. boto3
needs a region to build a client (credentials are only needed for actual
calls, which these unit tests never make). Set safe dummy values *before*
any test imports a shared module so importing never raises NoRegionError.
"""

import os

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("CONFIG_TABLE", "groundskeeper-config-test")
os.environ.setdefault("PAT_SECRET_NAME", "groundskeeper/github-pat-test")
os.environ.setdefault("BEDROCK_MODEL_ID", "amazon.nova-lite-v1:0")
os.environ.setdefault("PROJECT_TAG", "Groundskeeper")
