"""Textual application: assembles every panel and wires input to the engine.

Layout follows spec section 3.2. The app is a thin shell: it never decides game
mechanics. Plain input goes through :class:`~engine.game_loop.GameLoop`; slash
commands call deterministic engine functions (or the read-only handbook).
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll

from engine import game_loop as loop_mod
from engine import mandate as mandate_mod
from engine import npc_engine as npc
from engine import rules_engine as rules_engine
from engine import rules_handbook as rules
from engine import time_engine as tm
from engine import trade_engine as trade
from engine import world_forge as forge
from engine import world_map as world
from engine.catalogs import Catalogs, load_catalogs
from engine.config import Settings, get_settings
from engine.game_loop import GameLoop
from engine.llm_client import LLMClient
from engine.narrator import explain_rules
from engine.state_manager import StateManager, StateNotFoundError
from engine.worlds import DEFAULT_WORLD, DEFAULT_WORLD_ID, list_worlds, load_world
from ui import glyphs
from ui import theme_gruvbox as theme
from ui.theme_gruvbox import GRUVBOX
from ui.widgets import (
    CharacterCard,
    CommandBar,
    DialoguePanel,
    HeaderBar,
    InventoryTab,
    MapPanel,
    NarrativeLog,
    PartyPanel,
    QuestTab,
)
from ui.widgets.command_bar import CommandInput
from ui.widgets.inventory_tab import render_inventory
from ui.widgets.map_panel import render_map
from ui.widgets.party_panel import render_party
from ui.widgets.quest_tab import render_quests
from ui.widgets.setup_screen import SetupScreen

HELP_TEXT = (
    "СПРАВКА — что делает каждая команда и зачем:\n"
    "\n"
    "/main (/setup, /new) — открыть главное меню: выбор мира, персонаж, тон, правила,\n"
    "   правки, модель и список прошлых игр. Отсюда начинается новая игра.\n"
    "/help — эта справка. /exit — выйти (или Ctrl+Q).\n"
    "\n"
    "/rule <тема> — объяснить механику словами (без записи в историю). Темы: checks,\n"
    "   combat, time, travel, inventory, npc, crime, canon, llm, commands.\n"
    "/rules — твои правила мира: показать и менять (HP-потолок, вес, отношение,\n"
    "   частота встреч, летальность) + /rules save|load|list для своих наборов.\n"
    "/mandate <текст> — правки, которые ВАЖНЕЕ движка (напр. «нет магии», «никто не\n"
    "   умирает»). Нейросеть переводит их в ограничения своих прав и правила движка.\n"
    "/world — создать свой мир (new/check/refine/accept/play/list/discard).\n"
    "\n"
    "/roll <NdM+k> [причина] — ручной бросок кубиков (результат идёт в историю).\n"
    "/map — показать карту-сетку. /inv — инвентарь. /stats — характеристики.\n"
    "/quests — квесты и лог NPC. /time — игровое время. /party — отряд.\n"
    "/travel <локация> — идти в соседнюю локацию (тратит время, возможна встреча).\n"
    "/talk <npc> — социальная проверка (убедить/запугать). /say <текст> — реплика.\n"
    "/shop <npc> — ассортимент торговца. /buy <предмет> — купить. /sell <предмет> — продать.\n"
    "/ask <вопрос> — спросить у NPC «а есть ли у тебя …?» (например, /ask зелье).\n"
    "/use <item> — применить предмет. /equip и /unequip <item> — надеть/снять.\n"
    "\n"
    "/save — сохранить сейчас (автосохранение и так идёт каждый ход).\n"
    "/load <слот> — загрузить игру. /slots — имена сохранений. /games — список\n"
    "   прошлых игр (то же, что вкладка в главном меню).\n"
    "/snapshot — пересобрать сводку канона. /export — выгрузить хронику в Markdown.\n"
    "/log — сохранить весь текст истории в narrative.txt (чтобы копировать).\n"
    "/retcon list|undo <id>|edit <id> narration <текст> — работа с историей: показать\n"
    "   последние события, откатить ветку (без удаления прошлого) или поправить\n"
    "   только описание (механика не меняется).\n"
    "\n"
    "/model — выбрать нейросеть: список доступных моделей; /model <имя> — сменить;\n"
    "   /model endpoint <url> и /model key <ключ> — подключить любого провайдера.\n"
    "/emoji on|off — включить/выключить эмодзи (при выключении — ASCII-символы,\n"
    "   вёрстка всегда ровная). То же можно задать через EMOJI=false в .env.\n"
    "/fudge on|off — подкрутка кубиков для драматизма (каждая подкрутка логируется).\n"
    "/test — демо-мир, где уже что-то произошло (быстрый старт без настройки).\n"
    "\n"
    "Горячие клавиши: текст выделяется мышью, Ctrl+C копирует, Ctrl+Q — выход."
)

#: character templates used by the setup wizard (stat blocks + starting kit)
CLASS_TEMPLATES: dict[str, dict[str, object]] = {
    "warrior": {"stats": {"STR": 3, "DEX": 1, "INT": 1, "WIS": 1, "CHA": 0, "LUK": 1}, "hp": 28, "mp": 2, "ac": 13, "weapon": "heavy_axe", "armor": "leather_armor"},
    "rogue": {"stats": {"STR": 1, "DEX": 3, "INT": 2, "WIS": 1, "CHA": 1, "LUK": 2}, "hp": 20, "mp": 4, "ac": 12, "weapon": "dagger", "armor": "leather_armor"},
    "mage": {"stats": {"STR": 0, "DEX": 1, "INT": 4, "WIS": 2, "CHA": 1, "LUK": 1}, "hp": 16, "mp": 12, "ac": 10, "weapon": "dagger", "armor": None},
    "ranger": {"stats": {"STR": 2, "DEX": 3, "INT": 1, "WIS": 2, "CHA": 0, "LUK": 2}, "hp": 22, "mp": 4, "ac": 12, "weapon": "dagger", "armor": "leather_armor"},
    "wanderer": {"stats": {"STR": 3, "DEX": 1, "INT": 2, "WIS": 0, "CHA": 1, "LUK": 2}, "hp": 25, "mp": 10, "ac": 12, "weapon": "heavy_axe", "armor": "leather_armor"},
}

#: which world the wizard defaults to
DEFAULT_SETUP_WORLD = DEFAULT_WORLD_ID


class RpgApp(App):
    """The AI RPG Engine TUI."""

    CSS_PATH = "css/gruvbox.tcss"
    ALLOW_SELECT = True
    BINDINGS = [
        ("ctrl+q", "quit", "Выход"),
    ]

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        slot: str | None = None,
        client: LLMClient | None = None,
        skip_setup: bool = False,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self.settings = settings or get_settings()
        self._explicit_slot = slot
        self.slot = slot or "default"
        self.catalogs: Catalogs = load_catalogs(self.settings.data_path)
        self.client = client or LLMClient(self.settings)
        self.manager: StateManager | None = None
        self.game: GameLoop | None = None
        self._map_reveal = False
        self._dialogue_npc: str | None = None
        self._draft_dir: Path | None = None
        self._busy = False
        self.skip_setup = skip_setup
        self.mode = "play"  # "setup" while the setting questionnaire runs
        self._setup_index = 0
        self._setup_answers: dict[str, str] = {}

    # -- Textual hooks -------------------------------------------------------

    def get_css_variables(self) -> dict[str, str]:
        # Merge with Textual's defaults so built-in widgets keep resolving
        # $background / $foreground / etc., then lay Gruvbox on top.
        variables = super().get_css_variables()
        variables.update(theme.get_css_variables())
        variables.setdefault("background", theme.GRUVBOX["bg0"])
        variables["foreground"] = theme.GRUVBOX["fg1"]
        return variables

    def compose(self) -> ComposeResult:
        yield HeaderBar(id="header")
        with Horizontal(id="body"):
            with VerticalScroll(id="sidebar"):
                yield CharacterCard(id="character-card")
                yield PartyPanel(id="party-panel")
                yield MapPanel(id="map-panel")
                yield InventoryTab(id="inventory-tab")
                yield QuestTab(id="quest-tab")
                yield DialoguePanel(id="dialogue-panel")
            with Vertical(id="main"):
                yield NarrativeLog(id="narrative-log")
                yield CommandBar(id="command-bar")

    def on_mount(self) -> None:
        glyphs.set_emoji(self.settings.emoji)
        built = theme.gruvbox_theme()
        if built is not None:
            try:
                self.register_theme(built)
                self.theme = built.name
            except Exception:
                pass
        # Highlight color for mouse text selection.
        try:
            from rich.style import Style

            self.selection_style = Style(bgcolor=theme.GRUVBOX["bg3"], color=theme.GRUVBOX["fg0"])
        except Exception:
            pass

        if self._explicit_slot and StateManager.exists(self._explicit_slot, settings=self.settings):
            self.slot = self._explicit_slot
            self.manager = StateManager.load(self.slot, settings=self.settings)
            self.catalogs = self._catalogs_for_world(self.manager.state.flags.get("world_id"))
            self.game = GameLoop(self.manager, self.catalogs, self.client)
            self.mode = "play"
            self._greet_world()
        elif self.skip_setup:
            self._start_demo(self.slot)
        else:
            self._open_setup()
        self._focus_input()

    # -- slot management / setup --------------------------------------------

    def _open_slot(self, slot: str) -> StateManager:
        if StateManager.exists(slot, settings=self.settings):
            return StateManager.load(slot, settings=self.settings)
        return StateManager.new_game(slot, settings=self.settings)

    def _catalogs_for_world(self, world_id: str | None) -> Catalogs:
        """Load the catalogs of a save's world (falls back to the default)."""
        try:
            return load_world(world_id or DEFAULT_WORLD_ID, self.settings.data_path).catalogs
        except Exception:
            return load_catalogs(self.settings.data_path)

    def _load_slot(self, slot: str) -> bool:
        try:
            self.manager = StateManager.load(slot, settings=self.settings)
        except Exception as exc:  # noqa: BLE001
            self.log_entry(f"Не удалось загрузить «{slot}»: {exc}", "system")
            return False
        self.slot = slot
        self.catalogs = self._catalogs_for_world(self.manager.state.flags.get("world_id"))
        self.game = GameLoop(self.manager, self.catalogs, self.client)
        self.mode = "play"
        self._greet_world()
        return True

    def _greet_world(self) -> None:
        location = self.catalogs.location(self.manager.state.world.current_location)
        self.log_entry("Мир просыпается. Тишина ждёт твоего слова.", "system")
        lore = self.manager.state.flags.get("world_lore") or {}
        if lore:
            self.log_entry(
                f"\U0001f3ad {lore.get('name', '')} · {lore.get('subtitle', '')}".strip(" ·"),
                "system",
            )
            for label, key in (("\U0001f551 Эпоха", "era"), ("\u2728 Магия", "magic"), ("\U0001f465 Народы", "peoples")):
                value = lore.get(key)
                if value:
                    text = ", ".join(value) if isinstance(value, list) else str(value)
                    self.log_entry(f"   {label}: {text}", "system")
        premise = lore.get("premise")
        if premise:
            self.log_entry(f"\U0001f4dc {premise}", "system")
        self.log_entry(f"\U0001f4cd {location.name}. {location.description}", "location")
        if self.manager.state.dm_settings.model:
            self.settings.llm_model = self.manager.state.dm_settings.model
            self._rebuild_client()
        self.refresh_panels()
        self._focus_input()

    def _start_demo(self, slot: str) -> None:
        """Create a ready-made default world so the player can jump straight in."""
        self.slot = slot
        self.catalogs = load_catalogs(self.settings.data_path)
        self.manager = StateManager.new_game(slot, settings=self.settings)
        self.manager.state.flags.setdefault("world_id", DEFAULT_WORLD_ID)
        self.manager.save()
        self.game = GameLoop(self.manager, self.catalogs, self.client)
        self.mode = "play"
        self.log_entry("Демо-мир загружен — тут уже что-то произошло.", "system")
        self._greet_world()

    def _open_setup(self) -> None:
        self.mode = "setup"
        self.push_screen(
            SetupScreen(
                list_worlds(self.settings.data_path),
                list(CLASS_TEMPLATES.keys()),
                default_world=DEFAULT_SETUP_WORLD,
                settings=self.settings,
                games=StateManager.saves_info(settings=self.settings),
            ),
            self._on_setup_result,
        )

    def _on_setup_result(self, result: dict | None) -> None:
        if not result or result.get("action") == "cancel":
            if self.manager is None:
                self._start_demo(self.slot)
            return
        action = result.get("action")
        if action == "demo":
            self._start_demo("test")
            return
        if action == "load":
            slot = str(result.get("slot") or "")
            if slot and not self._load_slot(slot):
                self._start_demo(self.slot)
            return
        self._finalize_setup(result)

    def _finalize_setup(self, config: dict) -> None:
        world_id = config.get("world") or DEFAULT_SETUP_WORLD
        if world_id == "__custom__":
            world_id = DEFAULT_SETUP_WORLD
        chosen = load_world(world_id, self.settings.data_path)
        self.catalogs = chosen.catalogs

        # Apply LLM configuration chosen in the wizard, then rebuild the client.
        if config.get("llm_url"):
            self.settings.llm_base_url = str(config["llm_url"])
        if config.get("llm_key"):
            self.settings.llm_api_key = str(config["llm_key"])
        elif config.get("llm_url"):
            self.settings.llm_api_key = self.settings.openrouter_api_key
        if config.get("llm_model"):
            self.settings.llm_model = str(config["llm_model"])
        self._rebuild_client()

        self.manager = StateManager.new_game(self.slot, settings=self.settings, world=chosen)
        state = self.manager.state

        char_class = str(config.get("char_class") or "wanderer").strip().lower()
        custom_role = str(config.get("custom_role") or "").strip()
        template = CLASS_TEMPLATES.get(char_class, CLASS_TEMPLATES["wanderer"])
        player = state.player
        player.name = str(config.get("name") or player.name) or player.name
        player.kind = "player"
        stats = config.get("stats") or template["stats"]
        player.stats = {str(k): int(v) for k, v in dict(stats).items()}
        player.max_hp = rules_engine.effective_max_hp(state.rules, int(template["hp"]))  # type: ignore[arg-type]
        player.hp = player.max_hp
        player.max_mp = int(template["mp"])  # type: ignore[arg-type]
        player.mp = player.max_mp
        player.ac = int(template["ac"])  # type: ignore[arg-type]
        state.flags["setting"] = str(config.get("setting") or chosen.meta.tone)
        state.flags["char_class"] = char_class
        state.flags["role"] = custom_role or char_class
        state.flags["background"] = str(config.get("background") or "")
        state.flags["motivation"] = str(config.get("motivation") or "")
        state.flags["world_id"] = chosen.meta.id

        from engine import inventory_engine as inv

        for item_id in self._resolve_items(str(config.get("items_text") or "")):
            if item_id in self.catalogs.items:
                inv.add_item(state, self.catalogs, player, item_id)

        lethality = str(config.get("lethality") or "standard").strip().lower()
        if lethality in rules_engine.LETHALITY_PRESETS:
            rules_engine.set_rule(state.rules, "lethality", lethality)
        for law in str(config.get("laws") or "").split(";"):
            rules_engine.add_law(state.rules, law)
        for key in ("carry_base", "world_attitude"):
            raw = str(config.get(key) or "").strip()
            if raw:
                try:
                    rules_engine.set_rule(state.rules, key, raw)
                except rules_engine.RuleError:
                    pass

        edits = str(config.get("edits") or "").strip()
        rule_numbers = str(config.get("rule_numbers") or "").strip()
        if rule_numbers:
            edits = (edits + "\n" + rule_numbers).strip()
        if edits:
            self._apply_mandate_text(edits, refine=not self.client.offline)

        self.manager.save()
        self.game = GameLoop(self.manager, self.catalogs, self.client)
        self.mode = "play"
        self.log_entry(f"\u2705 Мир «{chosen.meta.name}» создан. Играем!", "system")
        self._greet_world()

    def _resolve_items(self, text: str) -> list[str]:
        """Resolve natural-language "what you carry" to catalog item ids."""
        text = text.strip()
        if not text:
            return []
        lowered = text.lower()
        found: list[str] = []
        for item_id, item in self.catalogs.items.items():
            if item_id.lower() in lowered or item.name.lower() in lowered:
                found.append(item_id)
        if not found:
            for token in re.split(r"[\s,;.]+", lowered):
                if token in self.catalogs.items:
                    found.append(token)
        # de-duplicate, keep order
        seen: set[str] = set()
        return [item_id for item_id in found if not (item_id in seen or seen.add(item_id))]

    # -- mandate (player edits) ---------------------------------------------

    def _apply_mandate_text(self, text: str, *, refine: bool) -> list[str]:
        mandate = mandate_mod.rule_based_mandate(text)
        changes = mandate_mod.apply_mandate(self.manager.state.rules, mandate)
        self.log_entry("\U0001f9f9 Твои правки (важнее движка):", "system")
        if changes:
            for change in changes:
                self.log_entry(f"   \u2022 {change}", "system")
        else:
            self.log_entry("   \u2022 (распознать удалось только текст закона)", "system")
        if refine:
            self.run_worker(self._refine_mandate(text), exclusive=False, group="mandate")
        return changes

    async def _refine_mandate(self, text: str) -> None:
        mandate = await mandate_mod.parse_mandate(self.client, text)
        if mandate.source != "llm":
            return
        mandate_mod.apply_mandate(self.manager.state.rules, mandate)
        self.manager.save()
        self.log_entry("\U0001f9e0 Нейросеть уточнила, какие её права ограничены.", "system")
        self.refresh_panels()

    # -- input ---------------------------------------------------------------

    def on_input_submitted(self, event) -> None:  # noqa: ANN001
        text = (event.value or "").strip()
        command_input = self.query_one(CommandInput)
        command_input.value = ""
        if not text:
            self._focus_input()
            return
        command_input.remember(text)
        self.run_worker(self.handle_input(text), exclusive=False, group="turn")
        self._focus_input()

    def _focus_input(self) -> None:
        try:
            self.query_one(CommandInput).focus()
        except Exception:
            pass

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.refresh_header()

    def _log_session(self, kind: str, text: str) -> None:
        """Append a plain-text line to the slot's session log (saved with the game)."""
        if self.manager is None:
            return
        try:
            from datetime import datetime

            path = self.manager.slot_dir / "session.log"
            stamp = self.manager.state.time
            prefix = f"[Д{stamp.day} {stamp.hour:02d}:{stamp.minute:02d}]"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"{prefix} {datetime.now():%H:%M:%S} {kind}: {text}\n")
        except Exception:
            pass

    _OUTCOME_STYLE = {
        "crit_success": GRUVBOX["purple"],
        "success": GRUVBOX["green"],
        "failure": GRUVBOX["red"],
        # black on red so a critical failure stays readable on the dark theme
        "crit_failure": f"bold {GRUVBOX['black']} on {GRUVBOX['red']}",
    }

    def _log_roll_detail(self, roll, reason: str | None = None) -> None:
        from ui.widgets.narrative_log import strip_emojis

        outcome = getattr(roll, "outcome", None)
        outcome_ru = {
            "crit_success": "критический успех",
            "success": "успех",
            "failure": "провал",
            "crit_failure": "критический провал",
        }.get(outcome or "", "")
        rolls = "/".join(str(value) for value in getattr(roll, "rolls", []) or [])
        modifier = getattr(roll, "modifier", 0) or 0
        detail = f"Бросок {getattr(roll, 'notation', '?')}"
        if reason:
            detail += f" ({reason})"
        detail += f": [{rolls}]"
        if modifier:
            detail += f" {'+' if modifier >= 0 else '-'} {abs(modifier)}"
        detail += f" = {getattr(roll, 'total', '?')}"
        dc = getattr(roll, "dc", None)
        if dc is not None:
            detail += f" против DC {dc}"
        if outcome_ru:
            detail += f" — {outcome_ru}"
        detail = strip_emojis(detail)
        style = self._OUTCOME_STYLE.get(outcome or "", GRUVBOX["fg2"])
        self.query_one(NarrativeLog).add(detail, "dice", style=style)
        self._log_session("бросок", detail)

    _EFFECT_COMMENTS = {
        "hp_delta": "здоровье",
        "mp_delta": "мана",
        "currency_delta": "деньги",
        "xp_delta": "опыт",
        "rep_delta": "репутация",
        "item_add": "получен предмет",
        "item_remove": "потерян предмет",
        "status_add": "состояние наложено",
        "status_remove": "состояние снято",
        "discover": "открыта локация",
        "quest_stage": "квест",
        "death": "смерть",
        "exposure": "огласка",
        "body_disposed": "тело спрятано",
        "rest": "отдых (минут)",
        "combat_start": "бой начался",
    }

    def _describe_effects(self, event) -> list[tuple[str, str]]:
        """(text, style) lines describing what changed, with a short comment."""
        if event is None:
            return []
        effects = getattr(event, "effects", None) or []
        rows: list[tuple[str, str]] = []
        for effect in effects:
            kind = str(effect.get("kind", ""))
            target = effect.get("target", "")
            value = effect.get("value")
            comment = self._EFFECT_COMMENTS.get(kind, kind)
            if kind in ("hp_delta", "mp_delta", "currency_delta", "xp_delta", "rep_delta") and isinstance(value, (int, float)):
                sign = "+" if value >= 0 else "−"
                text = f"{comment} {target}: {sign}{abs(value)}"
                style = GRUVBOX["green"] if value >= 0 else GRUVBOX["red"]
            elif kind in ("item_add", "discover", "status_add"):
                text = f"{comment}: {value if value is not None else target}"
                style = GRUVBOX["green"]
            elif kind in ("item_remove", "status_remove", "death"):
                text = f"{comment}: {value if value is not None else target}"
                style = GRUVBOX["red"]
            else:
                text = f"{comment}: {value if value is not None else target}"
                style = GRUVBOX["fg2"]
            rows.append((text, style))
        return rows

    def _log_effects(self, event) -> None:
        for text, style in self._describe_effects(event):
            self.query_one(NarrativeLog).add(text, "system", style=style)

    async def handle_input(self, text: str) -> None:
        if self.game is None:
            self.log_entry("Сначала заверши создание мира (/setup).", "system")
            return
        assert self.game is not None
        self.log_entry(text, "player")
        self._log_session("игрок", text)
        try:
            if text.startswith("/"):
                before = len(self.manager.canon)
                await self.handle_command(text)
                if len(self.manager.canon) > before:
                    self._log_effects(self.manager.canon.events[-1])
            else:
                self._set_busy(True)
                try:
                    result = await self.game.process(text)
                finally:
                    self._set_busy(False)
                self.log_entry(result.narration, "gm" if result.ok else "system")
                self._log_session("мир", result.narration)
                if result.roll is not None:
                    self._log_roll_detail(result.roll, text)
                self._log_effects(result.event)
        except Exception as exc:  # never let a turn kill the app
            self.log_entry(f"Ошибка хода: {exc}", "system")
        self._autosave()
        self.refresh_panels()
        self._focus_input()

    def _autosave(self) -> None:
        if not self.settings.autosave or self.manager is None:
            return
        try:
            self.manager.save()
        except Exception:
            pass

    # -- commands ------------------------------------------------------------

    async def handle_command(self, text: str) -> None:
        parts = text[1:].split(maxsplit=2)
        if not parts:
            return
        command = parts[0].lower()
        if command in ("new", "world_new", "main", "menu"):
            command = "setup"
        args = parts[1:]
        handler = getattr(self, f"_cmd_{command}", None)
        if handler is None:
            self.log_entry(f"Неизвестная команда: /{command} (/help)", "system")
            return
        await handler(args)

    async def _cmd_help(self, args: list[str]) -> None:
        self.log_entry(HELP_TEXT, "system")

    async def _cmd_test(self, args: list[str]) -> None:
        self._start_demo("test")

    async def _cmd_setup(self, args: list[str]) -> None:
        self._open_setup()

    async def _cmd_mandate(self, args: list[str]) -> None:
        text = " ".join(args).strip()
        if not text:
            constraints = mandate_mod.narrator_constraints(self.manager.state.rules)
            self.log_entry(constraints or "Правок нет. Добавь: /mandate нет магии", "system")
            return
        self._apply_mandate_text(text, refine=not self.client.offline)
        self.manager.save()
        self.refresh_panels()

    # -- world authoring (/world) -------------------------------------------

    def _start_world(self, world_id: str, *, slot: str | None = None) -> None:
        chosen = load_world(world_id, self.settings.data_path)
        self.catalogs = chosen.catalogs
        self.slot = slot or self.slot
        self.manager = StateManager.new_game(self.slot, settings=self.settings, world=chosen)
        self.game = GameLoop(self.manager, self.catalogs, self.client)
        self.mode = "play"
        self.log_entry(f"\u2705 Мир «{chosen.meta.name}» запущен.", "system")
        self._greet_world()

    def _show_pack_summary(self, pack: forge.Pack, directory: Path) -> None:
        world = pack.world
        emoji = world.get("emoji") or "\U0001f30d"
        name = world.get("name", "?")
        subtitle = world.get("subtitle", "")
        self.log_entry(f"\U0001f5fa\ufe0f {emoji} {name} — {subtitle}", "system")
        for label, key in (("тон", "tone"), ("эпоха", "era"), ("магия", "magic")):
            if world.get(key):
                self.log_entry(f"   {label}: {world[key]}", "system")
        if world.get("peoples"):
            self.log_entry("   народы: " + ", ".join(world["peoples"]), "system")
        if world.get("laws"):
            for law in world["laws"]:
                self.log_entry(f"   \U0001f4dc {law}", "system")
        self.log_entry(
            f"   локаций {len(pack.locations)}, предметов {len(pack.items)}, "
            f"NPC {len(pack.npcs)}, заклинаний {len(pack.spells)}",
            "system",
        )
        self.log_entry(f"   файлы: {directory}", "system")
        self.log_entry(
            "   правь руками (без нейросети) · /world check — проверка нейросетью · "
            "/world refine <текст> · /world accept · /world discard",
            "system",
        )

    async def _cmd_world(self, args: list[str]) -> None:
        tokens = " ".join(args).split(maxsplit=1)
        sub = tokens[0].lower() if tokens else "list"
        rest = tokens[1] if len(tokens) > 1 else ""

        if sub in ("list", ""):
            drafts = forge.list_drafts(self.settings.data_path)
            from engine.worlds import list_worlds

            worlds = [w.id for w in list_worlds(self.settings.data_path)]
            self.log_entry("\U0001f4c1 Миры: " + ", ".join(worlds), "system")
            self.log_entry(
                "\U0001f6e0\ufe0f Черновики: " + (", ".join(drafts) if drafts else "(нет)"),
                "system",
            )
            self.log_entry("Создать: /world new <описание мира>", "system")
            return

        if sub == "new":
            if not rest.strip():
                self.log_entry("Опиши мир: /world new мрачное фэнтези про город на костях кита", "system")
                return
            self.log_entry("\U0001f9e0 Структурирую и дополняю твой мир…", "system")
            pack = await forge.structurize(self.client, rest, self.settings.data_path)
            directory = forge.create_draft(rest, self.settings.data_path, pack=pack)
            self._draft_dir = directory
            self._show_pack_summary(forge.read_pack(directory), directory)
            return

        if sub in ("check", "refine"):
            if self._draft_dir is None:
                self.log_entry("Сначала /world new <описание>.", "system")
                return
            feedback = rest if sub == "refine" and rest else None
            self.log_entry("\U0001f50e Нейросеть проверяет и правит черновик…", "system")
            pack = await forge.review(self.client, self._draft_dir, self.settings.data_path, feedback=feedback)
            forge.write_pack(self._draft_dir, pack)
            self._show_pack_summary(pack, self._draft_dir)
            return

        if sub == "accept":
            if self._draft_dir is None:
                self.log_entry("Нет активного черновика.", "system")
                return
            target = forge.finalize(self._draft_dir, self.settings.data_path, world_id=rest or None)
            self._draft_dir = None
            self.log_entry(f"\u2705 Мир сохранён: {target.name}. Запустить: /world play {target.name}", "system")
            return

        if sub == "discard":
            if self._draft_dir is not None and self._draft_dir.exists():
                shutil.rmtree(self._draft_dir)
                self.log_entry("\U0001f5d1\ufe0f Черновик удалён.", "system")
            self._draft_dir = None
            return

        if sub == "play":
            if not rest.strip():
                self.log_entry("Формат: /world play <id>", "system")
                return
            self._start_world(rest.strip())
            return

        self.log_entry(
            "Подкоманды: new <описание> · check · refine <текст> · accept [id] · play <id> · list · discard",
            "system",
        )

    async def _cmd_rules(self, args: list[str]) -> None:
        ruleset = self.manager.state.rules
        tokens = " ".join(args).split()

        if not tokens:
            self.log_entry("\U0001f527 ПРАВИЛА МИРА (их придумываешь ты)", "system")
            for key, value, desc in rules_engine.describe(ruleset):
                self.log_entry(f"  {key:<18} = {value:<10} — {desc}", "system")
            if ruleset.laws:
                self.log_entry("\U0001f4dc Законы мира:", "system")
                for index, law in enumerate(ruleset.laws):
                    self.log_entry(f"  [{index}] {law}", "system")
            else:
                self.log_entry("  (законов пока нет — /rules law add <текст>)", "system")
            if ruleset.custom:
                self.log_entry(
                    "\U0001f3b2 Свои параметры: "
                    + ", ".join(f"{k}={v}" for k, v in ruleset.custom.items()),
                    "system",
                )
            self.log_entry(
                "Меняй: /rules set <ключ> <знач> · /rules lethality soft|standard|hardcore · "
                "/rules law add|del · /rules add <ключ> <знач> · /rules reset",
                "system",
            )
            self.log_entry(
                "Наборы: /rules save <имя> · /rules load <имя> · /rules list",
                "system",
            )
            return

        action = tokens[0].lower()
        try:
            if action == "set" and len(tokens) >= 3:
                rules_engine.set_rule(ruleset, tokens[1], " ".join(tokens[2:]))
                self.log_entry(f"\u2705 {tokens[1]} = {' '.join(tokens[2:])}", "system")
            elif action == "lethality" and len(tokens) >= 2:
                rules_engine.set_rule(ruleset, "lethality", tokens[1])
                self.log_entry(f"\U0001f480 Летальность: {ruleset.lethality}", "system")
            elif action == "law" and len(tokens) >= 3 and tokens[1] == "add":
                rules_engine.add_law(ruleset, " ".join(tokens[2:]))
                self.log_entry("\U0001f4dc Закон мира добавлен. Нейросеть будет его соблюдать.", "system")
            elif action == "law" and len(tokens) >= 3 and tokens[1] == "del":
                removed = rules_engine.remove_law(ruleset, int(tokens[2]))
                self.log_entry("\U0001f5d1\ufe0f Удалён." if removed else "Нет такого номера.", "system")
            elif action == "add" and len(tokens) >= 2:
                ruleset.custom[tokens[1]] = " ".join(tokens[2:])
                self.log_entry(f"\U0001f3b2 {tokens[1]} = {ruleset.custom[tokens[1]]}", "system")
            elif action == "save" and len(tokens) >= 2:
                name = " ".join(tokens[1:])
                ruleset.name = name
                path = rules_engine.save_ruleset(
                    ruleset, rules_engine.rulesets_dir(self.settings.data_path), name
                )
                self.log_entry(f"\U0001f4be Набор правил сохранён: {path.name}", "system")
            elif action == "load" and len(tokens) >= 2:
                name = " ".join(tokens[1:])
                directory = rules_engine.rulesets_dir(self.settings.data_path)
                target = directory / f"{name.replace(' ', '_')}.json"
                if not target.exists():
                    self.log_entry(f"Нет набора правил «{name}». (/rules list)", "system")
                    return
                loaded = rules_engine.load_ruleset(target)
                self.manager.state.rules = loaded
                self.log_entry(f"\U0001f4c2 Загружен набор правил «{loaded.name}».", "system")
            elif action == "list":
                names = rules_engine.list_rulesets(rules_engine.rulesets_dir(self.settings.data_path))
                self.log_entry(
                    "\U0001f4c1 Наборы правил: " + (", ".join(names) if names else "(нет)"),
                    "system",
                )
            elif action == "reset":
                rules_engine.reset(ruleset)
                self.log_entry("\u267b\ufe0f Правила сброшены.", "system")
            else:
                self.log_entry("Неизвестное действие /rules (введи /rules для справки).", "system")
                return
        except (rules_engine.RuleError, ValueError, IndexError) as exc:
            self.log_entry(f"\u26a0\ufe0f {exc}", "system")
            return
        self.manager.save()
        self.refresh_panels()

    async def _cmd_rule(self, args: list[str]) -> None:
        query = " ".join(args)
        result = rules.explain(query, state=self.manager.state)
        text = await explain_rules(self.client, query, result.text)
        self.log_entry(text, "system")

    async def _cmd_roll(self, args: list[str]) -> None:
        if not args:
            self.log_entry("Формат: /roll 2d6+3 [причина]", "system")
            return
        notation = args[0]
        reason = " ".join(args[1:]) or "ручной бросок"
        engine = self.manager.dice_engine()
        try:
            roll = engine.roll(notation)
        except ValueError as exc:
            self.log_entry(f"Плохая нотация: {exc}", "system")
            return
        self.manager.store_dice(engine)
        self._log_roll_detail(roll, reason)

    async def _cmd_map(self, args: list[str]) -> None:
        self._map_reveal = not self._map_reveal
        rendered = render_map(self.manager.state, self.catalogs, reveal=self._map_reveal)
        self.log_entry(str(rendered), "system")

    async def _cmd_inv(self, args: list[str]) -> None:
        self.log_entry(str(render_inventory(self.manager.state, self.catalogs, self.manager.state.player)), "system")

    async def _cmd_stats(self, args: list[str]) -> None:
        player = self.manager.state.player
        self.log_entry(
            f"{player.name} Lv.{player.level} · HP {player.hp}/{player.max_hp} · "
            f"AC {player.ac} · " + " ".join(f"{k} {v}" for k, v in player.stats.items()),
            "system",
        )

    async def _cmd_quests(self, args: list[str]) -> None:
        self.log_entry(str(render_quests(self.manager.state, self.catalogs)), "system")

    async def _cmd_party(self, args: list[str]) -> None:
        self.log_entry(str(render_party(self.manager.state, self.catalogs)), "system")

    async def _cmd_time(self, args: list[str]) -> None:
        self.log_entry(tm.describe(self.manager.state), "system")

    async def _cmd_save(self, args: list[str]) -> None:
        self.manager.save()
        self.log_entry("Сохранено. Канон проверен и записан.", "system")
        self.refresh_header(saved=True)

    async def _cmd_load(self, args: list[str]) -> None:
        if not args:
            self.log_entry("Формат: /load <слот> · список: /games", "system")
            return
        self._load_slot(args[0])

    async def _cmd_games(self, args: list[str]) -> None:
        self._open_setup()

    async def _cmd_shop(self, args: list[str]) -> None:
        if args:
            npc_id = self.game.resolve_npc(args[0])
            if npc_id:
                self._dialogue_npc = npc_id
        if not self._dialogue_npc:
            self.log_entry("Сначала /talk <npc> или /shop <npc>.", "system")
            return
        if not trade.is_merchant(self.catalogs, self._dialogue_npc):
            self.log_entry("Этот не торгует.", "system")
            return
        for line in trade.stock_lines(self.catalogs, self._dialogue_npc):
            self.log_entry(line, "system")

    async def _cmd_buy(self, args: list[str]) -> None:
        if not args or not self._dialogue_npc:
            self.log_entry("Формат: /buy <предмет> (после /shop <npc>).", "system")
            return
        message = trade.buy(self.manager, self.catalogs, self._dialogue_npc, " ".join(args))
        self.log_entry(message, "system")

    async def _cmd_sell(self, args: list[str]) -> None:
        if not args or not self._dialogue_npc:
            self.log_entry("Формат: /sell <предмет> (после /talk <npc>).", "system")
            return
        message = trade.sell(self.manager, self.catalogs, self._dialogue_npc, " ".join(args))
        self.log_entry(message, "system")

    async def _cmd_ask(self, args: list[str]) -> None:
        if not args or not self._dialogue_npc:
            self.log_entry("Формат: /ask <вопрос> (после /talk <npc>).", "system")
            return
        answer = trade.answer_about(self.catalogs, self._dialogue_npc, " ".join(args))
        self.log_entry(answer, "system")

    async def _cmd_emoji(self, args: list[str]) -> None:
        tokens = [token.lower() for token in args]
        if tokens and tokens[0] in ("on", "off"):
            self.settings.emoji = tokens[0] == "on"
        glyphs.set_emoji(self.settings.emoji)
        self.refresh_panels()
        state = "включены" if self.settings.emoji else "выключены (ASCII)"
        self.log_entry(f"Эмодзи: {state}", "system")

    async def _cmd_slots(self, args: list[str]) -> None:
        slots = StateManager.list_slots(settings=self.settings)
        self.log_entry("Слоты: " + (", ".join(slots) if slots else "(нет)"), "system")

    async def _cmd_travel(self, args: list[str]) -> None:
        if not args:
            self.log_entry("Формат: /travel <локация>", "system")
            return
        destination = self.game.resolve_location(args[0])
        if destination is None:
            self.log_entry("Такой локации нет на карте.", "system")
            return
        try:
            world.travel(self.manager, self.catalogs, destination)
        except world.WorldError as exc:
            self.log_entry(f"Путь закрыт: {exc}", "system")

    async def _cmd_talk(self, args: list[str]) -> None:
        if not args:
            self.log_entry("Формат: /talk <npc>", "system")
            return
        npc_id = self.game.resolve_npc(args[0])
        if npc_id is None:
            self.log_entry("Здесь не с кем говорить.", "system")
            return
        self._dialogue_npc = npc_id
        try:
            npc.social_check(self.manager, self.catalogs, npc_id, intent="ask")
        except npc.NpcError as exc:
            self.log_entry(str(exc), "system")

    async def _cmd_say(self, args: list[str]) -> None:
        text = " ".join(args)
        self.manager.append_event(
            type="dialogue", t=self.manager.state.time,
            actor=f"npc:{self._dialogue_npc}" if self._dialogue_npc else "player",
            input=text or "...",
            intent={"action_type": "say"},
        )
        self.log_entry(f"Ты говоришь: {text}", "player")

    async def _cmd_use(self, args: list[str]) -> None:
        from engine import inventory_engine as inv

        if not args:
            return
        instance_id = self.game.resolve_item(args[0])
        if instance_id is None:
            self.log_entry("Такого предмета нет.", "system")
            return
        try:
            inv.use_item(self.manager, self.catalogs, self.manager.state.player, instance_id)
        except inv.InventoryError as exc:
            self.log_entry(str(exc), "system")

    async def _cmd_equip(self, args: list[str]) -> None:
        from engine import inventory_engine as inv

        instance_id = self.game.resolve_item(args[0]) if args else None
        if instance_id is None:
            self.log_entry("Нечего надеть.", "system")
            return
        try:
            slot = inv.equip(self.manager.state, self.catalogs, self.manager.state.player, instance_id)
            self.log_entry(f"Надето ({slot}).", "system")
        except inv.InventoryError as exc:
            self.log_entry(str(exc), "system")

    async def _cmd_unequip(self, args: list[str]) -> None:
        from engine import inventory_engine as inv

        instance_id = self.game.resolve_item(args[0]) if args else None
        if instance_id and inv.unequip(self.manager.state, self.manager.state.player, instance_id):
            self.log_entry("Снято.", "system")
        else:
            self.log_entry("Это не надето.", "system")

    async def _cmd_snapshot(self, args: list[str]) -> None:
        snapshot = self.manager.build_snapshot()
        self.log_entry(f"Снапшот пересобран: фактов {len(snapshot['world_facts'])}.", "system")

    async def _cmd_retcon(self, args: list[str]) -> None:
        if not args or args[0] == "list":
            for event in self.manager.canon.events[-5:]:
                self.log_entry(f"{event.id} · {event.type} · {event.hash[:22]}…", "system")
            return
        action = args[0]
        if action == "undo":
            tokens = " ".join(args[1:]).split()
            if not tokens:
                self.log_entry("Формат: /retcon undo <evt_id> confirm", "system")
                return
            event_id = tokens[0]
            if "confirm" not in tokens:
                self.log_entry(f"Это откатит историю. Повтори с confirm: /retcon undo {event_id} confirm", "system")
                return
            try:
                branch = self.manager.canon.undo(event_id)
            except KeyError as exc:
                self.log_entry(f"Нет такого события: {exc}", "system")
                return
            self.manager.canon = branch
            self.manager.state.active_branch = branch.path.name
            self.manager.save()
            self.log_entry(f"Новая ветка: {branch.path.name}. Старая сохранена.", "system")
        elif action == "edit":
            tokens = " ".join(args[1:]).split(maxsplit=2)
            if len(tokens) < 3:
                self.log_entry("Формат: /retcon edit <evt_id> narration <текст>", "system")
                return
            event_id, field, value = tokens[0], tokens[1], tokens[2]
            try:
                self.manager.canon.amend(event_id, field, value)
            except (KeyError, ValueError) as exc:
                self.log_entry(f"Отказ: {exc}", "system")
                return
            self.log_entry("Правка записана как canon_amendment.", "system")
        else:
            self.log_entry("Подкоманды: list | undo | edit", "system")

    def _rebuild_client(self) -> None:
        self.client = LLMClient(self.settings)
        if self.game is not None:
            self.game.client = self.client
        if self.manager is not None:
            self.refresh_header()

    async def _cmd_model(self, args: list[str]) -> None:
        tokens = " ".join(args).split(maxsplit=1)
        sub = tokens[0].lower() if tokens else ""
        rest = tokens[1] if len(tokens) > 1 else ""

        if sub in ("endpoint", "url"):
            if not rest.strip():
                self.log_entry("Формат: /model endpoint https://api.example.com/v1", "system")
                return
            self.settings.llm_base_url = rest.strip()
            self._rebuild_client()
            self.log_entry(f"\U0001f310 Эндпоинт: {self.settings.effective_base_url}", "system")
            return

        if sub in ("key", "api"):
            self.settings.llm_api_key = rest.strip()
            self._rebuild_client()
            self.log_entry("\U0001f511 Ключ обновлён.", "system")
            return

        if not sub:
            self.log_entry(f"\U0001f310 Эндпоинт: {self.settings.effective_base_url}", "system")
            current = self.manager.state.dm_settings.model or self.settings.effective_model
            self.log_entry(f"\U0001f916 Текущая модель: {current}", "system")
            models = await self.client.list_models()
            self.log_entry(
                "Доступные: " + (", ".join(models) if models else "(не получены — оффлайн или нет /models)"),
                "system",
            )
            self.log_entry("Сменить: /model <имя> · /model endpoint <url> · /model key <key>", "system")
            return

        self.settings.llm_model = sub
        self.manager.state.dm_settings.model = sub
        self._rebuild_client()
        self.log_entry(f"\u2705 Модель: {sub}", "system")

    async def _cmd_fudge(self, args: list[str]) -> None:
        if args and args[0] in ("on", "off"):
            self.manager.state.dm_settings.fudge_dice = args[0] == "on"
        state = "вкл" if self.manager.state.dm_settings.fudge_dice else "выкл"
        self.log_entry(f"Подкрутка кубов: {state} (все подкрутки логируются).", "system")

    async def _cmd_export(self, args: list[str]) -> None:
        markdown = loop_mod.export_canon_markdown(self.manager, self.catalogs)
        target = self.manager.slot_dir / "chronicle.md"
        target.write_text(markdown, encoding="utf-8")
        self.log_entry(f"Хроника выгружена: {target}", "system")

    async def _cmd_log(self, args: list[str]) -> None:
        target = self.manager.slot_dir / "narrative.txt"
        target.write_text("\n".join(self.query_one(NarrativeLog).lines) + "\n", encoding="utf-8")
        self.log_entry(f"История выгружена: {target}", "system")

    async def _cmd_exit(self, args: list[str]) -> None:
        self.log_entry("Выход.", "system")
        self.exit()

    # -- rendering -----------------------------------------------------------

    def log_entry(self, text: str, kind: str = "gm", *, style: str | None = None) -> None:
        self.query_one(NarrativeLog).add(text, kind, style=style)

    def refresh_header(self, *, saved: bool = False) -> None:
        state = self.manager.state
        location = self.catalogs.location(state.world.current_location)
        lore_name = (state.flags.get("world_lore") or {}).get("name")
        world_id = state.flags.get("world_id", "")
        world = lore_name or (DEFAULT_WORLD.name if world_id == DEFAULT_WORLD_ID else world_id)
        self.query_one(HeaderBar).update_header(
            model=state.dm_settings.model or self.settings.effective_model,
            offline=self.client.offline,
            saved=saved,
            busy=self._busy,
            world=world,
            location=location.name,
            time_line=tm.describe(state),
        )

    def refresh_panels(self) -> None:
        assert self.manager is not None
        state = self.manager.state
        self.refresh_header()
        self.query_one(CharacterCard).update_from(state, self.catalogs)
        self.query_one(PartyPanel).update_from(state, self.catalogs)
        self.query_one(MapPanel).update_from(state, self.catalogs, reveal=self._map_reveal)
        self.query_one(InventoryTab).update_from(state, self.catalogs)
        self.query_one(QuestTab).update_from(state, self.catalogs)

        if self._dialogue_npc and self._dialogue_npc in state.npcs:
            self.query_one(DialoguePanel).update_from(
                state.npcs[self._dialogue_npc], self.catalogs.npcs.get(self._dialogue_npc)
            )
        else:
            self.query_one(DialoguePanel).update_from(None, None)
