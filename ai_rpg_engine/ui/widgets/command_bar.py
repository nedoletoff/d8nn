"""Command bar: text input with slash-command autocomplete and history."""

from __future__ import annotations

from typing import Iterable

from textual.containers import Vertical
from textual.suggester import Suggester
from textual.widgets import Input

#: Slash commands offered by autocomplete (mirrors the spec's command table).
DEFAULT_COMMANDS: tuple[str, ...] = (
    "/rule",
    "/roll",
    "/save",
    "/load",
    "/slots",
    "/map",
    "/inv",
    "/stats",
    "/quests",
    "/time",
    "/party",
    "/travel",
    "/talk",
    "/say",
    "/use",
    "/equip",
    "/unequip",
    "/snapshot",
    "/retcon",
    "/model",
    "/fudge",
    "/export",
    "/help",
    "/exit",
)


class SlashSuggester(Suggester):
    """Suggest slash commands while the user types a leading ``/``."""

    def __init__(self, commands: Iterable[str] = DEFAULT_COMMANDS) -> None:
        super().__init__(case_sensitive=True)
        self.commands = list(commands)

    async def get_suggestion(self, value: str) -> str | None:
        if not value.startswith("/"):
            return None
        for command in self.commands:
            if command.startswith(value) and command != value:
                return command
        return None


class CommandInput(Input):
    """Input with a simple up/down history."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.history: list[str] = []
        self._index: int = 0

    def remember(self, value: str) -> None:
        value = value.strip()
        if value and (not self.history or self.history[-1] != value):
            self.history.append(value)
        self._index = len(self.history)

    def _recall(self, delta: int) -> None:
        if not self.history:
            return
        self._index = max(0, min(len(self.history), self._index + delta))
        if self._index == len(self.history):
            self.value = ""
        else:
            self.value = self.history[self._index]

    async def key_up(self) -> None:
        self._recall(-1)

    async def key_down(self) -> None:
        self._recall(1)


class CommandBar(Vertical):
    """Container holding the command input."""

    def compose(self):
        yield CommandInput(
            placeholder="> что ты делаешь?  (/help для команд, /rule для справки)",
            suggester=SlashSuggester(),
            id="command-input",
        )

    @property
    def input(self) -> CommandInput:
        return self.query_one(CommandInput)
