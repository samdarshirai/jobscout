import logging
from pathlib import Path

import typer
import yaml
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from jobscout import __version__, spend
from jobscout.service import get_service

# Per-Posting progress (discover/dedupe/knockout/score counts, DESIGN §17)
# is INFO-level and otherwise invisible — stdlib logging defaults to
# WARNING+, and a Poll's knockout/score loops can run for minutes with no
# other sign of life. Confirmed live: without this, a slow real Poll looks
# indistinguishable from a hang.
logging.basicConfig(
    level=logging.INFO, format="%(message)s", handlers=[RichHandler(show_path=False)]
)

app = typer.Typer(
    name="jobscout",
    help="Jobscout — a personal job-hunt agent for one real candidate's search.",
    no_args_is_help=True,
)
resume_app = typer.Typer(help="Resume a paused Run from its marked-up gate file.")
app.add_typer(resume_app, name="resume")

console = Console()
GATES_DIR = Path("data/gates")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"jobscout {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        help="Show the version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """Jobscout command-line interface."""


def _fail(message: str) -> None:
    console.print(f"[red]Error:[/red] {message}")
    raise typer.Exit(code=1)


def _write_gate_file(pending_gate: dict, path: Path) -> None:
    """The "marked-up file" gates are answered through (DESIGN §16, week
    1-2). Fail-closed by construction: an unedited search_plan file
    aborts the Run (`decision: reject`), matching poll.py's own
    `_after_gate` — nothing here defaults to `approve`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    gate = pending_gate["gate"]
    if gate == "search_plan":
        text = yaml.safe_dump(
            {"gate": gate, "plan": pending_gate["plan"]}, sort_keys=False, allow_unicode=True
        )
        text += (
            '# change to "approve" to proceed; anything else (including leaving this) aborts the Run\n'
            "decision: reject\n"
        )
    else:  # static_questions | dynamic_questions
        text = f"gate: {gate}\nanswers:\n"
        for q in pending_gate["questions"]:
            if isinstance(q, dict):
                key, comment = q["key"], f"  # {q['prompt']}"
            else:
                key, comment = q, ""
            line = yaml.safe_dump({key: ""}, default_flow_style=False, allow_unicode=True).strip()
            text += f"  {line}{comment}\n"
    path.write_text(text)


def _read_gate_file(path: Path) -> tuple[str, object]:
    if not path.exists():
        _fail(f"no gate file at {path} — run the command that creates it first")
    doc = yaml.safe_load(path.read_text())
    gate = doc["gate"]
    return gate, (doc["decision"] if gate == "search_plan" else doc["answers"])


def _report_run(handle, gate_path: Path) -> None:
    if handle.status == "paused":
        _write_gate_file(handle.pending_gate, gate_path)
        console.print(
            f"Run paused at gate [bold]{handle.pending_gate['gate']}[/bold]. "
            f"Edit {gate_path} then resume."
        )
    else:
        console.print(f"[green]Run completed.[/green] Queue has {len(get_service().get_queue())} Posting(s).")


@app.command()
def poll() -> None:
    """Trigger a Poll run."""
    svc = get_service()
    try:
        handle = svc.trigger_run()
    except (ValueError, spend.SpendCapExceeded) as e:
        _fail(str(e))
    _report_run(handle, GATES_DIR / f"poll_{handle.run_id}.yaml")


@resume_app.command("poll")
def resume_poll(run_id: str) -> None:
    """Resume a paused Poll run from its gate file."""
    svc = get_service()
    gate_path = GATES_DIR / f"poll_{run_id}.yaml"
    _, decision = _read_gate_file(gate_path)
    try:
        handle = svc.resume_run(run_id, decision)
    except (ValueError, spend.SpendCapExceeded) as e:
        _fail(str(e))
    _report_run(handle, gate_path)


@app.command()
def onboard(
    resume_path: str | None = typer.Argument(None),
    reset: bool = typer.Option(False, "--reset", help="Wipe profile + criteria and start over."),
) -> None:
    """Run onboarding from a resume PDF, or --reset to wipe it and start over."""
    svc = get_service()
    if reset:
        svc.reset_onboarding()
        console.print("[green]Onboarding reset.[/green]")
        return
    try:
        handle = svc.run_onboarding(resume_path)
    except (ValueError, spend.SpendCapExceeded) as e:
        _fail(str(e))
    _onboard_report(handle)


def _onboard_report(handle) -> None:
    gate_path = GATES_DIR / "onboard.yaml"
    if handle.status == "paused":
        _write_gate_file(handle.pending_gate, gate_path)
        console.print(
            f"Onboarding paused at gate [bold]{handle.pending_gate['gate']}[/bold]. "
            f"Edit {gate_path} then run: jobscout resume onboard"
        )
    else:
        console.print("[green]Onboarding complete.[/green]")


@resume_app.command("onboard")
def resume_onboard() -> None:
    """Resume onboarding from its gate file."""
    svc = get_service()
    gate_path = GATES_DIR / "onboard.yaml"
    _, answers = _read_gate_file(gate_path)
    try:
        handle = svc.resume_onboarding(answers)
    except (ValueError, spend.SpendCapExceeded) as e:
        _fail(str(e))
    _onboard_report(handle)


@app.command()
def queue() -> None:
    """Show the Queue, sorted by Score."""
    entries = get_service().get_queue()
    table = Table("Score", "Company", "Title", "Posting", "Flag")
    for e in entries:
        flags = ", ".join(f for f, on in [("weak fit", e.weak_fit), ("changed", e.changed)] if on)
        table.add_row(str(e.score), e.company, e.title, e.posting_id, flags)
    console.print(table)


@app.command("spend")
def show_spend() -> None:
    """Show total Spend against the $20 cap."""
    console.print(f"${get_service().total_spend():.2f} / ${spend.CAP_USD:.2f}")


@app.command()
def thumb(
    posting_id: str,
    verdict: str,
    reason: str | None = typer.Option(None, "--reason", help="One-line why."),
) -> None:
    """Record a thumbs up/down Verdict on a Posting."""
    try:
        get_service().record_verdict(posting_id, verdict, reason)
    except ValueError as e:
        _fail(str(e))
    console.print(f"[green]Recorded.[/green] {posting_id}: {verdict}")


@app.command()
def learn() -> None:
    """Force-regenerate the Preference Summary now (§7)."""
    try:
        summary = get_service().learn()
    except spend.SpendCapExceeded as e:
        _fail(str(e))
    console.print(f"[green]Preference Summary regenerated:[/green]\n{summary}")


@app.command()
def rescore() -> None:
    """Force re-score every current Posting in the Queue (§7) — no
    retroactive re-scoring happens unless you ask for it here."""
    try:
        count = get_service().rescore()
    except spend.SpendCapExceeded as e:
        _fail(str(e))
    console.print(f"[green]Rescored {count} Posting(s).[/green]")


def run() -> None:
    app()


if __name__ == "__main__":
    run()
