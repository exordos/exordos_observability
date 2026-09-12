#    Copyright 2026 Genesis Corporation.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from __future__ import annotations

import os
import time

import pytest
import requests
from exordos.clients import base_client
from gcl_sdk.clients.http import base as http_client

# --- Environment configuration ---

EXORDOS_ENDPOINT = os.environ.get("EXORDOS_ENDPOINT", "http://10.20.0.2/api/core")
EXORDOS_USERNAME = os.environ.get("EXORDOS_USERNAME", "admin")
EXORDOS_PASSWORD = os.environ.get("EXORDOS_PASSWORD", "")

# Both plugins mount under the same metapaas user-api on metapaas-cp (port
# 8080) — /v1/types/victoria/ and /v1/types/grafana/ — so one base URL and
# one authenticated client each cover both.
EXORDOS_OBSERVABILITY_CP_URL = os.environ.get("EXORDOS_OBSERVABILITY_CP_URL", "")

# The functional suite reuses the instances deployed by the platform-wide
# observability composition manifest (observability.yaml.j2) rather than
# booting its own DP VMs. Those instances live in the EM project and are
# named "victoria" / "grafana" by the composition; override via env if a
# different composition (e.g. example_observability.yaml.j2) is deployed.
VICTORIA_INSTANCE_NAME = os.environ.get("EXORDOS_VICTORIA_INSTANCE_NAME", "victoria")
GRAFANA_INSTANCE_NAME = os.environ.get("EXORDOS_GRAFANA_INSTANCE_NAME", "grafana")

POLL_TIMEOUT = int(os.environ.get("EXORDOS_POLL_TIMEOUT", "600"))
POLL_INTERVAL = int(os.environ.get("EXORDOS_POLL_INTERVAL", "15"))

VICTORIA_INSTANCES = "/v1/types/victoria/instances/"
VICTORIA_VERSIONS = "/v1/types/victoria/versions/"
GRAFANA_INSTANCES = "/v1/types/grafana/instances/"
GRAFANA_VERSIONS = "/v1/types/grafana/versions/"
NODE_COLLECTION = "/v1/compute/nodes/"


# --- Auth helpers ---


def _get_auth_data(endpoint: str | None = None) -> dict:
    # Unscoped token: the composition instances live in the EM project, not
    # the metapaas project, so a project-scoped token cannot read them
    # (PolicyBasedController._force_project_id rejects cross-project reads).
    # An unscoped admin token (``*.*.*``) bypasses project forcing entirely,
    # which is exactly what's needed to reuse the composition instances.
    return dict(
        endpoint=endpoint or EXORDOS_ENDPOINT,
        username=EXORDOS_USERNAME,
        password=EXORDOS_PASSWORD,
        access_token=None,
        refresh_token=None,
        scope=None,
    )


@pytest.fixture(scope="session")
def core_client() -> http_client.CollectionBaseClient:
    return base_client.get_user_api_client(_get_auth_data())


@pytest.fixture(scope="session")
def observability_cp_ip(core_client) -> str:
    if EXORDOS_OBSERVABILITY_CP_URL:
        host = EXORDOS_OBSERVABILITY_CP_URL.split("//", 1)[-1].split(":")[0]
        return host

    nodes = core_client.filter(NODE_COLLECTION, name="metapaas-cp")
    if not nodes:
        all_nodes = core_client.filter(NODE_COLLECTION)
        nodes = [n for n in all_nodes if "metapaas" in n.get("name", "").lower()]
    if not nodes:
        pytest.skip(
            "No metapaas-cp compute node found — is metapaas element installed?"
        )
    node = nodes[0]
    net = node.get("default_network", {})
    ip = net.get("ipv4")
    if not ip:
        pytest.skip("metapaas-cp node has no IP yet")
    return ip


@pytest.fixture(scope="session")
def observability_api_client(
    observability_cp_ip,
) -> http_client.CollectionBaseClient:
    """Unscoped admin client for both victoria and grafana APIs.

    Auth is performed against Core IAM (``EXORDOS_ENDPOINT``) while API
    calls go to the MetaPaaS user-api on metapaas-cp (port 8080), mirroring
    how the CLI's realm config wires the two. The token is deliberately
    unscoped so it can read the composition instances regardless of which
    project they were created in (see ``_get_auth_data`` for the rationale).
    """
    cp_url = EXORDOS_OBSERVABILITY_CP_URL or f"http://{observability_cp_ip}:8080"
    core_auth = http_client.CoreIamAuthenticator(
        base_url=EXORDOS_ENDPOINT,
        username=EXORDOS_USERNAME,
        password=EXORDOS_PASSWORD,
        scope=None,
    )
    return http_client.CollectionBaseClient(base_url=cp_url, auth=core_auth)


def _poll_instance_status(
    client, collection, instance_uuid, target_status, timeout, interval
):
    deadline = time.monotonic() + timeout
    last_status = ""
    while time.monotonic() < deadline:
        instance = client.get(collection, uuid=instance_uuid)
        last_status = instance.get("status", "")
        if last_status == target_status:
            return instance
        if last_status in ("ERROR", "CREATE_FAILED", "DELETE_FAILED"):
            pytest.fail(f"Instance entered terminal status: {last_status}")
        time.sleep(interval)
    pytest.fail(
        f"Instance {instance_uuid} did not reach {target_status} "
        f"within {timeout}s (last: {last_status})"
    )


def _reuse_instance(client, collection, name, timeout, interval) -> dict:
    """Find an existing instance by name and wait until ACTIVE.

    The functional suite reuses the instances deployed by the observability
    composition manifest instead of booting its own DP VMs (CI runners
    don't have the resources to spin up extra VMs reliably, and creating a
    Grafana instance via the API also requires an ``auth`` configuration the
    test doesn't have). If the composition isn't deployed, skip gracefully.
    """
    instances = client.filter(collection, name=name)
    if not instances:
        pytest.skip(
            f"No {collection} instance named '{name}' found — is the "
            f"observability composition manifest deployed?"
        )
    instance_uuid = instances[0]["uuid"]
    return _poll_instance_status(
        client, collection, instance_uuid, "ACTIVE", timeout, interval
    )


@pytest.fixture(scope="session")
def victoria_instance(observability_api_client) -> dict:
    """Reuse the composition's Victoria instance and wait until ACTIVE."""
    return _reuse_instance(
        observability_api_client,
        VICTORIA_INSTANCES,
        VICTORIA_INSTANCE_NAME,
        POLL_TIMEOUT,
        POLL_INTERVAL,
    )


@pytest.fixture(scope="session")
def grafana_instance(observability_api_client) -> dict:
    """Reuse the composition's Grafana instance and wait until ACTIVE."""
    return _reuse_instance(
        observability_api_client,
        GRAFANA_INSTANCES,
        GRAFANA_INSTANCE_NAME,
        POLL_TIMEOUT,
        POLL_INTERVAL,
    )


@pytest.fixture(scope="session")
def victoria_metrics_endpoint(victoria_instance) -> str:
    endpoint = victoria_instance.get("metrics_endpoint", "")
    if not endpoint:
        pytest.skip("Victoria instance has no metrics_endpoint yet")
    return endpoint


@pytest.fixture(scope="session")
def victoria_logs_endpoint(victoria_instance) -> str:
    endpoint = victoria_instance.get("logs_endpoint", "")
    if not endpoint:
        pytest.skip("Victoria instance has no logs_endpoint yet")
    return endpoint


@pytest.fixture(scope="session")
def grafana_datasources(
    observability_api_client,
    grafana_instance,
) -> list[dict]:
    """Read the datasources already wired on the composition Grafana instance.

    The observability composition manifest provisions a prometheus
    datasource (``victoria-metrics``) and a VictoriaLogs datasource
    (``victoria-logs``) pointing at the composition Victoria instance. The
    functional suite checks that wiring rather than creating its own.
    """
    collection = f"{GRAFANA_INSTANCES}{grafana_instance['uuid']}/datasources/"
    datasources = observability_api_client.filter(collection)
    if not datasources:
        pytest.skip(
            "No grafana datasources found on the composition instance — is "
            "the observability composition manifest deployed?"
        )
    return datasources


# --- Helper utilities ---


def push_test_metric(
    metrics_endpoint: str, metric_name: str, value: float = 1.0
) -> None:
    """Push a single sample via the Prometheus text exposition remote-write-compatible import endpoint."""
    line = f"{metric_name} {value}"
    resp = requests.post(
        f"{metrics_endpoint}/api/v1/import/prometheus",
        data=line,
        timeout=30,
    )
    resp.raise_for_status()


def push_test_log(logs_endpoint: str, message: str, stream_fields: dict) -> None:
    """Push a single log line via the VictoriaLogs JSON stream ingestion endpoint."""
    resp = requests.post(
        f"{logs_endpoint}/insert/jsonline",
        params={"_stream_fields": ",".join(stream_fields.keys())},
        json={"_msg": message, **stream_fields},
        timeout=30,
    )
    resp.raise_for_status()


def query_metric(metrics_endpoint: str, query: str) -> dict:
    resp = requests.get(
        f"{metrics_endpoint}/api/v1/query", params={"query": query}, timeout=30
    )
    resp.raise_for_status()
    return resp.json()


def query_logs(logs_endpoint: str, query: str) -> list:
    resp = requests.get(
        f"{logs_endpoint}/select/logsql/query", params={"query": query}, timeout=30
    )
    resp.raise_for_status()
    return [line for line in resp.text.splitlines() if line]
