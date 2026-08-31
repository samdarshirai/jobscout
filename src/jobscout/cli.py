import typer

from jobscout import __version__

app = typer.Typer(
    name="jobscout",
    help="Jobscout — a personal job-hunt agent for one real candidate's search.",
    no_args_is_help=True,
)


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
    """Jobscout command-line interface. Subcommands are added by later units."""


def run() -> None:
    app()


if __name__ == "__main__":
    run()
