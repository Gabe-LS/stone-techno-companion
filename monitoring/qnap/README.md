# Health monitor on QNAP (Container Station)

Runs `monitor.sh` hourly from the always-on NAS instead of the Mac, so
monitoring does not stop when the Mac sleeps. External probes (HTTP, TLS,
latency) plus VPS internal checks over SSH; failures push to the phone via
ntfy.sh exactly as on the Mac.

## Setup

1. Copy this folder to a share on the QNAP, for example
   `/share/Container/stc-monitor/`, and copy the repo's `monitor.sh` into it:

   ```
   scp monitor.sh monitoring/qnap/* admin@<qnap>:/share/Container/stc-monitor/
   ```

2. On the QNAP, generate a dedicated SSH key for the monitor and authorize
   it on the VPS (key lives on the data volume, so it survives reboots):

   ```
   cd /share/Container/stc-monitor
   mkdir -p ssh logs
   ssh-keygen -t ed25519 -f ssh/id_ed25519 -N "" -C "stc-monitor@qnap"
   cat ssh/id_ed25519.pub
   # append that line to /root/.ssh/authorized_keys on the VPS
   ```

3. Build and start (Container Station's "Create Application" with this
   compose file, or over SSH):

   ```
   cd /share/Container/stc-monitor
   docker compose up -d --build
   ```

4. Verify: `docker logs stc-monitor` shows one full check run at startup.
   All lines should be OK. Test the alert pipeline once:

   ```
   docker exec stc-monitor /app/monitor.sh --test-alert
   ```

## Notes

- The entrypoint copies the key out of the read-only mount and chmods it
  600, because QNAP share permissions are usually too open for ssh.
- `monitor.sh` is bind-mounted: to update it, overwrite the file and the
  next cron run uses it (no rebuild). Changing the schedule or timezone
  needs `docker compose up -d` after editing the compose file.
- The hourly log is at `logs/monitor.log` (only failures are logged thanks
  to `--quiet`; the script self-rotates it at ~512 KB).
- Once this runs on the QNAP, the Mac crontab entry is redundant; keep at
  most one of the two to avoid duplicate alerts.
