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

# Run the functional suite on the realm, where its data plane nodes are.
#
# Usage: run-functional-in-realm.sh
#
# The suite talks to the metapaas CP and the victoria and grafana nodes on
# their own addresses, which only the realm's network reaches -- not the
# runner.  So copy this checkout to the realm's core VM over ssh and run tox
# there, against the core on that VM.
#
# SSH_KEY, SSH_HOST, SSH_PORT and ADMIN_PASSWORD come from the environment, as
# exordos_ci's element_realm_test workflow leaves them with `ssh: true`.
set -euo pipefail

: "${SSH_KEY:?SSH_KEY is not set}"
: "${SSH_HOST:?SSH_HOST is not set}"
: "${SSH_PORT:?SSH_PORT is not set}"
: "${ADMIN_PASSWORD:?ADMIN_PASSWORD is not set}"

realm=(ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
       -o BatchMode=yes -o ServerAliveInterval=30 -i "$SSH_KEY"
       -p "$SSH_PORT" "ubuntu@$SSH_HOST")

# The whole checkout, .git included: the package's version comes from git.
echo "Copying the checkout to the realm"
tar -C . -czf - . | "${realm[@]}" \
    'rm -rf ~/observability && mkdir ~/observability && tar -C ~/observability -xzf -'

# Through stdin, not the command line, so it shows in no process list.
printf '%s' "$ADMIN_PASSWORD" | "${realm[@]}" \
    'umask 077 && cat > ~/.observability_admin_password'

"${realm[@]}" bash -s <<'REMOTE'
set -euo pipefail
cd ~/observability

if ! command -v uv > /dev/null && [ ! -x ~/.local/bin/uv ]; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"
sudo apt-get update -q
sudo apt-get install -y -q libev-dev
uv tool install tox --with tox-uv

# The core is served on this VM: through its ingress, which resolves the
# "default" IAM client the suite logs in with.
export EXORDOS_ENDPOINT=http://localhost/api/core
export EXORDOS_USERNAME=admin
EXORDOS_PASSWORD="$(cat ~/.observability_admin_password)"
export EXORDOS_PASSWORD
export EXORDOS_POLL_TIMEOUT=1800

tox -e 3.12-functional
REMOTE
