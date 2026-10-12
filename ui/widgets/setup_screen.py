"""Setup wizard: a modal screen with separate tabs.

Tabs: World · Character · Tone · Rules · Edits · Model · Past games. Returns a
config dict via ``dismiss``; the app turns it into a fresh game.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static, TabbedContent, TabPane, TextArea

from engine import world_forge as forge
from engine.config import Settings
from engine.llm_client import LLMClient
from engine.worlds import WorldMeta

CLASS_LABELS: dict[str, str] = {
    "warrior": "Воин",
    "rogue": "Плут",
    "mage": "Маг",
    "ranger": "Следопыт",
    "wanderer": "Скиталец",
}

STAT_KEYS: tuple[str, ...] = ("STR", "DEX", "INT", "WIS", "CHA", "LUK")

#: sentinel value for the "build your own world" entry in the world Select
CUSTOM_WORLD = "__custom__"

CLASS_STATS: dict[str, dict[str, int]] = {
    "warrior": {"STR": 3, "DEX": 1, "INT": 1, "WIS": 1, "CHA": 0, "LUK": 1},
    "rogue": {"STR": 1, "DEX": 3, "INT": 2, "WIS": 1, "CHA": 1, "LUK": 2},
    "mage": {"STR": 0, "DEX": 1, "INT": 4, "WIS": 2, "CHA": 1, "LUK": 1},
    "ranger": {"STR": 2, "DEX": 3, "INT": 1, "WIS": 2, "CHA": 0, "LUK": 2},
    "wanderer": {"STR": 3, "DEX": 1, "INT": 2, "WIS": 0, "CHA": 1, "LUK": 2},
}

CHARACTER_PROMPT = """\
Ты помогаешь собрать героя для текстовой RPG. По описанию мира придумай героя и
верни строго JSON:
{
  "name": "имя",
  "role": "роль на русском (2-4 слова, можно свою)",
  "stats": {"STR":0,"DEX":0,"INT":0,"WIS":0,"CHA":0,"LUK":0},
  "background": "предыстория, 2-4 предложения",
  "motivation": "цель и мотив одной фразой",
  "items": ["id предметов из мира, 1-3"]
}
Имя, стиль и «статы» — в духе мира. Без эмодзи и пояснений."""


class SetupScreen(ModalScreen[dict[str, Any]]):
    """Collect world / character / tone / rules / model before the game starts."""

    def __init__(
        self,
        worlds: Iterable[WorldMeta],
        classes: Iterable[str],
        *,
        default_world: str | None = None,
        settings: Settings | None = None,
        games: Iterable[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__()
        self.worlds = list(worlds)
        self._world_by_id = {world.id: world for world in self.worlds}
        self._world_options = [(w.name, w.id) for w in self.worlds] + [("Свой мир… (создать)", CUSTOM_WORLD)]
        self._class_options = [(CLASS_LABELS.get(name, name), name) for name in classes]
        self._lethality_options = [
            ("Мягкая", "soft"), ("Обычная", "standard"), ("Жёсткая", "hardcore"),
        ]
        self._default_world = default_world or (self.worlds[0].id if self.worlds else "_")
        self.settings = settings
        self.games = list(games or [])

    # -- layout --------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Vertical(id="setup-root"):
            yield Static("Создание мира", id="setup-title")
            with TabbedContent():
                with TabPane("Мир", id="tab-world"):
                    with VerticalScroll():
                        yield Static("Прототип мира:", classes="hint")
                        yield Select(self._world_options, id="world", value=self._default_world)
                        yield Static("", id="world-desc")
                        with Vertical(id="world-custom"):
                            yield Static("Опиши свой мир и собери его нейросетью:", classes="hint")
                            yield Input(placeholder="напр. мрачное фэнтези про город на костях кита", id="world_prompt")
                            yield Button("Создать мир с нейросетью", id="gen_world")
                            yield Static("", id="world_status")
                with TabPane("Персонаж", id="tab-char"):
                    with VerticalScroll():
                        yield Static("Имя персонажа:", classes="hint")
                        yield Input(placeholder="имя", id="name")
                        yield Static("Роль (класс):", classes="hint")
                        yield Select(self._class_options, id="char_class", value="wanderer")
                        yield Static("…или своя роль (переопределяет класс):", classes="hint")
                        yield Input(placeholder="напр. алхимик-изгнанник", id="custom_role")
                        yield Static("Характеристики (можно править):", classes="hint")
                        with Vertical(id="stats-list"):
                            for key in STAT_KEYS:
                                with Horizontal(classes="stat-row"):
                                    yield Static(f"{key:>4}", classes="stat-label")
                                    yield Input(placeholder="0", id=f"stat_{key}", classes="stat")
                        yield Static("Предыстория:", classes="hint")
                        yield TextArea(id="background")
                        yield Static("Мотивация:", classes="hint")
                        yield Input(placeholder="чего ты хочешь", id="motivation")
                        yield Static("Что с собой (обычными словами — нейросеть разберёт):", classes="hint")
                        yield TextArea(id="items")
                        yield Button("Собрать персонажа с нейросетью", id="gen_char")
                        yield Static("", id="char_status")
                with TabPane("Тон", id="tab-tone"):
                    with VerticalScroll():
                        yield Static("Опиши сеттинг и атмосферу. Нейросеть будет им следовать.", classes="hint")
                        yield TextArea(id="setting")
                with TabPane("Правила", id="tab-rules"):
                    with VerticalScroll():
                        yield Static("Летальность:", classes="hint")
                        yield Select(self._lethality_options, id="lethality", value="standard")
                        yield Static("Законы мира (через ';'):", classes="hint")
                        yield Input(placeholder="Мёртвые не возвращаются; Магия запрещена", id="laws")
                        yield Static("Числа правил одной строкой (нейросеть разберёт сама):", classes="hint")
                        yield Input(placeholder="напр. вес побольше, мир дружелюбнее, carry_base = 30", id="rule_numbers")
                with TabPane("Правки", id="tab-edits"):
                    with VerticalScroll():
                        yield Static(
                            "Дополнительные правки к игре своими словами. Они ВАЖНЕЕ движка.\n"
                            "Напр.: «магии не существует», «никто не умирает», «carry_base = 30»,\n"
                            "«эльфов нет», «тон уютный». Их разбирает нейросеть и переводит\n"
                            "в ограничения её собственных прав и в правила движка.",
                            classes="hint",
                        )
                        yield TextArea(id="edits")
                with TabPane("Модель", id="tab-model"):
                    with VerticalScroll():
                        yield Static("Любой OpenAI-совместимый эндпоинт.", classes="hint")
                        yield Input(placeholder="https://api.openai.com/v1", id="llm_url")
                        yield Input(placeholder="API-ключ", id="llm_key", password=True)
                        yield Input(placeholder="модель, напр. gpt-4o-mini", id="llm_model")
                        yield Button("Проверить связь", id="check_llm")
                        yield Static("", id="llm_status")
                with TabPane("Прошлые игры", id="tab-games"):
                    with VerticalScroll():
                        yield Static("Продолжить сохранённую игру (автосохранение идёт каждый ход):", classes="hint")
                        yield Vertical(id="games-list")
            with Horizontal(id="setup-buttons"):
                yield Button("Начать", id="start", variant="success")
                yield Button("Демо (/test)", id="demo")
                yield Button("Отмена", id="cancel")

    def on_mount(self) -> None:
        self._refresh_world_desc(self._default_world)
        self._toggle_world_custom(self._default_world)
        self._apply_class_stats("wanderer")
        self._populate_games()
        if self.settings is not None:
            try:
                self.query_one("#llm_url", Input).value = self.settings.effective_base_url
                self.query_one("#llm_model", Input).value = self.settings.effective_model
            except Exception:
                pass

    def _toggle_world_custom(self, world_id: str) -> None:
        try:
            self.query_one("#world-custom", Vertical).display = world_id == CUSTOM_WORLD
        except Exception:
            pass

    # -- helpers -------------------------------------------------------------

    def _apply_class_stats(self, char_class: str) -> None:
        stats = CLASS_STATS.get(char_class, CLASS_STATS["wanderer"])
        for key in STAT_KEYS:
            try:
                self.query_one(f"#stat_{key}", Input).value = str(stats.get(key, 0))
            except Exception:
                pass

    def _populate_games(self) -> None:
        try:
            container = self.query_one("#games-list", Vertical)
        except Exception:
            return
        if not self.games:
            container.mount(Static("Сохранённых игр нет.", classes="hint"))
            return
        for index, info in enumerate(self.games):
            slot = str(info.get("slot", ""))
            gt = info.get("game_time") or {}
            time_text = f"Д{gt.get('day', '?')} {int(gt.get('hour', 0)):02d}:{int(gt.get('minute', 0)):02d}" if gt else ""
            location = str(info.get("location", ""))
            hp_text = f"HP {info.get('hp', '?')}/{info.get('max_hp', '?')}"
            world = str(info.get("world_id") or info.get("active_branch") or "")
            label = " · ".join(part for part in (slot, world, location, time_text, hp_text) if part)
            container.mount(Button(label, id=f"game-{index}"))

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "world":
            world_id = str(event.value)
            self._refresh_world_desc(world_id)
            self._toggle_world_custom(world_id)
        elif event.select.id == "char_class":
            self._apply_class_stats(str(event.value))

    def _refresh_world_desc(self, world_id: str) -> None:
        if world_id == CUSTOM_WORLD:
            try:
                self.query_one("#world-desc", Static).update(
                    "Свой мир: опиши его словами ниже и нажми «Создать мир с нейросетью».\n"
                    "Нейросеть дополнит детали и сделает полноценный мир."
                )
            except Exception:
                pass
            return
        world = self._world_by_id.get(world_id)
        text = ""
        if world is not None:
            lines = [
                f"{world.name} — {world.subtitle}".strip(" —"),
                world.description,
                f"Тон: {world.tone}" if world.tone else "",
                f"Эпоха: {world.era}" if world.era else "",
                f"Магия: {world.magic}" if world.magic else "",
                "Народы: " + ", ".join(world.peoples) if world.peoples else "",
            ]
            text = "\n".join(line for line in lines if line)
        try:
            self.query_one("#world-desc", Static).update(text)
        except Exception:
            pass

    # -- LLM helpers ---------------------------------------------------------

    def _client(self) -> LLMClient | None:
        if self.settings is None or not self.settings.has_llm or self.settings.effective_offline:
            return None
        return LLMClient(self.settings)

    async def _generate_world(self) -> None:
        status = self.query_one("#world_status", Static)
        prompt = self.query_one("#world_prompt", Input).value.strip()
        client = self._client()
        if client is None:
            status.update("Нейросеть недоступна (оффлайн). Выбери готовый мир слева.")
            return
        if not prompt:
            status.update("Опиши мир в поле выше.")
            return
        status.update("Придумываю мир…")
        pack = await forge.structurize(client, prompt, self.settings.data_path)  # type: ignore[union-attr]
        await client.aclose()
        draft = forge.create_draft(prompt, self.settings.data_path, pack=pack)
        target = forge.finalize(draft, self.settings.data_path)
        world = WorldMeta(**{**pack.world, "id": target.name})
        self._world_by_id[target.name] = world
        self._world_options = [(w.name, w.id) for w in self.worlds]
        self._world_options.append((world.name, target.name))
        self._world_options.append(("Свой мир… (создать)", CUSTOM_WORLD))
        select = self.query_one("#world", Select)
        select.set_options(self._world_options)
        select.value = target.name
        self._refresh_world_desc(target.name)
        self._toggle_world_custom(target.name)
        status.update(f"Мир создан: {world.name}. Он выбран для игры.")

    async def _generate_character(self) -> None:
        status = self.query_one("#char_status", Static)
        world_id = str(self.query_one("#world", Select).value)
        world = self._world_by_id.get(world_id)
        client = self._client()
        if client is None:
            status.update("Нейросеть недоступна (оффлайн). Впиши героя вручную.")
            return
        status.update("Собираю героя…")
        context = {
            "world": world.name if world else "",
            "tone": world.tone if world else "",
            "era": world.era if world else "",
            "peoples": world.peoples if world else [],
        }
        content = await client.complete(
            [
                {"role": "system", "content": CHARACTER_PROMPT},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ],
            response_format={"type": "json_object"},
            temperature=0.9,
        )
        await client.aclose()
        if not content:
            status.update("Не удалось. Впиши героя вручную.")
            return
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            status.update("Нейросеть ответила странно. Впиши вручную.")
            return
        if data.get("name"):
            self.query_one("#name", Input).value = str(data["name"])
        if data.get("role"):
            self.query_one("#custom_role", Input).value = str(data["role"])
        for key in STAT_KEYS:
            value = (data.get("stats") or {}).get(key)
            if value is not None:
                self.query_one(f"#stat_{key}", Input).value = str(value)
        if data.get("background"):
            self.query_one("#background", TextArea).text = str(data["background"])
        if data.get("motivation"):
            self.query_one("#motivation", Input).value = str(data["motivation"])
        items = data.get("items") or []
        if items:
            self.query_one("#items", TextArea).text = "\n".join(str(i) for i in items)
        status.update(f"Готово: {data.get('name', '—')} ({data.get('role', 'роль не задана')})")

    async def _check_connection(self) -> None:
        status = self.query_one("#llm_status", Static)
        base_url = self.query_one("#llm_url", Input).value.strip()
        api_key = self.query_one("#llm_key", Input).value.strip() or (
            self.settings.effective_api_key if self.settings else ""
        )
        if not base_url or not api_key:
            status.update("Укажи эндпоинт и ключ.")
            return
        status.update("Проверяю соединение…")
        probe = Settings(llm_base_url=base_url, llm_api_key=api_key, offline_mode="never")
        client = LLMClient(probe)
        models = await client.list_models()
        await client.aclose()
        if models:
            status.update("Доступные модели: " + ", ".join(models[:20]))
        else:
            status.update("Не удалось получить список моделей. Проверь эндпоинт и ключ.")

    # -- collect / buttons ---------------------------------------------------

    def _collect(self) -> dict[str, Any]:
        stats: dict[str, int] = {}
        for key in STAT_KEYS:
            raw = self.query_one(f"#stat_{key}", Input).value.strip()
            try:
                stats[key] = int(raw)
            except ValueError:
                stats[key] = CLASS_STATS["wanderer"].get(key, 0)
        items_text = self.query_one("#items", TextArea).text
        return {
            "world": str(self.query_one("#world", Select).value),
            "name": self.query_one("#name", Input).value.strip(),
            "char_class": str(self.query_one("#char_class", Select).value),
            "custom_role": self.query_one("#custom_role", Input).value.strip(),
            "stats": stats,
            "background": self.query_one("#background", TextArea).text.strip(),
            "motivation": self.query_one("#motivation", Input).value.strip(),
            "items_text": items_text.strip(),
            "setting": self.query_one("#setting", TextArea).text.strip(),
            "lethality": str(self.query_one("#lethality", Select).value),
            "laws": self.query_one("#laws", Input).value.strip(),
            "rule_numbers": self.query_one("#rule_numbers", Input).value.strip(),
            "edits": self.query_one("#edits", TextArea).text.strip(),
            "llm_url": self.query_one("#llm_url", Input).value.strip(),
            "llm_key": self.query_one("#llm_key", Input).value.strip(),
            "llm_model": self.query_one("#llm_model", Input).value.strip(),
        }

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "cancel":
            self.dismiss({"action": "cancel"})
        elif button_id == "demo":
            self.dismiss({"action": "demo"})
        elif button_id == "gen_world":
            self.run_worker(self._generate_world(), exclusive=False, group="genworld")
        elif button_id == "gen_char":
            self.run_worker(self._generate_character(), exclusive=False, group="genchar")
        elif button_id == "check_llm":
            self.run_worker(self._check_connection(), exclusive=False, group="llmcheck")
        elif button_id.startswith("game-"):
            try:
                index = int(button_id.split("-", 1)[1])
                slot = str(self.games[index]["slot"])
            except (ValueError, IndexError, KeyError):
                return
            self.dismiss({"action": "load", "slot": slot})
        else:
            config = self._collect()
            config["action"] = "start"
            self.dismiss(config)
