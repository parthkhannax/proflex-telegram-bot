# Lessons

## 2026-10-17 zsh doesn't word-split string variables
- Failure: `X='--exclude=a --exclude=b'; rsync $X ...` passed one bogus arg in zsh, so every exclude was ignored (65 MB of logs copied) and unquoted globs like `--exclude=config*.json` aborted two copies with "no matches found".
- Signal: repo size jumped to 69 MB; "no matches found" in output.
- Rule: in zsh, put flags in an array `X=(--exclude='*.log' ...)` and expand `"${X[@]}"`; always quote glob patterns passed as args. Check `du -sh` after any bulk copy.
