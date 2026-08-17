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
"""Dashboard content sources for GrafanaArtifactDashboard.

Each source kind knows how to fetch dashboard content and return it as a
parsed dict. Sources are polymorphic ``AbstractKindModel`` subclasses, so
the ``source`` field on ``GrafanaArtifactDashboard`` is declared with
``KindModelSelectorType`` and the framework deserializes the stored dict
into the right subclass based on the ``kind`` field.
"""

from __future__ import annotations

import hashlib
import json

import bazooka
from restalchemy.dm import models as ra_models
from restalchemy.dm import properties, types_dynamic
from restalchemy.dm import types as ra_types

# HTTP timeout (seconds) for URL-based dashboard fetches.
URL_DASHBOARD_TIMEOUT = 30

# Base URL for the Grafana community dashboard catalog API.
GRAFANA_CATALOG_API = "https://grafana.com/api/dashboards"


class AbstractDashboardSource(
    ra_models.SimpleViewMixin,
    types_dynamic.AbstractKindModel,
):
    """Abstract dashboard content source.

    Subclasses implement fetching dashboard content from a specific
    source kind (URN, inline, etc.). The ``kind`` field is auto-populated
    by ``AbstractKindModel`` from the class' ``KIND`` attribute.

    ``dashboard()`` results are cached in memory after the first
    successful call, so repeated builder iterations (or multiple
    instances referencing the same source) don't re-fetch the content.
    This is important for external sources like grafana.com that
    rate-limit requests (HTTP 429). The cache lives on the instance,
    so a freshly deserialized source object starts without a cache.
    """

    # In-memory cache for the fetched dashboard content.
    # Not persisted — only lives as long as the source object.
    _cached_dashboard: dict | None = None

    def dashboard(self, core_client=None) -> dict:
        """Return dashboard content as a parsed dict.

        Caches the result of the first successful ``_fetch_dashboard()``
        call. Subsequent calls return the cached value without invoking
        the subclass fetch logic. ``core_client`` is an authenticated
        Exordos Core API client (see ``common.client.create_core_client``);
        only ``UrnDashboardSource`` uses it, to resolve the artifact URN
        against Core's repo-artifact registry.

        Raises ``TypeError`` if the fetched content is not a JSON object
        (dict). This is validated here — before caching — so that bad
        content is never cached and the next call retries the fetch.
        """
        if self._cached_dashboard is not None:
            return self._cached_dashboard
        content = self._fetch_dashboard(core_client=core_client)
        if not isinstance(content, dict):
            raise TypeError(
                f"Dashboard content from source {self.KIND!r} is not a "
                f"JSON object (got {type(content).__name__})"
            )
        self._cached_dashboard = content
        return content

    def _fetch_dashboard(self, core_client=None) -> dict:
        """Fetch dashboard content from the underlying source.

        Subclasses override this to implement the actual retrieval logic.
        """
        raise NotImplementedError()

    def source_digest(self) -> str:
        """Return the source-specific part of the dashboard version_ref.

        The full version_ref is ``{dashboard_uuid}_{source_digest()}``.
        Each source kind defines its own stable identifier for the content
        it references (e.g. URN for ``UrnDashboardSource``, content hash
        for ``RawDashboardSource``).
        """
        raise NotImplementedError()


class UrnDashboardSource(AbstractDashboardSource):
    """Dashboard source that fetches a JSON dashboard from a repo artifact.

    ``urn`` is a ``urn:artifacts:<uuid>`` reference into Exordos Core's
    repo-artifact registry (``RepoArtifact`` — see
    ``exordos_core/exordos_core/repo/dm/models.py``). Unlike a caller-supplied
    HTTP URL, a URN can only ever resolve to an artifact that a trusted
    ``Repository`` has already indexed — the control plane never dereferences
    an address that came directly from request input, which avoids SSRF.
    """

    KIND = "urn"

    ARTIFACT_URN_NAMESPACE = "artifacts"

    urn = properties.property(
        ra_types.String(min_length=1, max_length=2048), required=True
    )

    def _fetch_dashboard(self, core_client=None) -> dict:
        if core_client is None:
            raise ValueError(
                "UrnDashboardSource requires an authenticated core_client "
                "to resolve the artifact URN"
            )
        if not self.urn.startswith(f"urn:{self.ARTIFACT_URN_NAMESPACE}:"):
            raise ValueError(f"Not an artifact URN: {self.urn!r}")
        matches = core_client.filter("/v1/repo/artifacts/", urn=self.urn)
        if not matches:
            raise LookupError(f"No repo artifact found for urn {self.urn!r}")
        uri = matches[0]["uri"]

        client = bazooka.Client(default_timeout=URL_DASHBOARD_TIMEOUT)
        response = client.get(uri)
        response.raise_for_status()
        return response.json()

    def source_digest(self) -> str:
        return self.urn


class RawDashboardSource(AbstractDashboardSource):
    """Dashboard source that stores the dashboard JSON inline."""

    KIND = "raw"

    content = properties.property(ra_types.Dict(), required=True)

    def _fetch_dashboard(self, core_client=None) -> dict:
        return self.content

    def source_digest(self) -> str:
        return hashlib.sha256(
            json.dumps(self.content, sort_keys=True).encode()
        ).hexdigest()


class BundledDashboardSource(AbstractDashboardSource):
    """Dashboard source that fetches from the Grafana community catalog.

    References a dashboard by its numeric ID on grafana.com. The content
    is downloaded via the catalog API at runtime by the builder (through
    ``dashboard()``).
    """

    KIND = "bundled"

    dashboard_id = properties.property(ra_types.Integer(min_value=1), required=True)
    revision = properties.property(ra_types.Integer(min_value=1), default=1)

    def _catalog_url(self) -> str:
        return (
            f"{GRAFANA_CATALOG_API}/{self.dashboard_id}"
            f"/revisions/{self.revision}/download"
        )

    def _fetch_dashboard(self, core_client=None) -> dict:
        client = bazooka.Client(default_timeout=URL_DASHBOARD_TIMEOUT)
        response = client.get(self._catalog_url())
        response.raise_for_status()
        return response.json()

    def source_digest(self) -> str:
        return f"grafana_{self.dashboard_id}_{self.revision}"
