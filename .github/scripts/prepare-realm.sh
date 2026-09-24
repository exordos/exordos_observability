#!/usr/bin/env bash

# Copyright 2026 Genesis Corporation
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

# Get a realm ready for the observability elements: run with the CLI pointed at
# the realm, before they are installed.
#
# Usage: prepare-realm.sh
#
# - Overbook the realm's hypervisor.  A warm realm is sized for the pool's
#   defaults, and the metapaas CP plus the victoria and grafana data planes do
#   not all fit on it one to one -- the runner test overbooked the same way.
# - Install metapaas, which victoriaaas and grafanaaas plug into, unless the
#   realm already has it, and wait until it is ACTIVE.  Its CP image is
#   net-booted into its VM, which alone took ~18 min on a GitHub runner.
set -euo pipefail

here="$(dirname "$0")"

uuid="$(exordos c h l -o json | jq -r '.[0].uuid // ""')"
if [ -z "$uuid" ]; then
    echo "The realm reports no hypervisor to overbook" >&2
    exit 1
fi
exordos compute hypervisors update "$uuid" --cores-ratio 10.0 --ram-ratio 10.0

if [ -z "$(exordos ee l -o json -f name=metapaas | jq -r '.[0].status // ""')" ]; then
    exordos e e install metapaas
else
    echo "The realm already has metapaas"
fi
"$here/wait-for-element.sh" metapaas 2700
exordos e e show metapaas
