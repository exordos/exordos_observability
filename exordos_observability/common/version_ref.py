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

"""Version reference URN utilities for observability plugin versions.

A version_ref is a URN string encoding the version slug, uuid, and image URL
in the format::

    urn:exordos:<slug>:<uuid>:<image>

For example::

    urn:exordos:victoria:12345678-1234-...:https://repo.example.com/img.raw.zst

The image is always the last colon-separated component, so URLs containing
colons (e.g. ``https://``) are preserved intact when parsed with
``split(':', 4)``.
"""

URN_PREFIX = "urn:exordos"
IMAGE_URN_PREFIX = "urn:images"


def normalize_image(image: str) -> str:
    """Normalize an image reference for embedding into a version_ref URN.

    If *image* is itself a URN (``urn:images:<uuid>``), extract just the
    UUID so the version_ref doesn't contain a nested URN.  HTTP(S) URLs and
    bare values are returned unchanged.
    """
    if image.startswith(IMAGE_URN_PREFIX + ":"):
        return image[len(IMAGE_URN_PREFIX) + 1 :]
    return image


def build_version_ref(slug: str, uuid: str, image: str) -> str:
    """Build a version_ref URN from its components."""
    return f"{URN_PREFIX}:{slug}:{uuid}:{normalize_image(image)}"


def parse_version_ref(version_ref: str) -> tuple[str, str, str]:
    """Parse a version_ref URN into (slug, uuid, image).

    Raises ValueError if the string is not a valid exordos version_ref URN.
    """
    parts = version_ref.split(":", 4)
    if len(parts) < 5 or f"{parts[0]}:{parts[1]}" != URN_PREFIX:
        raise ValueError(f"Invalid version_ref URN: {version_ref!r}")
    _slug, _uuid, _image = parts[2], parts[3], parts[4]
    if not _slug or not _uuid or not _image:
        raise ValueError(f"Invalid version_ref URN: {version_ref!r}")
    return _slug, _uuid, _image


def parse_image(version_ref: str) -> str:
    """Extract the image component from a version_ref URN.

    Returns the normalized image (bare UUID for ``urn:images`` references,
    HTTP URL as-is).  Use :func:`parse_disk_image` when the result is meant
    for a disk spec that expects the full ``urn:images:<uuid>`` form.
    """
    return parse_version_ref(version_ref)[2]


def parse_disk_image(version_ref: str) -> str:
    """Extract the image in the format expected by disk specs.

    Bare UUIDs (from normalized ``urn:images`` references) are wrapped back
    into ``urn:images:<uuid>``.  HTTP(S) URLs are returned unchanged.
    """
    image = parse_image(version_ref)
    if image.startswith(("http://", "https://")):
        return image
    return f"{IMAGE_URN_PREFIX}:{image}"
