from __future__ import annotations

import time

import requests

from exordos_observability.victoria.tests.functional import conftest as ft


class TestVictoriaStorages:
    def test_metrics_roundtrip(self, victoria_metrics_endpoint) -> None:
        metric = f'exordos_observability_e2e_test{{run="{int(time.time())}"}}'
        ft.push_test_metric(victoria_metrics_endpoint, metric.split("{")[0], value=42)

        deadline = time.monotonic() + 60
        result = None
        while time.monotonic() < deadline:
            result = ft.query_metric(victoria_metrics_endpoint, metric.split("{")[0])
            if result.get("data", {}).get("result"):
                break
            time.sleep(3)

        assert result is not None
        samples = result["data"]["result"]
        assert samples, f"metric not queryable after push: {result}"
        assert float(samples[0]["value"][1]) == 42

    def test_logs_roundtrip(self, victoria_logs_endpoint) -> None:
        marker = f"e2e-test-{int(time.time())}"
        ft.push_test_log(
            victoria_logs_endpoint,
            f"exordos observability e2e marker {marker}",
            {"job": "exordos-observability-e2e"},
        )

        deadline = time.monotonic() + 60
        lines: list[str] = []
        while time.monotonic() < deadline:
            lines = ft.query_logs(
                victoria_logs_endpoint, f'job:exordos-observability-e2e AND "{marker}"'
            )
            if lines:
                break
            time.sleep(3)

        assert lines, "pushed log line not queryable via VictoriaLogs"
        assert marker in lines[0]


class TestGrafanaWiring:
    def test_datasources_point_at_victoria(
        self, grafana_datasources, victoria_metrics_endpoint, victoria_logs_endpoint
    ) -> None:
        by_type = {ds["type"]: ds for ds in grafana_datasources}
        assert by_type["prometheus"]["url"] == victoria_metrics_endpoint
        assert (
            by_type["victoriametrics-logs-datasource"]["url"] == victoria_logs_endpoint
        )
        assert by_type["prometheus"]["is_default"] is True

    def test_grafana_process_healthy(self, grafana_instance) -> None:
        ips = grafana_instance.get("ipsv4", [])
        assert ips, "grafana instance has no IP"
        resp = requests.get(f"http://{ips[0]}:3000/api/health", timeout=30)
        resp.raise_for_status()
        assert resp.json().get("database") == "ok"


class TestIdempotency:
    def test_no_restart_loop_across_reconciliation_cycles(
        self, victoria_instance, grafana_instance, observability_api_client
    ) -> None:
        """Re-poll both instances a few times; status must stay ACTIVE.

        A DP driver that restarts its service unconditionally on every
        reconciliation tick (the pitfall this plugin's design docs flag
        explicitly) would eventually show up here as flapping status or a
        node dropping out of `ipsv4` between polls.
        """
        for _ in range(3):
            time.sleep(10)
            v = observability_api_client.get(
                ft.VICTORIA_INSTANCES, uuid=victoria_instance["uuid"]
            )
            g = observability_api_client.get(
                ft.GRAFANA_INSTANCES, uuid=grafana_instance["uuid"]
            )
            assert v["status"] == "ACTIVE"
            assert g["status"] == "ACTIVE"
            assert v["ipsv4"]
            assert g["ipsv4"]
