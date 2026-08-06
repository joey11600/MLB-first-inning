#!/usr/bin/env bash
# ============================================================
# diagnose_runner_host.sh -- read-only post-mortem for the
# self-hosted GitHub Actions runner box.
# ============================================================
#
# WHY: on 2026-08-06 the runner (Contabo vmi3065305 /
# 217.216.81.119) stopped answering GitHub at ~16:10 UTC. Run
# #3184 waited 16m57s for a machine and GitHub gave up:
# "The job was not acquired by Runner of type self-hosted even
# after multiple attempts."  The runner came back on its own by
# ~17:09 UTC.  WHY it went away is still unknown, and the answer
# only exists in this box's own logs.
#
# Run it ON THE BOX:
#   curl -fsSL https://raw.githubusercontent.com/joey11600/MLB-first-inning/HEAD/scripts/diagnose_runner_host.sh | sudo bash
#
# Or paste the file over and: sudo bash diagnose_runner_host.sh
#
# READ-ONLY. It changes nothing, starts nothing, stops nothing.
# Every command is an inspection. Safe to run while jobs are live.
#
# Redirect to a file and send it back:
#   ... | sudo bash > runner_diag.txt 2>&1
# ============================================================

# Window of interest. Override for a different incident:
#   INCIDENT_DATE=2026-08-07 FROM=14:00 TO=18:00 sudo bash ...
INCIDENT_DATE="${INCIDENT_DATE:-2026-08-06}"
FROM="${FROM:-15:30}"
TO="${TO:-17:30}"

line() { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }

line "WHO AM I"
hostname; uname -a; date -u '+now: %Y-%m-%d %H:%M:%S UTC'
echo "timezone: $(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null || echo '?')"
echo "NOTE: journalctl times below are LOCAL to this box unless it is set to UTC."

line "DID IT REBOOT? (a reboot explains everything)"
uptime
last reboot 2>/dev/null | head -5
echo "-- unclean shutdowns / crashes --"
journalctl --list-boots 2>/dev/null | tail -5

line "OUT OF MEMORY? (weekly recalibrate peaks ~2GB)"
free -h
echo "-- OOM killer activity (all boots) --"
(journalctl -k --no-pager 2>/dev/null | grep -iE 'out of memory|oom-killer|killed process' | tail -20) || echo "  none found"
echo "-- dmesg OOM --"
(dmesg -T 2>/dev/null | grep -iE 'oom|killed process' | tail -10) || echo "  none found (or dmesg restricted)"

line "DISK FULL? (a full disk silently breaks the runner)"
df -h / /var /home 2>/dev/null | sort -u
echo "-- runner work dir --"
du -sh /home/*/actions-runner/_work 2>/dev/null || echo "  (not found)"

line "RUNNER SERVICE STATE"
systemctl list-units --type=service --all 2>/dev/null | grep -i 'actions.runner' || echo "  no actions.runner unit found"
for u in $(systemctl list-units --type=service --all --plain --no-legend 2>/dev/null | awk '/actions\.runner/{print $1}'); do
  echo "-- $u --"
  systemctl status "$u" --no-pager -l 2>/dev/null | head -20
done

line "RUNNER LOGS AROUND THE INCIDENT ($INCIDENT_DATE $FROM-$TO)"
journalctl -u 'actions.runner.*' --since "$INCIDENT_DATE $FROM" --until "$INCIDENT_DATE $TO" --no-pager 2>/dev/null | tail -120 \
  || echo "  (no journal entries -- runner may log to _diag/ instead)"

line "RUNNER'S OWN DIAGNOSTIC LOGS (_diag)"
# The runner writes its own rolling logs; these often carry the
# real reason (listener disconnect, token refresh failure, etc.)
for d in /home/*/actions-runner/_diag /opt/actions-runner/_diag /root/actions-runner/_diag; do
  [ -d "$d" ] || continue
  echo "-- $d (most recent 2 files, last 60 lines each) --"
  for f in $(ls -1t "$d"/*.log 2>/dev/null | head -2); do
    echo "---- $f ----"; tail -60 "$f"
  done
done

line "NETWORK / CONNECTIVITY TO GITHUB"
(curl -s -o /dev/null -w '  api.github.com  HTTP %{http_code}  connect %{time_connect}s  total %{time_total}s\n' https://api.github.com/ --max-time 15) || echo "  curl to api.github.com FAILED"
(curl -s -o /dev/null -w '  pipelines (runner control plane) HTTP %{http_code}  total %{time_total}s\n' https://pipelines.actions.githubusercontent.com/ --max-time 15) || echo "  curl to pipelines FAILED"
echo "-- DNS --"; (getent hosts api.github.com || echo "  DNS lookup FAILED")

line "SSH BRUTE-FORCE PRESSURE (password auth is ENABLED on this box)"
# Hypothesis worth ruling in/out: an internet-facing box with
# password auth open takes constant login attempts, which can
# starve a small VPS of CPU/entropy/file handles.
for f in /var/log/auth.log /var/log/secure; do
  [ -f "$f" ] || continue
  echo "-- $f --"
  echo "  failed logins today: $(grep -c 'Failed password' "$f" 2>/dev/null || echo 0)"
  echo "  top attacking IPs:"
  grep 'Failed password' "$f" 2>/dev/null | grep -oE 'from [0-9.]+' | sort | uniq -c | sort -rn | head -5 | sed 's/^/    /'
done
command -v fail2ban-client >/dev/null && { echo "-- fail2ban --"; fail2ban-client status sshd 2>/dev/null | sed 's/^/  /'; } || echo "  fail2ban: NOT installed"

line "RESOURCE PRESSURE RIGHT NOW"
echo "-- load --"; cat /proc/loadavg
echo "-- top 8 by memory --"; ps aux --sort=-%mem 2>/dev/null | head -9
echo "-- pressure stall (if kernel supports) --"
for p in cpu memory io; do [ -r "/proc/pressure/$p" ] && echo "  $p: $(head -1 /proc/pressure/$p)"; done

line "SYSTEM ERRORS IN THE WINDOW"
journalctl --since "$INCIDENT_DATE $FROM" --until "$INCIDENT_DATE $TO" -p err --no-pager 2>/dev/null | tail -40 \
  || echo "  none"

line "DONE"
echo "Send this whole output back. The decisive sections are:"
echo "  'DID IT REBOOT', 'OUT OF MEMORY', 'RUNNER LOGS AROUND THE INCIDENT', and '_diag'."
