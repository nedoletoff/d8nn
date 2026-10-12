# d8nn

**dungeon and neutron network** — детерминированная текстовая RPG-песочница с
ИИ-гейммастером. Механику считает Python, нейросеть — только рассказчик.

Проект и вся документация лежат в [`ai_rpg_engine/`](ai_rpg_engine/README.md).

## Быстрый старт

```bash
cd ai_rpg_engine
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Игра работает без ключа (оффлайн). Чтобы включить нейросеть — скопируй
`.env.example` в `.env` и укажи `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL`
(любой OpenAI-совместимый эндпоинт). Подробнее — в
[`ai_rpg_engine/README.md`](ai_rpg_engine/README.md).

## Что внутри

- Детерминированные броски, бой, время, инвентарь, квесты, репутация, преступления.
- **Неизменяемый канон** (`canon_log.jsonl`) с sha256-цепочкой.
- TUI на Textual (Gruvbox), выделение текста мышью, 6 прототипов мира, свой мир
  и свой персонаж через нейросеть, торговля, автосохранения.
- Тесты: `cd ai_rpg_engine && pytest -q`.

## Лицензия

Как есть, для личного использования.
