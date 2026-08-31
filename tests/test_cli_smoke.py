import subprocess

from typer.testing import CliRunner

from jobscout.cli import app

runner = CliRunner()


def test_help_exits_zero_and_names_the_tool():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Jobscout" in result.output


def test_version_flag_prints_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_console_script_help_runs():
    result = subprocess.run(
        ["uv", "run", "jobscout", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "Jobscout" in result.stdout
