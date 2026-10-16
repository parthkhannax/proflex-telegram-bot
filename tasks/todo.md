# Proflex Telegram bot → Cloudflare Worker + GitHub Actions + Claude cloud

## Goal / acceptance
- Bot answers commands and runs every scheduled job with the Mac off, at $0.
- Done = Mac copy stopped, a command answered from the cloud, one scheduled job posted from the cloud, admin DM on failure.

## Inventory (2026-10-17, from ~/Desktop/claude-work/proflex-telegram-bot)
| Piece | Today (Mac) | Cloud home | Status |
|---|---|---|---|
| Commands: /levels /near /support /ceiling /sr /stale /status /help | bot.py long-poll | Worker → Action (yfinance) | needs rework |
| /update /undo /staleupdate (edit levels.csv) | levels_edit.py | Worker → Action commits levels.csv | needs rework |
| /week /today /dayahead | macro.py + Chrome render | Action (Chrome on ubuntu runner) | Mac paths to replace |
| /snap /movers /sectors /rotation | snap.py screenshots local servers 8000/5050/11001 | Action starts those 3 servers, screenshots | copy 3 dashboards into repo |
| Sched: snapshots x3, levels_board, levels_summary, scorecard, week_ahead, day_ahead | due_jobs loop | Action cron | needs rework |
| X mirror (xpost.py → proflex-x-post skill scripts) | /Users/... skill paths | vendor scripts into repo | Mac paths to replace |
| stale_reminder_loop (hourly) | thread | Action cron hourly | ok |
| squawk_loop (econ data every 20s) | thread | ? | DECISION |
| tv-levels alerts.py (every 60s) | launchd | ? | DECISION |
| Position alerts (monitor.py, 15 min) | launchd | Action cron | later |

No folder has its own git repo (all inside the home-dir repo) → new repo needed.

## Plan
- [x] 1. Decisions: PUBLIC repo; squawk + levels alerts on Worker cron (1 min, JS port); keep snapshots (Action runs 3 dashboards)
- [x] 2. (75 files, secret scan clean; .tv-profile browser profile removed) Create repo, vendor bot + tv-levels + 3 dashboards + skill scripts, strip secrets
- [x] 3. Make code path-agnostic (HERE-relative, Linux Chrome, no `open`)
- [ ] 4. Secrets into GitHub (bot token, chat ids, Buffer, image host)
- [ ] 5. Actions: scheduled jobs workflow + command workflow (workflow_dispatch)
- [ ] 6. Cloudflare Worker: webhook, chat allowlist, fast replies, dispatch heavy cmds
- [ ] 7. Switch Telegram from long-poll to webhook; stop Mac launchd bot
- [ ] 8. Verify: /status, /sr, one scheduled job, failure DM
- [ ] 9. Results + lessons

## Working notes
- Telegram: webhook and getUpdates can't coexist → Mac bot must stop at cutover (step 7), not before.
- Actions cron runs 5–20 min late; min interval 5 min.
- Private repo = 2,000 free min/month; each job bills ≥1 min.
- Public repo: levels.csv + stale.json + tv_alerts.json are readable by anyone (Parth accepted). Chat IDs, tokens → GitHub/Worker secrets only.
- Excluded from copy: logs (rotation logs were 65 MB), *.bak*, caches, state files, config.json, plists.

- Step 3: CHROME via env/which; vendor/ replaces ~/.claude skill paths; bot/services.json for the 3 dashboards; WA number moved to env; host_image.sh uses ASSETS_TOKEN for x-post-assets push.
