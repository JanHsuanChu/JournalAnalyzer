#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -f ".connect.env" ]]; then
  echo "Missing .connect.env. Create it with:"
  echo "  CONNECT_SERVER=https://connect.systems-apps.com/connect"
  echo "  CONNECT_API_KEY=..."
  exit 1
fi

set -a
source ".connect.env"
set +a

python3 -m pip install --upgrade --target ./.connect_python_pkgs rsconnect-python

PYTHONPATH="./.connect_python_pkgs" python3 -m rsconnect.main write-manifest shiny . --overwrite
PYTHONPATH="./.connect_python_pkgs" python3 -m rsconnect.main deploy shiny --server "$CONNECT_SERVER" --api-key "$CONNECT_API_KEY" --insecure .
