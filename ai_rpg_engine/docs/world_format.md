# Создание своего мира

Мир — это папка `data/worlds/<id>/` с оверлеями каталогов и файлом `world.json`.
Проще всего создать его командой **`/world new <описание>`** — движок сделает
черновик, а нейросеть структурирует и дополнит. Ниже — ручной формат.

## Флоу создания (команда `/world`)

1. `/world new <свободное описание мира>` — черновик собирается в
   `data/worlds/_draft_<id>/`. Если нейросеть онлайн, она структурирует
   описание, дополняет пробелы и правит ошибки; оффлайн — минимальный скелет.
2. **Правки игрока.** Открой файлы черновика и поправь руками — это финальный
   проход **без проверки нейросети**.
3. По желанию — `/world check`: нейросеть просмотрит файлы и исправит ошибки.
4. `/world accept [id]` — валидация ссылок и перенос в настоящий мир.
   `/world discard` — удалить черновик.

`/world list` — все черновики и миры. Запустить мир — `/setup` (вкладка «Мир»)
или `/world play <id>`.

## Файлы мира

| Файл | Обязателен | Что описывает |
|---|---|---|
| `world.json` | да | мета: имя, тон, эпоха, магия, народы, законы, старт, персонаж, отряд, правила |
| `world_regions.json` | да | карта-сетка и стартовая локация |
| `items_catalog.json` | нет | предметы (оружие/броня/расходники/…) |
| `npcs_catalog.json` | нет | NPC |
| `spells_catalog.json` | нет | заклинания/программы |
| `quests_catalog.json` | нет | задания (завязку лучше не фиксировать) |

Оверлеи **сливаются** с базовыми каталогами `data/`: можно переопределить часть
или добавить своё. Шаблоны-примеры — в `data/templates/`:

- `world_template.json`, `world_regions_template.json`
- `items_template.json`, `npcs_template.json`, `spells_template.json`, `quests_template.json`
- `rules_template.json`

## world.json — поля

| Поле | Смысл |
|---|---|
| `id` | уникальный id (латиница, подчёркивания) |
| `name`, `subtitle`, `emoji` | отображение |
| `tone` | жанр и тон |
| `description` | 2–3 предложения о мире |
| `premise` | завязка без конкретного квеста |
| `era` | эпоха и технологии |
| `magic` | как устроена магия (или что её нет) |
| `peoples` | народы/фракции |
| `laws` | законы мира (уходят в промпт рассказчику) |
| `start` | id стартовой локации (должен быть в `world_regions.json`) |
| `rules` | числовые правила + `toggles` + `capabilities` (см. `rules_template.json`) |
| `player` | стартовый персонаж: `name`,`class`,`stats`,`hp`,`mp`,`ac`,`weapon`,`armor`,`currency` |
| `party` | спутники (список как у player) |
| `starting_items` | id предметов в сумку |

`rules` может задавать `toggles` (напр. `magic_enabled: false` для мира без
магии) и `capabilities` (какие права у рассказчика).

## Локации (сетка)

Мир — сетка клеток. Координаты `coords: {x, y}` задают позицию на доске
(колонки — буквы, строки — цифры). `connections` — соседние id; ходить можно
только по ним. `terrain` влияет на иконку: `village, forest, marsh, bridge,
cave, water, ruin, road, mountain, school, library, garden, tower, city,
market, desert, oasis, camp`.

```jsonc
"loc_start": {
  "id": "loc_start", "name": "…", "description": "…",
  "coords": { "x": 0, "y": 0 },
  "connections": ["loc_neighbor"],
  "danger_level": 0, "encounters": [], "npcs": [], "terrain": "village"
}
```

Чего не хватает — движок подставит (`terrain` → `default`, битые ссылки
отбросит при `/world accept`).
