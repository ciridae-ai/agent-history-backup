# claude-history-backup

Backup Claude Code and Codex session history before automatic cleanup removes old sessions. Claude Code removes transcripts older than `cleanupPeriodDays` in `~/.claude/settings.json` (30 days when unset).

## Motivation

The data may be useful in the future. Useful for what? Well, let us focus on collecting the data, ["and then the answer to that question will reveal itself. I think there will be lots of possible answers"](https://www.dwarkesh.com/p/ilya-sutskever-2).

## Install

```bash
pip install claude-history-backup
# or
uv tool install claude-history-backup
```

## Quick Start

```bash
claude-history sync              # Create a local backup archive
claude-history scheduler-install # Install daily scheduler (macOS)
claude-history status            # Check status and when transcripts expire
```

Back up to Google Cloud Storage instead (needs the [Google Cloud CLI](https://cloud.google.com/sdk/docs/install), logged in with `gcloud auth login`):

```bash
claude-history config --remote-destination gs://my-bucket/transcripts
claude-history remote-sync --dry-run          # List what would be copied
claude-history remote-sync                    # Copy new and changed files
claude-history scheduler-install --remote     # Run remote-sync weekly (macOS)
```

## How It Works

```
~/.claude/projects/           →  sync  →   ~/claude_code_history/
                                           ├── backup_20251208_180825.zip
                                           ├── backup_20251210_100000.zip
                                           └── ...
```

Each `sync` creates a new timestamped zip archive of all sessions. Archives accumulate - manage them manually when needed.

```
~/.claude/projects/            →  remote-sync  →  gs://BUCKET/PREFIX/claude-code/projects/
~/.codex/sessions/             →               →  gs://BUCKET/PREFIX/codex/sessions/
~/.codex/archived_sessions/    →               →  gs://BUCKET/PREFIX/codex/archived_sessions/
```

`remote-sync` runs `gcloud storage rsync --recursive` for each folder, so a run copies only new or changed files. It never deletes anything in the bucket: a transcript that Claude Code or Codex cleans up locally stays in the backup. It exits 1 with a message when `gcloud` is missing, not logged in, or no destination is configured, and `status` shows when the last successful run finished.

## Commands

```bash
claude-history status         # Show sync status, oldest transcripts and days until removal
claude-history sync           # Create new backup archive
claude-history remote-sync    # Copy Claude Code and Codex transcripts to Cloud Storage
claude-history list           # List all archives
claude-history config         # View/set backup location and remote destination
claude-history logs           # View scheduler log

# Scheduler (macOS launchd - runs missed jobs on wake)
claude-history scheduler-install                     # Daily at 10 AM: local check and zip
claude-history scheduler-install --remote            # Sundays at 10 AM: remote-sync
claude-history scheduler-install --remote --dry-run  # Print the plist without installing
claude-history scheduler-status
claude-history scheduler-remove
```

The scheduler installs one job; run `scheduler-remove` before switching between the daily and weekly job. Each run's output goes to `~/.claude/history-backup.log`.

## Configuration

```bash
# View current config
claude-history config

# Change backup location
claude-history config --backup-root ~/Dropbox/claude-backups

# Set the Cloud Storage destination for remote-sync
claude-history config --remote-destination gs://my-bucket/transcripts
```

Default: `~/claude_code_history`
Config file: `~/.config/claude-history/config.json`
