"""Shared fixtures for testing agent-history-backup."""

from datetime import datetime, timedelta

import pytest


@pytest.fixture
def temp_home(tmp_path):
    """Create a temporary home directory structure."""
    home = tmp_path / "home"
    home.mkdir()

    # Create .claude/projects structure
    claude_projects = home / ".claude" / "projects"
    claude_projects.mkdir(parents=True)

    # Create config directory
    config_dir = home / ".config" / "agent-history-backup"
    config_dir.mkdir(parents=True)

    # Create default backup root
    backup_root = home / "claude_code_history"
    backup_root.mkdir()

    return home


@pytest.fixture
def mock_paths(temp_home, monkeypatch):
    """Mock all path constants to use temp directories."""
    import agent_history_backup.cli as cli

    claude_projects = temp_home / ".claude" / "projects"
    claude_settings = temp_home / ".claude" / "settings.json"
    codex_sessions = temp_home / ".codex" / "sessions"
    codex_archived_sessions = temp_home / ".codex" / "archived_sessions"
    config_dir = temp_home / ".config" / "agent-history-backup"
    config_file = config_dir / "config.json"
    meta_file = config_dir / "sync_meta.json"
    log_file = temp_home / "Library" / "Logs" / "agent-history-backup.log"
    backup_root = temp_home / "claude_code_history"

    monkeypatch.setattr(cli, "CLAUDE_PROJECTS", claude_projects)
    monkeypatch.setattr(cli, "CLAUDE_SETTINGS", claude_settings)
    monkeypatch.setattr(cli, "CODEX_SESSIONS", codex_sessions)
    monkeypatch.setattr(cli, "CODEX_ARCHIVED_SESSIONS", codex_archived_sessions)
    monkeypatch.setattr(cli, "CONFIG_FILE", config_file)
    monkeypatch.setattr(cli, "META_FILE", meta_file)
    monkeypatch.setattr(cli, "LOG_FILE", log_file)
    monkeypatch.setattr(cli, "DEFAULT_BACKUP_ROOT", backup_root)

    return {
        "home": temp_home,
        "claude_projects": claude_projects,
        "claude_settings": claude_settings,
        "codex_sessions": codex_sessions,
        "codex_archived_sessions": codex_archived_sessions,
        "config_file": config_file,
        "meta_file": meta_file,
        "log_file": log_file,
        "backup_root": backup_root,
    }


FAKE_GCLOUD_SCRIPT = """#!{python}
import json, os, sys
with open(os.environ["FAKE_GCLOUD_CALLS"], "a") as calls:
    calls.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1:3] == ["auth", "list"]:
    print(os.environ.get("FAKE_GCLOUD_ACCOUNT", ""))
    sys.exit(0)
sys.exit(int(os.environ.get("FAKE_GCLOUD_RSYNC_EXIT", "0")))
"""


@pytest.fixture
def fake_gcloud(tmp_path, monkeypatch):
    """Put a fake `gcloud` executable first on PATH that records each call's arguments.

    `auth list` prints FAKE_GCLOUD_ACCOUNT (logged in by default); every other call
    exits with FAKE_GCLOUD_RSYNC_EXIT. `calls()` returns the recorded argument lists.
    """
    import json
    import sys

    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    gcloud = bin_dir / "gcloud"
    gcloud.write_text(FAKE_GCLOUD_SCRIPT.format(python=sys.executable))
    gcloud.chmod(0o755)
    calls_file = tmp_path / "gcloud-calls.jsonl"

    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setenv("FAKE_GCLOUD_CALLS", str(calls_file))
    monkeypatch.setenv("FAKE_GCLOUD_ACCOUNT", "someone@example.com")

    class FakeGcloud:
        path = gcloud

        @staticmethod
        def calls() -> list[list[str]]:
            if not calls_file.exists():
                return []
            return [json.loads(line) for line in calls_file.read_text().splitlines()]

    return FakeGcloud()


@pytest.fixture
def create_sessions(mock_paths):
    """Factory fixture to create mock session directories."""
    def _create_sessions(count: int, days_back: list[int] = None):
        """
        Create session directories with specific modification times.

        Args:
            count: Number of sessions to create
            days_back: List of days back from now for each session's mtime.
                       If None, uses [0, 1, 2, ...] for each session.
        """
        if days_back is None:
            days_back = list(range(count))

        sessions = []
        projects_dir = mock_paths["claude_projects"]

        for i, days in enumerate(days_back[:count]):
            session = projects_dir / f"session_{i:03d}"
            session.mkdir(exist_ok=True)

            # Create a file in the session so it's not empty
            (session / "conversation.json").write_text("{}")

            # Set modification time
            import os
            mtime = datetime.now() - timedelta(days=days)
            timestamp = mtime.timestamp()
            os.utime(session, (timestamp, timestamp))

            sessions.append(session)

        return sessions

    return _create_sessions


@pytest.fixture
def create_archives(mock_paths):
    """Factory fixture to create mock archive files."""
    def _create_archives(count: int, days_back: list[int] = None):
        """Create archive zip files with specific modification times."""
        if days_back is None:
            days_back = list(range(count))

        archives = []
        backup_root = mock_paths["backup_root"]

        for _, days in enumerate(days_back[:count]):
            mtime = datetime.now() - timedelta(days=days)
            timestamp = mtime.strftime("%Y%m%d_%H%M%S")
            archive = backup_root / f"backup_{timestamp}.zip"
            archive.write_bytes(b"PK\x03\x04" + b"\x00" * 100)  # Minimal zip header

            # Set modification time
            import os
            os.utime(archive, (mtime.timestamp(), mtime.timestamp()))

            archives.append(archive)

        return archives

    return _create_archives


@pytest.fixture
def cli_runner():
    """Typer CLI test runner."""
    from typer.testing import CliRunner
    return CliRunner()
