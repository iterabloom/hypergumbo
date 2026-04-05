- **vPR Queue (offline resilience):**
  - When remote is unavailable, `auto-pr` queues as a vPR (virtual PR) in `.git/PR_QUEUE`.
  - vPRs form a linear chain: each new vPR branches from the previous one.
  - Flush pushes ALL vPRs as a single atomic PR (no race conditions with other contributors).
  - Commands:
    - `./scripts/auto-pr list` — Show queued vPRs
    - `./scripts/auto-pr status` — Show queue status and next steps
    - `./scripts/auto-pr flush` — Push all vPRs as single PR
  - To add more changes while queue is non-empty:
    ```bash
    tip=$(./scripts/auto-pr status | grep "Queue tip" | awk '{print $3}')
    git checkout -b author/feat/next-change "$tip"
    ```
