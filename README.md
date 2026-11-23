# Agent History Backup

Backs up Claude Code and Codex transcripts before automatic cleanup removes them. Claude Code deletes transcripts older than `cleanupPeriodDays` in `~/.claude/settings.json` (30 days when unset).

`remote-sync` copies both Claude Code (`~/.claude/projects`) and Codex (`~/.codex/sessions`, `~/.codex/archived_sessions`) transcripts to Google Cloud Storage. The older local `sync` zips Claude Code transcripts only.

## Motivation

The data may be useful in the future. Useful for what? Well, let us focus on collecting the data, ["and then the answer to that question will reveal itself. I think there will be lots of possible answers"](https://www.dwarkesh.com/p/ilya-sutskever-2).

## Install

```bash
git clone https://github.com/ciridae-ai/agent-history-backup
cd agent-history-backup
uv tool install .
```

## Quick Start

```bash
agent-history sync              # Create a local backup archive
agent-history scheduler-install # Install daily scheduler (macOS)
agent-history status            # Check status and when transcripts expire
```

Back up to Google Cloud Storage instead (needs the [Google Cloud CLI](https://cloud.google.com/sdk/docs/install), logged in with `gcloud auth login`):

```bash
agent-history config --remote-destination gs://my-bucket/transcripts
agent-history remote-sync --dry-run          # List what would be copied
agent-history remote-sync                    # Copy new and changed files
agent-history scheduler-install --remote     # Run remote-sync weekly (macOS)
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
agent-history status         # Show sync status, oldest transcripts and days until removal
agent-history sync           # Create new backup archive
agent-history remote-sync    # Copy Claude Code and Codex transcripts to Cloud Storage
agent-history list           # List all archives
agent-history config         # View/set backup location and remote destination
agent-history logs           # View scheduler log

# Scheduler (macOS launchd - runs missed jobs on wake)
agent-history scheduler-install                     # Daily at 10 AM: local check and zip
agent-history scheduler-install --remote            # Sundays at 10 AM: remote-sync
agent-history scheduler-install --remote --dry-run  # Print the plist without installing
agent-history scheduler-status
agent-history scheduler-remove
```

The scheduler installs one job; run `scheduler-remove` before switching between the daily and weekly job. Each run's output goes to `~/Library/Logs/agent-history-backup.log`; `agent-history logs` shows its end.

## Configuration

```bash
# View current config
agent-history config

# Change backup location
agent-history config --backup-root ~/Dropbox/claude-backups

# Set the Cloud Storage destination for remote-sync
agent-history config --remote-destination gs://my-bucket/transcripts
```

Default backup location: `~/claude_code_history` (local zips hold Claude Code transcripts only)
Config file: `~/.config/agent-history-backup/config.json`
Sync metadata (last local and remote sync): `~/.config/agent-history-backup/sync_meta.json`
Scheduler: launchd job `com.ciridae.agent-history-backup` in `~/Library/LaunchAgents/com.ciridae.agent-history-backup.plist`
