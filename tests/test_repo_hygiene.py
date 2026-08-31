import subprocess
from pathlib import Path


def _is_ignored(path: str) -> bool:
    repo_root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        ["git", "check-ignore", "-q", path],
        cwd=repo_root,
    )
    assert result.returncode in (0, 1), f"git check-ignore failed for {path} (rc={result.returncode})"
    return result.returncode == 0


def test_secrets_and_local_data_are_ignored():
    for path in [".env", ".env.local", "data/jobscout.sqlite", "data/resume.pdf", "data/criteria.yaml", "data/voice.md"]:
        assert _is_ignored(path), f"{path} must be git-ignored (DESIGN §2, §19)"


def test_example_data_stays_tracked():
    assert not _is_ignored("data/example/fake_resume.pdf"), "data/example/ must not be ignored (DESIGN §19)"


def test_env_example_lists_every_secret():
    text = Path(".env.example").read_text()
    for key in [
        "OPENROUTER_API_KEY",
        "LANGCHAIN_API_KEY",
        "ADZUNA_APP_ID",
        "ADZUNA_APP_KEY",
        "TELEGRAM_BOT_TOKEN",
        "JOBSCOUT_WEB_TOKEN",
    ]:
        assert key in text, f"{key} missing from .env.example"
