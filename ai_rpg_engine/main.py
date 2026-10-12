#!/usr/bin/env python3
"""AI RPG Engine - entry point, OS gate and bootstrap.

Order matters:

1. The macOS gate runs first and unconditionally (spec section 0.6).
2. Settings are loaded and storage directories are prepared.
3. In ``--check`` mode a short status report is printed and we exit.
4. Otherwise the Textual app is imported lazily and launched.

The engine is deterministic Python. If no LLM key is configured we run in
offline mode: every mechanic still works, only the prose is templated.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

BANNER_TITLE = "A I   R P G   E N G I N E"


def _darwin_gate() -> None:
    """Refuse to run on macOS with a bold, colored message (spec section 0.6)."""
    if sys.platform != "darwin":
        return
    try:
        from rich.console import Console

        Console().print("open your mind", style="bold red")
    except Exception:  # pragma: no cover - rich should always be present
        sys.stderr.write("open your mind\n")
    sys.exit(1)


def _print_banner(offline: bool) -> None:
    """Print a minimal Gruvbox-colored startup banner."""
    try:
        from rich.console import Console
        from rich.text import Text

        console = Console()
        title = Text(BANNER_TITLE, style="bold #fabd2f")
        mode = Text("offline (dry mode)" if offline else "narrative online", style="#8ec07c" if not offline else "#fb4934")
        console.print(title)
        console.print(Text("llm layer: ", style="#a89984") + mode)
    except Exception:  # pragma: no cover
        print(BANNER_TITLE)


def _status_report(settings: object) -> int:
    """Print a bootstrap status summary (used by ``--check``)."""
    from rich.console import Console
    from rich.table import Table

    from engine import __version__

    # Local import avoided at module scope to keep the OS gate lightweight.
    from engine.config import Settings

    assert isinstance(settings, Settings)
    table = Table(title="AI RPG Engine - bootstrap check", border_style="#504945")
    table.add_column("key", style="#83a598")
    table.add_column("value", style="#ebdbb2")
    table.add_row("version", __version__)
    table.add_row("python", sys.version.split()[0])
    table.add_row("platform", sys.platform)
    table.add_row("project root", str(settings.resolve(settings.save_dir).parent))
    table.add_row("data dir", str(settings.data_path))
    table.add_row("saves dir", str(settings.saves_path))
    table.add_row("logs dir", str(settings.logs_path))
    table.add_row("api key present", "yes" if settings.has_llm else "no")
    table.add_row("endpoint", settings.effective_base_url)
    table.add_row("offline mode", settings.offline_mode)
    table.add_row("effective offline", "yes" if settings.effective_offline else "no")
    table.add_row("default model", settings.effective_model)
    table.add_row("fudge dice", "on" if settings.fudge_dice else "off")
    table.add_row("canon chain", "enabled" if settings.canon_chain_enabled else "disabled")
    Console().print(table)
    return 0


def _launch_tui(settings: object, *, slot: str | None = None) -> int:
    """Import and run the Textual app lazily."""
    try:
        from ui.tui_app import RpgApp
    except ModuleNotFoundError as exc:
        # During incremental development the UI package may not be built yet.
        if (exc.name or "").startswith("ui"):
            print(
                "[bootstrap] TUI is not built yet (spec step 10). "
                "Engine bootstrap, config and tests work already.",
                file=sys.stderr,
            )
            return 0
        raise
    from engine.config import Settings

    assert isinstance(settings, Settings)
    app = RpgApp(settings=settings, slot=slot)
    app.run()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai_rpg_engine",
        description="Deterministic tabletop RPG sandbox with an AI narrator.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="run bootstrap and print a status report, then exit",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="force offline (dry) mode for this run",
    )
    parser.add_argument(
        "--slot",
        default=None,
        help="load a saved game by slot name (otherwise the main menu opens)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _darwin_gate()

    args = build_parser().parse_args(argv)

    from engine.config import get_settings

    settings = get_settings()
    if args.offline:
        settings.offline_mode = "force"
    settings.ensure_dirs()

    if args.check:
        return _status_report(settings)

    _print_banner(settings.effective_offline)
    try:
        return _launch_tui(settings, slot=getattr(args, "slot", None))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
