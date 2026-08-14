"""rich-based Live status displays for run_cli_select.py.

Two multi-step flows narrate their progress purely through a sequence of
``Event(message=...)`` calls: the config-presence check
(``response_job_agent``, github issue #20) and the extract -> verify ->
write pipeline (``response_job_url_fetch_node`` /
``response_update_spreadsheet_node``, github issue #21). Both get the same
two-line ``rich.live.Live`` treatment: a permanent header line (optionally
next to a step progress bar) and an ephemeral sub-step line beneath it that
is swapped out as each new message arrives. A stage can also attach a
vertical detail list (`set_substep_detail`) that, unlike the sub-step line,
sticks around across later sub-step updates until the stage concludes --
at which point everything collapses for good, leaving only the header --
by then holding the outcome -- in the scrollback.

Kept as its own module (rather than folded into run_cli_select.py) so the
rich-specific rendering logic is easy to find and reuse from both stages.
"""

from __future__ import annotations

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.progress_bar import ProgressBar
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text


class StageDisplay:
    """A permanent header line, optionally with a step progress bar, plus an
    ephemeral spinner+text sub-step line beneath it.

    ``steps`` is the ordered list of macro-step labels shown next to the
    progress bar while the stage is running (pass a single-item list for a
    stage with no meaningful sub-steps, e.g. the config-presence check).
    ``set_step``/``set_substep`` update the live display in place;
    ``finish`` swaps the header for the final outcome and drops the
    sub-step line for good.
    """

    def __init__(self, console: Console, steps: list[str]) -> None:
        self._console = console
        self._steps = steps
        self._step_index = 0
        self._spinner = Spinner("dots", style="cyan")
        self._detail_lines: list[str] = []
        self._done = False
        self._outcome: Text | None = None
        self._live = Live(console=console, refresh_per_second=10, transient=False)

    def _render(self) -> RenderableType:
        if self._outcome is not None:
            return self._outcome

        grid = Table.grid(padding=(0, 1))
        grid.add_column()
        grid.add_column()
        if len(self._steps) > 1:
            bar = ProgressBar(total=len(self._steps), completed=self._step_index, width=20)
            grid.add_row(bar, Text(self._steps[self._step_index], style="bold cyan"))
        else:
            grid.add_row(Text("⏳", style="cyan"), Text(self._steps[0], style="bold cyan"))

        renderables: list[RenderableType] = [grid, self._spinner]
        if self._detail_lines:
            detail = Text(
                "    (preview only -- remaining fields captured but not shown, for brevity)\n",
                style="dim italic",
            )
            detail.append("\n".join(f"    • {line}" for line in self._detail_lines), style="dim")
            renderables.append(detail)
        return Group(*renderables)

    def start(self) -> None:
        self._live.start()
        self._live.update(self._render())

    def set_step(self, step_index: int) -> None:
        self._step_index = step_index
        self._live.update(self._render())

    def set_substep(self, text: str) -> None:
        self._spinner.update(text=text)
        self._live.update(self._render())

    def set_substep_detail(self, header: str, lines: list[str]) -> None:
        """Like `set_substep`, but with an extra vertical list of detail
        lines (e.g. a preview of extracted field values) rendered beneath
        the header. Unlike the ephemeral sub-step text -- which each
        `set_substep`/`set_substep_detail` call overwrites -- this detail
        list persists through any later `set_substep` calls, staying
        visible for the rest of the stage until `finish`/`abort` drops it.
        Call again (with new lines, or `lines=[]` to clear) to replace it
        earlier."""
        self._spinner.update(text=header)
        self._detail_lines = lines
        self._live.update(self._render())

    def finish(self, title: str, *, icon: str, style: str) -> None:
        self._done = True
        self._outcome = Text(f"{icon} {title}", style=style)
        self._live.update(self._render())
        self._live.stop()

    def abort(self) -> None:
        """Best-effort cleanup for when the stage never reaches `finish` (an
        exception propagated out of the graph instead). Leaves whatever was
        last shown in the scrollback rather than an animated spinner."""
        if not self._done:
            self._live.stop()
            self._done = True
