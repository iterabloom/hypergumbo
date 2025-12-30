# Running hypergumbo on Intel Macs

Some tree-sitter grammar packages lack pre-built wheels for Intel Macs (x86_64 Darwin), causing installation failures. This guide provides a Docker-based workaround that mimics `pipx` behavior.

**Limitation:** Lean and Wolfram analyzers require building grammars from source inside the container, which this setup does not support. All other 49 analyzers work.

## How It Works

1. A Docker image with hypergumbo pre-installed
2. A shell shim at `~/.local/bin/hypergumbo` that runs the container transparently

## Installation

### 1. Build the Docker image (one-time)

```bash
mkdir -p ~/.local/share/hypergumbo-docker
cat > ~/.local/share/hypergumbo-docker/Dockerfile <<'EOF'
FROM python:3.11 AS builder
RUN pip install -U pip wheel
RUN pip wheel --wheel-dir /wheels "hypergumbo==0.6.0"

FROM python:3.11-slim
COPY --from=builder /wheels /wheels
RUN pip install -U pip \
 && pip install --no-index --find-links=/wheels "hypergumbo==0.6.0" \
 && rm -rf /wheels
WORKDIR /work
ENTRYPOINT ["hypergumbo"]
CMD ["--help"]
EOF

docker build -t hypergumbo:0.6.0 ~/.local/share/hypergumbo-docker
```

### 2. Create the PATH shim

```bash
mkdir -p ~/.local/bin ~/.cache/hypergumbo-docker ~/.config/hypergumbo-docker

cat > ~/.local/bin/hypergumbo <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

IMAGE="${HYPERGUMBO_IMAGE:-hypergumbo:0.6.0}"

# Allocate a TTY only if we're in one
TTY=()
if [[ -t 0 && -t 1 ]]; then TTY=(-it); fi

# Host-side cache/config (writable)
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$HOME/.cache/hypergumbo-docker}"
export XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config/hypergumbo-docker}"

# Safety: default to read-only repo unless explicitly disabled
WORK_MOUNT_MODE="ro"
if [[ "${HYPERGUMBO_DOCKER_RO:-1}" == "0" ]]; then
  WORK_MOUNT_MODE="rw"
fi

# Make sure cache/config exist
mkdir -p "$XDG_CACHE_HOME" "$XDG_CONFIG_HOME"

# Mount strategy:
# - HOME read-only (so you can point at things in ~)
# - repo (PWD) with chosen rw/ro
# - cache/config writable
# - /Volumes read-only (optional but handy on macOS)
docker run --rm "${TTY[@]}" \
  --user "$(id -u):$(id -g)" \
  --network=none \
  --cap-drop=ALL \
  --security-opt=no-new-privileges \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=1g \
  -e HOME="$HOME" -e USER="$USER" \
  -e XDG_CACHE_HOME="$XDG_CACHE_HOME" \
  -e XDG_CONFIG_HOME="$XDG_CONFIG_HOME" \
  -v "$HOME":"$HOME:ro" \
  -v "$PWD":"$PWD:$WORK_MOUNT_MODE" \
  -v "$XDG_CACHE_HOME":"$XDG_CACHE_HOME:rw" \
  -v "$XDG_CONFIG_HOME":"$XDG_CONFIG_HOME:rw" \
  -v /Volumes:/Volumes:ro \
  -w "$PWD" \
  "$IMAGE" "$@"
EOF

chmod +x ~/.local/bin/hypergumbo
```

Ensure `~/.local/bin` is in your PATH (add to `~/.zshrc` or `~/.bashrc` if needed):

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## Usage

```bash
hypergumbo --help
hypergumbo .
hypergumbo /Users/you/some/repo -t 500
```

### Write-enabled mode

By default, the repo is mounted read-only. For commands that write (like `hypergumbo init`):

```bash
HYPERGUMBO_DOCKER_RO=0 hypergumbo init
```

### Upgrading

To upgrade to a new version, update the version in both the Dockerfile and shim, then rebuild:

```bash
# Edit ~/.local/share/hypergumbo-docker/Dockerfile (change version)
# Edit ~/.local/bin/hypergumbo (change HYPERGUMBO_IMAGE default)
docker build -t hypergumbo:X.Y.Z ~/.local/share/hypergumbo-docker
```

## Uninstallation

```bash
rm -f ~/.local/bin/hypergumbo
docker rmi -f hypergumbo:0.6.0 2>/dev/null || true
rm -rf ~/.local/share/hypergumbo-docker ~/.cache/hypergumbo-docker ~/.config/hypergumbo-docker
```

## Troubleshooting

### "Operation not permitted" mount errors

Docker Desktop requires explicit permission to mount certain paths. Go to **Docker Desktop → Settings → Resources → File Sharing** and add `$HOME` and `/Volumes` to the allowed list.
