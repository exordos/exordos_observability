#    Copyright 2026 Genesis Corporation.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
import exordos_observability.grafana.controlplane.dm.sources as sources_module
from exordos_observability.grafana.controlplane.dm import sources


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FailingResponse:
    def raise_for_status(self):
        raise RuntimeError("404 not found")

    def json(self):
        return {}


class FakeCoreClient:
    """Minimal stand-in for the ``CollectionBaseClient`` used by builders."""

    def __init__(self, artifacts: dict[str, dict]):
        self._artifacts = artifacts
        self.filter_calls: list[tuple[str, dict]] = []

    def filter(self, collection: str, **filters):
        self.filter_calls.append((collection, filters))
        urn = filters.get("urn")
        artifact = self._artifacts.get(urn)
        return [artifact] if artifact is not None else []


class TestUrnDashboardSource:
    URN = "urn:artifacts:12345678-1234-1234-1234-123456789012"

    def test_kind_is_urn(self) -> None:
        src = sources.UrnDashboardSource(urn=self.URN)
        assert src.kind == "urn"

    def test_urn_is_stored(self) -> None:
        src = sources.UrnDashboardSource(urn=self.URN)
        assert src.urn == self.URN

    def test_dashboard_resolves_urn_and_fetches_json(self, monkeypatch) -> None:
        captured = {}

        class FakeClient:
            def __init__(self, *args, **kwargs):
                captured["timeout"] = kwargs.get("default_timeout")

            def get(self, url, **kwargs):
                captured["url"] = url
                return FakeResponse({"title": "foo", "panels": []})

        monkeypatch.setattr(sources_module.bazooka, "Client", FakeClient)

        core_client = FakeCoreClient(
            {self.URN: {"urn": self.URN, "uri": "https://repo.internal/d.json"}}
        )
        src = sources.UrnDashboardSource(urn=self.URN)
        result = src.dashboard(core_client=core_client)

        assert result == {"title": "foo", "panels": []}
        assert captured["url"] == "https://repo.internal/d.json"
        assert captured["timeout"] == sources.URL_DASHBOARD_TIMEOUT
        assert core_client.filter_calls == [
            ("/v1/repo/artifacts/", {"urn": self.URN})
        ]

    def test_dashboard_caches_after_first_fetch(self, monkeypatch) -> None:
        fetch_count = {"n": 0}

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def get(self, url, **kwargs):
                fetch_count["n"] += 1
                return FakeResponse({"title": "foo"})

        monkeypatch.setattr(sources_module.bazooka, "Client", FakeClient)

        core_client = FakeCoreClient(
            {self.URN: {"urn": self.URN, "uri": "https://repo.internal/d.json"}}
        )
        src = sources.UrnDashboardSource(urn=self.URN)
        assert src.dashboard(core_client=core_client) == {"title": "foo"}
        assert src.dashboard(core_client=core_client) == {"title": "foo"}
        assert src.dashboard(core_client=core_client) == {"title": "foo"}
        # Only one HTTP fetch (and one URN lookup) despite three calls.
        assert fetch_count["n"] == 1
        assert len(core_client.filter_calls) == 1

    def test_dashboard_propagates_http_error(self, monkeypatch) -> None:
        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def get(self, url, **kwargs):
                return FailingResponse()

        monkeypatch.setattr(sources_module.bazooka, "Client", FakeClient)

        core_client = FakeCoreClient(
            {self.URN: {"urn": self.URN, "uri": "https://repo.internal/missing.json"}}
        )
        src = sources.UrnDashboardSource(urn=self.URN)
        import pytest

        with pytest.raises(RuntimeError):
            src.dashboard(core_client=core_client)

    def test_dashboard_raises_without_core_client(self) -> None:
        import pytest

        src = sources.UrnDashboardSource(urn=self.URN)
        with pytest.raises(ValueError):
            src.dashboard()

    def test_dashboard_raises_for_non_artifact_urn(self) -> None:
        import pytest

        src = sources.UrnDashboardSource(urn="urn:images:12345678")
        with pytest.raises(ValueError):
            src.dashboard(core_client=FakeCoreClient({}))

    def test_dashboard_raises_when_artifact_not_found(self) -> None:
        import pytest

        src = sources.UrnDashboardSource(urn=self.URN)
        with pytest.raises(LookupError):
            src.dashboard(core_client=FakeCoreClient({}))

    def test_source_digest_returns_urn(self) -> None:
        src = sources.UrnDashboardSource(urn=self.URN)
        assert src.source_digest() == self.URN


class TestRawDashboardSource:
    def test_kind_is_raw(self) -> None:
        src = sources.RawDashboardSource(content={"title": "foo"})
        assert src.kind == "raw"

    def test_content_is_stored(self) -> None:
        payload = {"title": "foo", "panels": []}
        src = sources.RawDashboardSource(content=payload)
        assert src.content == payload

    def test_dashboard_returns_content(self) -> None:
        payload = {"title": "foo", "panels": [{"id": 1}]}
        src = sources.RawDashboardSource(content=payload)
        assert src.dashboard() == payload

    def test_source_digest_returns_sha256_of_content(self) -> None:
        import hashlib
        import json

        payload = {"title": "foo", "panels": []}
        src = sources.RawDashboardSource(content=payload)
        expected = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()
        assert src.source_digest() == expected

    def test_source_digest_stable_for_same_content(self) -> None:
        payload = {"title": "foo", "panels": []}
        a = sources.RawDashboardSource(content=payload)
        b = sources.RawDashboardSource(content=dict(payload))
        assert a.source_digest() == b.source_digest()

    def test_source_digest_differs_for_different_content(self) -> None:
        a = sources.RawDashboardSource(content={"title": "foo"})
        b = sources.RawDashboardSource(content={"title": "bar"})
        assert a.source_digest() != b.source_digest()


class TestBundledDashboardSource:
    def test_kind_is_bundled(self) -> None:
        src = sources.BundledDashboardSource(dashboard_id=1860)
        assert src.kind == "bundled"

    def test_dashboard_id_is_stored(self) -> None:
        src = sources.BundledDashboardSource(dashboard_id=1860, revision=31)
        assert src.dashboard_id == 1860
        assert src.revision == 31

    def test_revision_defaults_to_1(self) -> None:
        src = sources.BundledDashboardSource(dashboard_id=1860)
        assert src.revision == 1

    def test_catalog_url(self) -> None:
        src = sources.BundledDashboardSource(dashboard_id=1860, revision=31)
        expected = "https://grafana.com/api/dashboards/1860/revisions/31/download"
        assert src._catalog_url() == expected

    def test_dashboard_fetches_from_catalog(self, monkeypatch) -> None:
        captured = {}

        class FakeResponse:
            def __init__(self, payload):
                self._payload = payload

            def raise_for_status(self):
                pass

            def json(self):
                return self._payload

        class FakeClient:
            def __init__(self, *args, **kwargs):
                captured["timeout"] = kwargs.get("default_timeout")

            def get(self, url, **kwargs):
                captured["url"] = url
                return FakeResponse({"title": "Node Exporter Full", "panels": []})

        monkeypatch.setattr(sources_module.bazooka, "Client", FakeClient)

        src = sources.BundledDashboardSource(dashboard_id=1860, revision=31)
        result = src.dashboard()

        assert result == {"title": "Node Exporter Full", "panels": []}
        assert captured["url"] == (
            "https://grafana.com/api/dashboards/1860/revisions/31/download"
        )
        assert captured["timeout"] == sources.URL_DASHBOARD_TIMEOUT

    def test_dashboard_propagates_http_error(self, monkeypatch) -> None:
        class FakeResponse:
            def raise_for_status(self):
                raise RuntimeError("404 not found")

            def json(self):
                return {}

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def get(self, url, **kwargs):
                return FakeResponse()

        monkeypatch.setattr(sources_module.bazooka, "Client", FakeClient)

        import pytest

        src = sources.BundledDashboardSource(dashboard_id=999999)
        with pytest.raises(RuntimeError):
            src.dashboard()

    def test_source_digest_includes_id_and_revision(self) -> None:
        src = sources.BundledDashboardSource(dashboard_id=1860, revision=31)
        assert src.source_digest() == "grafana_1860_31"

    def test_source_digest_differs_for_different_revisions(self) -> None:
        a = sources.BundledDashboardSource(dashboard_id=1860, revision=1)
        b = sources.BundledDashboardSource(dashboard_id=1860, revision=31)
        assert a.source_digest() != b.source_digest()


class TestAbstractDashboardSource:
    def test_fetch_dashboard_raises_not_implemented(self) -> None:
        class DummySource(sources.AbstractDashboardSource):
            KIND = "dummy"

        src = DummySource()
        try:
            src.dashboard()
            assert False, "expected NotImplementedError"
        except NotImplementedError:
            pass

    def test_source_digest_raises_not_implemented(self) -> None:
        class DummySource(sources.AbstractDashboardSource):
            KIND = "dummy"

        src = DummySource()
        try:
            src.source_digest()
            assert False, "expected NotImplementedError"
        except NotImplementedError:
            pass

    def test_dashboard_caches_fetch_result(self) -> None:
        class DummySource(sources.AbstractDashboardSource):
            KIND = "dummy"
            _fetch_count = 0

            def _fetch_dashboard(self, core_client=None) -> dict:
                self._fetch_count += 1
                return {"title": "cached"}

        src = DummySource()
        assert src.dashboard() == {"title": "cached"}
        assert src.dashboard() == {"title": "cached"}
        assert src._fetch_count == 1

    def test_dashboard_raises_type_error_for_non_dict(self) -> None:
        """A source returning a JSON array (not an object) must raise
        TypeError so the builder catches it and marks the dashboard
        as ERROR."""
        import pytest

        class DummySource(sources.AbstractDashboardSource):
            KIND = "dummy"

            def _fetch_dashboard(self, core_client=None) -> dict:
                return []

        src = DummySource()
        with pytest.raises(TypeError, match="not a JSON object"):
            src.dashboard()

    def test_dashboard_does_not_cache_non_dict(self) -> None:
        """Bad content (non-dict) must not be cached, so the next call
        retries the fetch — if the underlying source was fixed, the
        dashboard recovers on the next tick."""
        class DummySource(sources.AbstractDashboardSource):
            KIND = "dummy"
            _fetch_count = 0

            def _fetch_dashboard(self, core_client=None) -> dict:
                self._fetch_count += 1
                if self._fetch_count == 1:
                    return []
                return {"title": "recovered"}

        src = DummySource()
        try:
            src.dashboard()
        except TypeError:
            pass
        # Second call retries fetch and succeeds.
        assert src.dashboard() == {"title": "recovered"}
        assert src._fetch_count == 2
