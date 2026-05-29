#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

python3.12 -m pytest -q

set +u
source /opt/ros/jazzy/setup.bash
set -u
colcon build --event-handlers console_direct+
colcon test --event-handlers console_direct+

set +u
source install/setup.bash
set -u
hurry --help >/dev/null
hurry init --print >/dev/null
hurry setup usbipd --json >/dev/null || true
hurry scan --json >/dev/null
hurry watch --once --json --no-attach >/dev/null
hurry ros export --format json >/dev/null

echo "software checks passed"
