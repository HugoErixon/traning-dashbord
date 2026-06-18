---
description: Ship to the Pi over Tailscale — commit & push local changes, then git pull + restart dashboard
allowed-tools: Bash
---

Ship the current work to the Raspberry Pi. This does the full chain so the user
only has to trigger one thing when they decide a change is done.

The Pi is reached over **Tailscale** (`hugoerixon@100.94.127.20`, MagicDNS name
`raspberrypi`) so deploy works from any network, home or away. The old LAN
address `192.168.1.96` only works on the home WiFi.

## Step 1 — commit & push (only if there are local changes)
Run `git status --porcelain`. If it shows changes:
- Stage everything: `git add -A`
- Commit with a concise message describing the change (end the message with the
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer).
- Push: `git push` (if it's rejected because the remote moved, run
  `git pull --rebase` then `git push` again).

If there are no local changes, skip straight to step 2 (deploy whatever is already on `main`).

## Step 2 — deploy on the Pi (over Tailscale)
Run this single command and report the result:

```
ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20 hugoerixon@100.94.127.20 "cd ~/traning-dashbord && git pull && sudo systemctl restart dashboard && systemctl status dashboard --no-pager | head -n 12"
```

## Then report
- On success: confirm the service is `active (running)` and remind the user to hard-refresh (Ctrl+F5).
- If it times out / can't connect: check that Tailscale is up on this machine and the Pi shows as `active` in `tailscale status` (the Pi must be online on the tailnet).
- If sudo hangs on a password prompt, tell the user passwordless sudo for `systemctl restart dashboard` isn't configured.
- If `git pull` on the Pi reports local Pi-side changes blocking the merge, surface that — do NOT discard them without asking.
