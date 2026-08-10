# Self-Hosted GitHub Actions Runner

## Why we need this

GitHub Actions free-tier runners are queued behind every other repo on
the shared pool.  In practice this means a 5-minute cron routinely fires
1-3 hours late ("cron lag") -- on the night of 2026-05-04 we observed
the 5-min ODDS-ONLY cron firing **5 times in 4 hours** instead of the
expected 48 times, leaving a STRONG bet ungraded for 58 minutes.

A self-hosted runner is a dedicated worker bound to this repo only.  It
never queues, so crons fire on time every time.

A €4/month VPS handles this load with room to spare.  The system Python
predict cycle peaks at ~500MB RAM; the weekly recalibration peaks at
~2GB; everything else is trivial.

## Provisioning

**Recommended VPS**: Hetzner Cloud **CAX11** (€4.51/mo, ARM, 2 vCPU +
4 GB RAM, EU data centers).  Solid uptime, no IP-block reputation, ARM
wheels for pandas/numpy/scikit-learn are mature.

**Alternatives**:
- Hetzner Cloud CX22 (€4.59/mo, x86, 2 vCPU + 4 GB) -- same as CAX11
  but x86 if you prefer.
- DigitalOcean Basic Droplet ($6/mo, 1 vCPU + 1 GB) -- works but tight
  on RAM during the weekly recal.  Bump to 2 GB ($12/mo) if you see
  OOM kills.
- Vultr Cloud Compute ($6/mo) -- equivalent to DO.

Any **Ubuntu 22.04+ LTS** box with **>= 2 GB RAM** works.  Pick whichever
provider you already have an account with -- the differences for this
workload are within margin of error.

## Setup (~5 minutes)

### 1. SSH into the freshly-provisioned VPS as root

```bash
ssh root@<VPS_IP>
```

### 2. Run the bootstrap script

```bash
curl -fsSL https://raw.githubusercontent.com/joey11600/MLB-first-inning/HEAD/scripts/setup_self_hosted_runner.sh | bash
```

This installs Python 3.13, git, jq, the GH Actions runner binary, and
creates a `runner` service user.  Auto-detects ARM vs x86.  Idempotent
-- safe to re-run if anything fails partway through.

### 3. Get a runner registration token

Open: https://github.com/joey11600/MLB-first-inning/settings/actions/runners/new

Copy the token shown (1-hour TTL; refreshes every time you visit the
page).

### 4. Configure the runner (replace `<TOKEN>`)

```bash
sudo -u runner bash -c 'cd /opt/actions-runner && ./config.sh \
  --url https://github.com/joey11600/MLB-first-inning \
  --token <TOKEN> \
  --labels self-hosted,linux,nrfi \
  --unattended'
```

### 5. Install + start the systemd service

```bash
cd /opt/actions-runner
./svc.sh install runner
./svc.sh start
```

The runner is now running as a systemd unit
(`actions.runner.joey11600-MLB-first-inning.<hostname>.service`) and
will auto-restart on reboot.

### 6. Verify

Open: https://github.com/joey11600/MLB-first-inning/settings/actions/runners

You should see the runner with a green **Idle** status within 30
seconds of step 5.

## Cutover -- switch workflows to use the runner

The workflow files (`daily.yml`, `backup.yml`, `shadow_gate.yml`) read
`runs-on:` from a repo variable that defaults to `ubuntu-latest`:

```yaml
runs-on: ${{ vars.RUNNER_LABEL || 'ubuntu-latest' }}
```

To cut over:

1. Open: https://github.com/joey11600/MLB-first-inning/settings/variables/actions
2. Click **New repository variable**
3. Name: `RUNNER_LABEL`
4. Value: `self-hosted`
5. Click **Add variable**

The next cron fire will run on the VPS.  Watch the live job at
https://github.com/joey11600/MLB-first-inning/actions -- the runner row
should say `self-hosted` rather than `GitHub Actions`.

## Rollback

If anything goes wrong (runner offline, VPS down, weird env quirk):

1. Go to https://github.com/joey11600/MLB-first-inning/settings/variables/actions
2. Delete the `RUNNER_LABEL` variable
3. Workflows fall back to `ubuntu-latest` instantly -- no redeploy, no
   workflow file edit.  GH-hosted runners pick up the next cron fire.

## Operating

| Task | Command |
|---|---|
| Tail runner logs | `journalctl -u 'actions.runner.*' -f` |
| Status | `cd /opt/actions-runner && ./svc.sh status` |
| Restart | `cd /opt/actions-runner && ./svc.sh stop && ./svc.sh start` |
| Stop temporarily | `cd /opt/actions-runner && ./svc.sh stop` |
| Disk usage | `du -sh /opt/actions-runner/_work` (caches checkouts) |
| Update OS | `apt-get update && apt-get upgrade -y` |

The runner self-updates major versions automatically; no maintenance
needed.

### The runner's version gates which ACTIONS this repo may use

Every `actions/*` release from `checkout@v5`, `setup-python@v6` and
`setup-node@v5` onward runs on Node 24, and each of those release notes
states the same floor: **runner v2.327.1 or newer**.  GitHub-hosted
runners are always current, so this constraint is invisible until a
workflow lands on THIS box -- and `daily.yml` (the predict cron) and
`backup.yml` both do, via `runs-on: ${{ vars.RUNNER_LABEL || ... }}`
with `RUNNER_LABEL=self-hosted`.  An action too new for the runner fails
the whole job, which on `daily.yml` means no picks.

Checked before the 2026-08-10 bump to `@v7`: runner `vmi3065305` was on
**2.336.0**, comfortably clear.  Self-update keeps it that way, so this
is a non-issue in normal operation.  It becomes one only if self-update
is ever disabled or the box sits offline for a long stretch -- so if a
job starts failing right after an action version bump, check this first:

```bash
gh api repos/joey11600/MLB-first-inning/actions/runners \
  -q '.runners[] | "\(.name) \(.status) \(.version)"'
```

## Troubleshooting

### Workflow fails with "no runner matches the labels"

Means the runner is offline OR the label doesn't match.  Check:
- `./svc.sh status` shows `active (running)`
- The runner shows `Idle` (green) at the repo's runners page
- `vars.RUNNER_LABEL` value matches one of the runner's labels
  (`self-hosted`, `linux`, or `nrfi` -- any one will work)

### Runner shows offline within minutes of starting

Likely a network issue.  Check:
- Outbound HTTPS to `*.github.com` is allowed (no firewall blocking)
- DNS resolution works: `dig github.com`
- Token wasn't expired when running config.sh (1-hour TTL)

### `setup-python@v5` step takes >2 minutes

The action is downloading Python because it can't find a cached
version.  Either:
- Wait it out -- subsequent runs will use the cache.
- Run a job once, then check `/opt/actions-runner/_work/_tool/Python`
  is populated.  Subsequent jobs reuse this cache.

## Security

**This setup is appropriate ONLY for private repositories.**  Self-hosted
runners on public repos are a known security risk: external PRs can
execute arbitrary code on your VPS.  GitHub even shows a warning about
this when you add a runner to a public repo.

The `joey11600/MLB-first-inning` repository should remain **private**
while using a self-hosted runner.  If you ever make it public, remove
the runner first.

## Cost summary

- **VPS**: €4-7/month depending on provider.
- **GitHub Actions minutes**: now $0/month for the moved jobs -- your
  free 2,000 minutes/month go entirely to other repos or unused.

Net: roughly the same as the pre-self-hosted setup if you were on the
free tier; lower than if you were paying for additional minutes.

## When NOT to bother

If your cron lag tolerance is "anything under 30 minutes is fine," GH-
hosted runners are good enough.  Self-host only if:
- You've measured actual cron lag impacting decisions (we have)
- You can't tolerate >5 min lag on a specific cron (the 5-min DK scrape
  + grade is the canonical case)
- You want predictable cost over a free tier with sporadic spikes
