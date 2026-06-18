---
description: Ship to the Pi — commit & push local changes, then git pull + restart dashboard
allowed-tools: Bash
---

Ship the current work to the Raspberry Pi. This does the full chain so the user
only has to trigger one thing when they decide a change is done.

## Step 1 — commit & push (only if there are local changes)
Run `git status --porcelain`. If it shows changes:
- Stage everything: `git add -A`
- Commit with a concise message describing the change (end the message with the
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer).
- Push: `git push` (if it's rejected because the remote moved, run
  `git pull --rebase` then `git push` again).

If there are no local changes, skip straight to step 2 (deploy whatever is already on `main`).

## Step 2 — deploy on the Pi
Run this single command and report the result:

```
ssh hugoerixon@192.168.1.96 "cd ~/traning-dashbord && git pull && sudo systemctl restart dashboard && systemctl status dashboard --no-pager | head -n 12"
```

## Then report
- On success: confirm the service is `active (running)` and remind the user to hard-refresh (Ctrl+F5).
- If SSH or sudo hangs on a password prompt, tell the user the key/NOPASSWD setup isn't in place.
- If `git pull` on the Pi reports local Pi-side changes blocking the merge, surface that — do NOT discard them without asking.
