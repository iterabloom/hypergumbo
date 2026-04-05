<!-- SPDX-License-Identifier: MPL-2.0 -->
# htrac serve Deployment Guide

This guide covers deploying `htrac serve` with systemd process supervision
and Tor onion service for remote access (ADR-0019 Part A).

## Architecture

```
[iOS/macOS app] ──Tor──▶ [Tor onion service] ──127.0.0.1:7380──▶ [htrac serve]
                                                                      │
                                                                      ▼
                                                                [TrackerSet]
                                                                  ◄──────►
                                                                [CLI / TUI]
```

- `htrac serve` binds to `127.0.0.1:7380` (never `0.0.0.0`)
- Tor daemon publishes an onion service forwarding to the local listener
- iOS/macOS native app connects via Tor to the onion address
- CLI and TUI coexist via filesystem-level locking (flock + inotify)

## Prerequisites

```bash
# Create dedicated user
sudo useradd -r -m -s /bin/bash htrac

# Install hypergumbo-tracker
sudo -u htrac pip install --user hypergumbo-tracker

# Initialize tracker
sudo -u htrac htrac init /home/htrac/.agent

# Install Tor
sudo apt install tor
```

## systemd Setup

```bash
# Copy unit file
sudo cp deploy/htrac-serve.service /etc/systemd/system/

# Reload and enable
sudo systemctl daemon-reload
sudo systemctl enable --now htrac-serve

# Check status
sudo systemctl status htrac-serve
sudo journalctl -u htrac-serve -f
```

The unit file includes security hardening (NoNewPrivileges, ProtectSystem,
MemoryDenyWriteExecute, etc.) and automatic restart on crash.

## Tor Onion Service

Configure Tor to forward to the htrac local listener:

```bash
# Add to /etc/tor/torrc:
HiddenServiceDir /var/lib/tor/htrac-serve/
HiddenServicePort 7380 127.0.0.1:7380

# Restart Tor
sudo systemctl restart tor

# Get the onion address
sudo cat /var/lib/tor/htrac-serve/hostname
```

The onion address is the endpoint the iOS/macOS app connects to.

## Configuration

Auth settings are in the tracker's `config.yaml` under the `auth` section:

```yaml
auth:
  session_ttl_minutes: 15        # Fixed window, no interaction reset
  duress_module: null             # Path to custom duress handler (gitignored)
  rate_limit:
    base_delay: 2.0              # Seconds, doubles on each failure
    max_failures: 10             # Lock credential after this many
  webauthn:
    rp_id: "your-onion-address.onion"
    rp_name: "My Tracker"
    origin: "https://your-onion-address.onion"
```

## Health Check

```bash
curl http://127.0.0.1:7380/health
# {"status": "ok", "pid": 12345, "uptime_seconds": 3600.5}
```

## CLI Coexistence

The CLI, TUI, and serve all share the same TrackerSet via filesystem
locking. No IPC needed. When the CLI writes ops files, the serve
filesystem watcher detects the change and pushes a `state_snapshot`
to all connected WebSocket clients within sub-second latency.
