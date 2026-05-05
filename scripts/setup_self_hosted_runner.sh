#!/usr/bin/env bash
# scripts/setup_self_hosted_runner.sh
#
# Bootstrap a GitHub Actions self-hosted runner on Ubuntu 22.04+ for the
# MLB First-Inning predictor.  Eliminates GH Actions free-tier cron lag
# (observed 5x fires in 4 hours instead of the expected 48x) by giving
# the repo a dedicated runner that's not queued behind every public repo
# on the shared pool.
#
# Run this on a freshly-provisioned VPS as root:
#   curl -fsSL https://raw.githubusercontent.com/joey11600/MLB-first-inning/HEAD/scripts/setup_self_hosted_runner.sh | bash
#
# Architecture: auto-detects x86_64 vs aarch64 (works on Hetzner CAX11
# ARM, Hetzner CX11 x86, DO droplets, Vultr, etc.).
#
# What this script does (system prep only):
#   1. Installs system deps: Python 3.13, git, jq, build tools
#   2. Creates a `runner` service user
#   3. Downloads the latest GH Actions runner binary into /opt/actions-runner
#
# What you do AFTER this script (manual; needs a one-time token):
#   4. Get a runner registration token from
#      https://github.com/joey11600/MLB-first-inning/settings/actions/runners/new
#   5. Run the printed config.sh + svc.sh commands as the runner user
#   6. Set repo variable RUNNER_LABEL=self-hosted to cut over

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/joey11600/MLB-first-inning}"
RUNNER_USER="${RUNNER_USER:-runner}"
RUNNER_DIR="${RUNNER_DIR:-/opt/actions-runner}"
RUNNER_LABELS="${RUNNER_LABELS:-self-hosted,linux,nrfi}"

if [[ "$EUID" -ne 0 ]]; then
  echo "Run this script as root (sudo $0)" >&2
  exit 1
fi

echo "==> Phase 1: system deps (Python 3.13, git, build tools)..."
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y \
  curl jq git build-essential libssl-dev libffi-dev \
  software-properties-common ca-certificates pkg-config

# Python 3.13 from deadsnakes -- works on both ARM and x86 Ubuntu.
# The workflow files use actions/setup-python@v5 which finds the system
# Python first; pre-installing here makes that step instant rather than
# having setup-python download + extract a tarball on every job.
add-apt-repository -y ppa:deadsnakes/ppa
apt-get update
apt-get install -y python3.13 python3.13-dev python3.13-venv python3-pip

# Make `python3` and `python` resolve to 3.13 so any bare `python` in a
# workflow Just Works.  Existing python3 is preserved as a lower-priority
# alternative so apt-managed packages don't break.
update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.13 50 || true
ln -sf /usr/bin/python3.13 /usr/local/bin/python3
ln -sf /usr/bin/python3.13 /usr/local/bin/python

echo "==> Phase 2: runner service user..."
if ! id "$RUNNER_USER" &>/dev/null; then
  useradd -m -s /bin/bash "$RUNNER_USER"
  echo "    created user: $RUNNER_USER"
else
  echo "    user $RUNNER_USER already exists"
fi
mkdir -p "$RUNNER_DIR"
chown -R "$RUNNER_USER:$RUNNER_USER" "$RUNNER_DIR"

echo "==> Phase 3: GH Actions runner binary..."
ARCH=$(uname -m)
case "$ARCH" in
  x86_64)  RUNNER_PKG_ARCH="x64"   ;;
  aarch64) RUNNER_PKG_ARCH="arm64" ;;
  *) echo "Unsupported arch: $ARCH" >&2; exit 1 ;;
esac

# Latest runner version -- the runner auto-updates after this initial
# install, so whatever's current at provisioning time is fine.
LATEST=$(curl -fsSL https://api.github.com/repos/actions/runner/releases/latest \
  | jq -r .tag_name | sed 's/^v//')
URL="https://github.com/actions/runner/releases/download/v${LATEST}/actions-runner-linux-${RUNNER_PKG_ARCH}-${LATEST}.tar.gz"

echo "    downloading runner v${LATEST} (${RUNNER_PKG_ARCH})..."
cd "$RUNNER_DIR"
sudo -u "$RUNNER_USER" curl -fsSL -o runner.tar.gz "$URL"
sudo -u "$RUNNER_USER" tar xzf runner.tar.gz
rm -f runner.tar.gz

# Install any runner-side OS deps the binary needs.
if [[ -f ./bin/installdependencies.sh ]]; then
  ./bin/installdependencies.sh
fi

cat <<EOF

================================================================
System prep complete.  Now finish registration manually:

1. Get a registration token (1-hour TTL) from:
   $REPO_URL/settings/actions/runners/new

2. Run this on the VPS, replacing <TOKEN>:

   sudo -u $RUNNER_USER bash -c 'cd $RUNNER_DIR && ./config.sh \\
     --url $REPO_URL \\
     --token <TOKEN> \\
     --labels $RUNNER_LABELS \\
     --unattended'

3. Install + start the systemd service so the runner survives reboot:

   cd $RUNNER_DIR
   ./svc.sh install $RUNNER_USER
   ./svc.sh start

4. Verify the runner shows "Idle" (green) at:
   $REPO_URL/settings/actions/runners

5. Cut over the workflows by setting a repo variable:
   - Go to: $REPO_URL/settings/variables/actions
   - Name:  RUNNER_LABEL
   - Value: self-hosted
   - Click "Add variable"

   The next cron fire will run on this VPS instead of GitHub's pool.
   To roll back, just delete the variable -- workflows fall back to
   ubuntu-latest automatically.

================================================================
EOF
