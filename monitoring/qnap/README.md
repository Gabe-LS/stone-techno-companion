# Health monitor on QNAP (Container Station)

Runs `monitor.sh` hourly from the always-on NAS. External probes (HTTP,
TLS, latency) plus VPS internal checks over SSH; failures push to the
phone via ntfy.sh.

The container **auto-updates `monitor.sh` from GitHub** before each run,
so pushing a fix to the repo is enough (no manual copy, no rebuild). If
the fetch fails (GitHub down, network issue), the cached copy runs instead.

## Setup

1. Copy this folder to a share on the QNAP, for example
   `/share/Container/stc-monitor/`:

   ```
   scp -r monitoring/qnap/* admin@<qnap>:/share/Container/stc-monitor/
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

3. If the GitHub repo is private, uncomment `GITHUB_TOKEN` in
   `docker-compose.yml` and set a read-only personal access token.

4. Build and start (Container Station's "Create Application" with this
   compose file, or over SSH):

   ```
   cd /share/Container/stc-monitor
   docker compose up -d --build
   ```

5. Verify: `docker logs stc-monitor` shows one full check run at startup.
   All lines should be OK. Test the alert pipeline once:

   ```
   docker exec stc-monitor /app/monitor.sh --test-alert
   ```

## Updating

Push changes to `monitor.sh` in the repo. The NAS picks them up on the
next hourly run (no rebuild, no manual copy).

To update the container infrastructure (Dockerfile, entrypoint, compose):

```
scp -r monitoring/qnap/* admin@<qnap>:/share/Container/stc-monitor/
ssh admin@<qnap> "cd /share/Container/stc-monitor && docker compose up -d --build"
```

## Notes

- The entrypoint copies SSH keys from the read-only mount and chmods them
  600, because QNAP share permissions are usually too open for ssh.
- The hourly log is at `logs/monitor.log` (only failures are logged thanks
  to `--quiet`; the script self-rotates it at ~512 KB).
