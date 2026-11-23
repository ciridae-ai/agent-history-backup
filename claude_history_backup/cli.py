#!/usr/bin/env python3
"""CLI for backing up Claude Code and Codex session history.

Creates timestamped zip archives of ~/.claude/projects/ locally, and copies
Claude Code and Codex transcripts incrementally to Google Cloud Storage,
before automatic cleanup removes old sessions.
"""

import fnmatch
import json
import os
import plistlib
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import NamedTuple, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

app = typer.Typer(
    name="claude-history",
    help="Backup and manage Claude Code session history",
    add_completion=True,
    no_args_is_help=True,
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help"]},
)
console = Console()

# Paths
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
CONFIG_FILE = Path.home() / ".config" / "claude-history" / "config.json"
DEFAULT_BACKUP_ROOT = Path.home() / "claude_code_history"
CODEX_SESSIONS = Path.home() / ".codex" / "sessions"
CODEX_ARCHIVED_SESSIONS = Path.home() / ".codex" / "archived_sessions"
CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"

# Threshold for triggering sync (days)
SYNC_THRESHOLD_DAYS = 3

# Claude Code's retention when settings.json has no cleanupPeriodDays
DEFAULT_CLAUDE_CLEANUP_PERIOD_DAYS = 30


class TranscriptSource(NamedTuple):
    """A local folder of agent transcripts and where it is copied under the remote destination."""

    name: str
    path: Path
    remote_folder: str
    transcript_file_pattern: str


def get_transcript_sources() -> list[TranscriptSource]:
    """Every local transcript folder that remote-sync copies."""
    return [
        TranscriptSource("Claude Code", CLAUDE_PROJECTS, "claude-code/projects", "*.jsonl"),
        TranscriptSource("Codex", CODEX_SESSIONS, "codex/sessions", "rollout-*"),
        TranscriptSource(
            "Codex archived", CODEX_ARCHIVED_SESSIONS, "codex/archived_sessions", "rollout-*"
        ),
    ]


def load_config() -> dict:
    """Load config from file."""
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {}


def save_config(config: dict) -> None:
    """Save config to file."""
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2))


def get_backup_root() -> Path:
    """Get the backup root directory from config or default."""
    config = load_config()
    return Path(config.get("backup_root", str(DEFAULT_BACKUP_ROOT)))


def get_meta_file() -> Path:
    return get_backup_root() / ".sync_meta.json"


def info(text: str) -> str:
    return f"[cyan]ℹ {text}[/cyan]"


def error(text: str) -> str:
    return f"[red]✗ {text}[/red]"


def success(text: str) -> str:
    return f"[green]✓ {text}[/green]"


def warning(text: str) -> str:
    return f"[yellow]⚠ {text}[/yellow]"


def get_oldest_session_date(path: Path) -> Optional[datetime]:
    """Get the modification time of the oldest session directory."""
    if not path.exists():
        return None

    oldest = None
    for item in path.iterdir():
        if item.is_dir():
            mtime = datetime.fromtimestamp(item.stat().st_mtime)
            if oldest is None or mtime < oldest:
                oldest = mtime
    return oldest


def get_newest_session_date(path: Path) -> Optional[datetime]:
    """Get the modification time of the newest session directory."""
    if not path.exists():
        return None

    newest = None
    for item in path.iterdir():
        if item.is_dir():
            mtime = datetime.fromtimestamp(item.stat().st_mtime)
            if newest is None or mtime > newest:
                newest = mtime
    return newest


def load_meta() -> dict:
    """Load sync metadata."""
    meta_file = get_meta_file()
    if meta_file.exists():
        return json.loads(meta_file.read_text())
    return {}


def save_meta(meta: dict) -> None:
    """Save sync metadata."""
    meta_file = get_meta_file()
    meta_file.parent.mkdir(parents=True, exist_ok=True)
    meta_file.write_text(json.dumps(meta, indent=2, default=str))


def count_sessions(path: Path) -> int:
    """Count session directories."""
    if not path.exists():
        return 0
    return sum(1 for item in path.iterdir() if item.is_dir())


def get_dir_size(path: Path) -> str:
    """Get human-readable directory size."""
    try:
        result = subprocess.run(
            ["du", "-sh", str(path)],
            capture_output=True,
            text=True,
        )
        return result.stdout.split()[0] if result.returncode == 0 else "?"
    except Exception:
        return "?"


def get_archives() -> list[Path]:
    """Get list of archive zip files sorted by date (newest first)."""
    backup_root = get_backup_root()
    if not backup_root.exists():
        return []
    return sorted(backup_root.glob("backup_*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)


def count_transcripts_and_find_oldest(path: Path, file_pattern: str) -> tuple[int, datetime | None]:
    """Count transcript files under path and return the oldest file modification time.

    Only file metadata is read, never file contents.
    """
    count = 0
    oldest_mtime = None
    for directory, _, file_names in os.walk(path):
        for file_name in fnmatch.filter(file_names, file_pattern):
            mtime = os.stat(os.path.join(directory, file_name)).st_mtime
            count += 1
            if oldest_mtime is None or mtime < oldest_mtime:
                oldest_mtime = mtime
    return count, datetime.fromtimestamp(oldest_mtime) if oldest_mtime is not None else None


def get_claude_cleanup_period_days() -> int:
    """Read Claude Code's transcript retention (cleanupPeriodDays) from its settings.json."""
    if not CLAUDE_SETTINGS.exists():
        return DEFAULT_CLAUDE_CLEANUP_PERIOD_DAYS
    settings = json.loads(CLAUDE_SETTINGS.read_text())
    return int(settings.get("cleanupPeriodDays", DEFAULT_CLAUDE_CLEANUP_PERIOD_DAYS))


def print_transcript_expiry() -> None:
    """Print, per transcript source, the oldest transcript and when Claude Code would remove it."""
    cleanup_period_days = get_claude_cleanup_period_days()
    table = Table(title="Transcript Expiry")
    table.add_column("Source", style="cyan")
    table.add_column("Transcripts", style="white")
    table.add_column("Oldest", style="white")
    table.add_column("Retention", style="white")
    table.add_column("Days until oldest is removed", style="white")

    for source in get_transcript_sources():
        count, oldest = count_transcripts_and_find_oldest(
            source.path, source.transcript_file_pattern
        )
        oldest_text = oldest.strftime("%Y-%m-%d %H:%M") if oldest else "N/A"
        if source.path != CLAUDE_PROJECTS:
            table.add_row(source.name, str(count), oldest_text, "no automatic cleanup known", "-")
            continue
        if oldest is None:
            days_left_text = "-"
        else:
            days_left = cleanup_period_days - (datetime.now() - oldest).days
            days_left_text = str(days_left) if days_left > 0 else "due now"
        table.add_row(
            source.name, str(count), oldest_text, f"{cleanup_period_days} days", days_left_text
        )

    console.print(table)
    last_remote_sync = load_meta().get("last_remote_sync_finished")
    if last_remote_sync:
        console.print(info(f"Last successful remote sync finished: {last_remote_sync}"))
    else:
        console.print(warning("No successful remote sync yet"))


@app.command()
def status() -> None:
    """Show current sync status.

    [bold cyan]EXAMPLES[/bold cyan]:
      [dim]$[/dim] claude-history status
    """
    meta = load_meta()
    backup_root = get_backup_root()
    archives = get_archives()

    # Get current state
    projects_oldest = get_oldest_session_date(CLAUDE_PROJECTS)
    projects_newest = get_newest_session_date(CLAUDE_PROJECTS)
    projects_count = count_sessions(CLAUDE_PROJECTS)
    projects_size = get_dir_size(CLAUDE_PROJECTS)

    # Archive stats
    archive_count = len(archives)
    archive_total_size = get_dir_size(backup_root) if backup_root.exists() else "0"

    # Build status table
    table = Table(title="Claude History Sync Status")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="white")

    table.add_row("Sessions in ~/.claude/projects", str(projects_count))
    table.add_row("Projects size", projects_size)
    table.add_row(
        "Oldest session",
        projects_oldest.strftime("%Y-%m-%d %H:%M") if projects_oldest else "N/A",
    )
    table.add_row(
        "Newest session",
        projects_newest.strftime("%Y-%m-%d %H:%M") if projects_newest else "N/A",
    )
    table.add_row("", "")
    table.add_row("Archives saved", str(archive_count))
    table.add_row("Total archive size", archive_total_size)
    table.add_row("Backup location", str(backup_root))

    console.print(table)

    # Last sync info
    if meta.get("last_sync"):
        last_sync = meta["last_sync"]
        console.print(f"\n{info(f'Last sync: {last_sync}')}")
        if meta.get("last_sync_oldest"):
            last_oldest_str = meta["last_sync_oldest"]
            console.print(f"{info(f'Oldest at last sync: {last_oldest_str}')}")
    else:
        console.print(f"\n{warning('No sync metadata found')}")

    # Check if sync needed
    if projects_oldest and meta.get("last_sync_oldest"):
        last_oldest = datetime.fromisoformat(meta["last_sync_oldest"])
        gap = (projects_oldest - last_oldest).days

        if gap == 0:
            console.print(f"\n{success('Backup is current (no cleanup since last sync)')}")
        elif gap <= SYNC_THRESHOLD_DAYS:
            console.print(
                f"\n{warning(f'Sync recommended! {gap} days of sessions removed since last sync (threshold: {SYNC_THRESHOLD_DAYS})')}"
            )
        else:
            console.print(
                f"\n{success(f'No sync needed. {gap} days since oldest backed up session.')}"
            )

    console.print()
    print_transcript_expiry()


@app.command()
def sync(
    force: bool = typer.Option(False, "--force", "-f", help="Force sync even if not needed"),
) -> None:
    """Create a new backup archive of all sessions.

    Creates a timestamped zip of ~/.claude/projects/

    [bold cyan]EXAMPLES[/bold cyan]:
      [dim]$[/dim] claude-history sync
      [dim]$[/dim] claude-history sync --force
    """
    if not CLAUDE_PROJECTS.exists():
        console.print(error("~/.claude/projects not found"))
        raise typer.Exit(code=1)

    backup_root = get_backup_root()
    backup_root.mkdir(parents=True, exist_ok=True)

    # Create archive name with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_name = f"backup_{timestamp}"
    archive_path = backup_root / archive_name

    console.print(info("Creating backup archive..."))

    # Create zip archive directly from projects
    shutil.make_archive(str(archive_path), "zip", CLAUDE_PROJECTS)

    final_path = Path(f"{archive_path}.zip")
    size = get_dir_size(final_path)
    console.print(success(f"Created: {final_path.name} ({size})"))

    # Update metadata
    projects_oldest = get_oldest_session_date(CLAUDE_PROJECTS)
    meta = load_meta()
    meta["last_sync"] = datetime.now().isoformat()
    meta["last_sync_oldest"] = projects_oldest.isoformat() if projects_oldest else None
    save_meta(meta)


@app.command()
def check(
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress output unless sync needed"),
) -> None:
    """Check if sync is needed (used by scheduler).

    Triggers sync if oldest session in projects is within 3 days
    of the oldest session at last sync time.

    [bold cyan]EXAMPLES[/bold cyan]:
      [dim]$[/dim] claude-history check
      [dim]$[/dim] claude-history check --quiet
    """
    meta = load_meta()
    projects_oldest = get_oldest_session_date(CLAUDE_PROJECTS)

    if not projects_oldest:
        if not quiet:
            console.print(info("No sessions in projects directory"))
        return

    # First run - always sync
    if not meta.get("last_sync_oldest"):
        if not quiet:
            console.print(info("First run - syncing..."))
        sync()
        return

    last_oldest = datetime.fromisoformat(meta["last_sync_oldest"])
    gap = (projects_oldest - last_oldest).days

    # gap > 0 means cleanup happened, gap <= threshold means sync needed
    if gap > 0 and gap <= SYNC_THRESHOLD_DAYS:
        if not quiet:
            console.print(warning(f"Gap is {gap} days - triggering sync"))
        sync()
    else:
        if not quiet:
            console.print(success(f"No sync needed. Gap is {gap} days."))


@app.command("remote-sync")
def remote_sync(
    destination: str | None = typer.Option(
        None,
        "--destination",
        "-d",
        help="Copy to this destination instead of the configured remote destination",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="List what would be copied without copying"
    ),
) -> None:
    """Copy all Claude Code and Codex transcripts to Google Cloud Storage.

    Runs [bold]gcloud storage rsync --recursive[/bold] per source, so each run copies
    only new or changed files. Files removed locally are never deleted remotely.
    Exits 1 when gcloud is missing, not logged in, or no destination is set.

    [bold cyan]EXAMPLES[/bold cyan]:
      [dim]$[/dim] claude-history remote-sync
      [dim]$[/dim] claude-history remote-sync --dry-run
    """
    console.print(info(f"remote-sync started at {datetime.now().isoformat(timespec='seconds')}"))

    destination = destination or load_config().get("remote_destination")
    if not destination:
        console.print(
            error(
                "No remote destination configured. "
                "Run: claude-history config --remote-destination gs://BUCKET/PREFIX"
            )
        )
        raise typer.Exit(code=1)
    destination = destination.rstrip("/")

    gcloud_path = shutil.which("gcloud")
    if not gcloud_path:
        console.print(error("gcloud not found on PATH. Install the Google Cloud CLI."))
        raise typer.Exit(code=1)

    active_account = subprocess.run(
        [gcloud_path, "auth", "list", "--filter=status:ACTIVE", "--format=value(account)"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not active_account:
        console.print(error("gcloud is not logged in. Run: gcloud auth login"))
        raise typer.Exit(code=1)

    for source in get_transcript_sources():
        if not source.path.exists():
            console.print(warning(f"Skipping {source.name}: {source.path} not found"))
            continue
        source_destination = f"{destination}/{source.remote_folder}"
        console.print(info(f"{source.name}: {source.path} -> {source_destination}"))
        command = [gcloud_path, "storage", "rsync", "--recursive"]
        if dry_run:
            command.append("--dry-run")
        result = subprocess.run([*command, str(source.path), source_destination])
        if result.returncode != 0:
            console.print(
                error(f"gcloud storage rsync failed for {source.name} (exit {result.returncode})")
            )
            raise typer.Exit(code=1)

    finished_at = datetime.now().isoformat(timespec="seconds")
    if dry_run:
        console.print(success(f"remote-sync dry run finished at {finished_at}"))
        return
    meta = load_meta()
    meta["last_remote_sync_finished"] = finished_at
    save_meta(meta)
    console.print(success(f"remote-sync finished at {finished_at}"))


# launchd plist path
LAUNCHD_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.warren.claude-history-backup.plist"
LAUNCHD_LABEL = "com.warren.claude-history-backup"

LAUNCHD_SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


def build_launchd_plist(cli_path: str, log_path: str, remote: bool) -> bytes:
    """Build the launchd job: a daily local check, or a weekly remote-sync when remote is set.

    launchd starts jobs with only the system PATH, so the remote job's PATH also holds
    the folder where gcloud is installed now.
    """
    job = {
        "Label": LAUNCHD_LABEL,
        "StandardOutPath": log_path,
        "StandardErrorPath": log_path,
    }
    if remote:
        job["ProgramArguments"] = [cli_path, "remote-sync"]
        job["StartCalendarInterval"] = {"Weekday": 0, "Hour": 10, "Minute": 0}
        gcloud_path = shutil.which("gcloud")
        job["EnvironmentVariables"] = {
            "PATH": f"{Path(gcloud_path).parent}:{LAUNCHD_SYSTEM_PATH}"
            if gcloud_path
            else LAUNCHD_SYSTEM_PATH
        }
    else:
        job["ProgramArguments"] = [cli_path, "check", "--quiet"]
        job["StartCalendarInterval"] = {"Hour": 10, "Minute": 0}
    return plistlib.dumps(job)


@app.command("scheduler-install")
def scheduler_install(
    remote: bool = typer.Option(
        False, "--remote", help="Run remote-sync weekly instead of the daily local check"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plist without installing it"),
) -> None:
    """Install launchd scheduler for automatic backups.

    Uses macOS launchd (runs missed jobs on wake, unlike cron).
    Default: daily at 10 AM, runs [bold]check --quiet[/bold] (local zip archive).
    With --remote: Sundays at 10 AM, runs [bold]remote-sync[/bold] to Google Cloud Storage.

    [bold cyan]EXAMPLES[/bold cyan]:
      [dim]$[/dim] claude-history scheduler-install
      [dim]$[/dim] claude-history scheduler-install --remote --dry-run
    """
    # Find claude-history path
    cli_path = shutil.which("claude-history")
    if not cli_path:
        cli_path = str(Path.home() / ".local" / "bin" / "claude-history")

    log_path = str(Path.home() / ".claude" / "history-backup.log")
    plist_content = build_launchd_plist(cli_path, log_path, remote)

    if dry_run:
        console.print(info(f"Would write {LAUNCHD_PLIST}:"))
        print(plist_content.decode())
        return

    # Check if already installed
    result = subprocess.run(
        ["launchctl", "list", LAUNCHD_LABEL],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        console.print(warning("Scheduler already installed"))
        console.print(info(f"Plist: {LAUNCHD_PLIST}"))
        return

    # Write plist
    LAUNCHD_PLIST.parent.mkdir(parents=True, exist_ok=True)
    LAUNCHD_PLIST.write_bytes(plist_content)

    # Load it
    result = subprocess.run(
        ["launchctl", "load", str(LAUNCHD_PLIST)],
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        schedule = "weekly remote-sync, Sundays at 10 AM" if remote else "daily at 10 AM"
        console.print(success(f"Scheduler installed ({schedule})"))
        console.print(info("Uses launchd - runs missed jobs on wake"))
        console.print(info(f"Plist: {LAUNCHD_PLIST}"))
    else:
        console.print(error(f"Failed to install: {result.stderr}"))
        raise typer.Exit(code=1)


@app.command("scheduler-remove")
def scheduler_remove() -> None:
    """Remove the launchd scheduler.

    [bold cyan]EXAMPLES[/bold cyan]:
      [dim]$[/dim] claude-history scheduler-remove
    """
    # Unload if running
    subprocess.run(
        ["launchctl", "unload", str(LAUNCHD_PLIST)],
        capture_output=True,
    )

    # Remove plist file
    if LAUNCHD_PLIST.exists():
        LAUNCHD_PLIST.unlink()
        console.print(success("Scheduler removed"))
    else:
        console.print(info("Scheduler not installed"))


@app.command("scheduler-status")
def scheduler_status() -> None:
    """Check scheduler status.

    [bold cyan]EXAMPLES[/bold cyan]:
      [dim]$[/dim] claude-history scheduler-status
    """
    result = subprocess.run(
        ["launchctl", "list", LAUNCHD_LABEL],
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        console.print(success("Scheduler is active"))
        console.print(info(f"Plist: {LAUNCHD_PLIST}"))
        # Show last run time from log
        log_file = Path.home() / ".claude" / "history-backup.log"
        if log_file.exists():
            mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
            console.print(info(f"Log last modified: {mtime.strftime('%Y-%m-%d %H:%M')}"))
    else:
        console.print(warning("Scheduler is not active"))
        if LAUNCHD_PLIST.exists():
            console.print(info("Plist exists but not loaded. Run: scheduler-install"))


@app.command()
def logs(
    lines: int = typer.Option(20, "--lines", "-n", help="Number of lines to show"),
) -> None:
    """Show backup log file.

    [bold cyan]EXAMPLES[/bold cyan]:
      [dim]$[/dim] claude-history logs
      [dim]$[/dim] claude-history logs -n 50
    """
    log_file = Path.home() / ".claude" / "history-backup.log"

    if not log_file.exists():
        console.print(info("No log file yet"))
        return

    result = subprocess.run(
        ["tail", f"-{lines}", str(log_file)],
        capture_output=True,
        text=True,
    )

    if result.stdout:
        console.print(Panel(result.stdout, title="Backup Log", border_style="dim"))
    else:
        console.print(info("Log file is empty"))


@app.command("list")
def list_archives() -> None:
    """List all backup archives.

    [bold cyan]EXAMPLES[/bold cyan]:
      [dim]$[/dim] claude-history list
    """
    archives = get_archives()

    if not archives:
        console.print(info("No archives found"))
        return

    table = Table(title="Backup Archives")
    table.add_column("Archive", style="cyan")
    table.add_column("Size", style="white")
    table.add_column("Created", style="dim")

    for arch in archives:
        size = get_dir_size(arch)
        created = datetime.fromtimestamp(arch.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        table.add_row(arch.name, size, created)

    console.print(table)

    backup_root = get_backup_root()
    total_size = get_dir_size(backup_root)
    console.print(f"\n{info(f'Total: {len(archives)} archives, {total_size}')}")


@app.command()
def config(
    backup_root: Optional[str] = typer.Option(
        None, "--backup-root", "-b", help="Set backup root directory"
    ),
    remote_destination: str | None = typer.Option(
        None,
        "--remote-destination",
        "-r",
        help="Set the gs://BUCKET/PREFIX that remote-sync copies transcripts to",
    ),
) -> None:
    """View or set configuration.

    [bold cyan]EXAMPLES[/bold cyan]:
      [dim]$[/dim] claude-history config
      [dim]$[/dim] claude-history config --backup-root ~/my-backups
      [dim]$[/dim] claude-history config --remote-destination gs://my-bucket/transcripts
    """
    cfg = load_config()

    if remote_destination and not remote_destination.startswith("gs://"):
        console.print(error("Remote destination must start with gs://"))
        raise typer.Exit(code=1)

    if remote_destination or backup_root:
        if remote_destination:
            cfg["remote_destination"] = remote_destination.rstrip("/")
            console.print(success(f"Remote destination set to: {cfg['remote_destination']}"))
        if backup_root:
            # Expand ~ and resolve path
            new_root = Path(backup_root).expanduser().resolve()
            cfg["backup_root"] = str(new_root)
            console.print(success(f"Backup root set to: {new_root}"))
        save_config(cfg)
        return

    # Show current config
    current_root = get_backup_root()
    console.print(
        Panel(
            f"[cyan]Backup location:[/cyan] {current_root}\n"
            f"[cyan]Remote destination:[/cyan] {cfg.get('remote_destination', 'not set')}\n"
            f"[cyan]Config file:[/cyan] {CONFIG_FILE}",
            title="Configuration",
            border_style="cyan",
        )
    )


def main() -> None:
    """Entry point for CLI."""
    app()


if __name__ == "__main__":
    main()
