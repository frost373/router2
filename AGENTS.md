# Repository Guidelines

## Project Structure & Module Organization
- `scripts/` contains the data-generation pipeline and utility entry points, including `generate_training_data.py`, `validate_commands.py`, and `embedding_client.py`.
- `commands/` stores per-game command registries such as `commands/mmorpg.json`.
- `tools/dashboard/` contains the FastAPI dashboard backend (`server.py`) and static frontend assets in `tools/dashboard/static/`.
- `doc/` holds design notes and prompt references. Runtime artifacts are written to `output/` and `logs/`; keep both untracked.

## Build, Test, and Development Commands
- `python scripts/validate_commands.py commands/mmorpg.json`: validate a command registry before editing aliases or slots.
- `python scripts/generate_training_data.py --game mmorpg`: run the full training-data pipeline.
- `python scripts/generate_global_negatives.py --game mmorpg`: regenerate only global negative samples.
- `python -m pip install -r tools/dashboard/requirements.txt`: install dashboard dependencies.
- `python tools/dashboard/server.py`: start the dashboard at `http://localhost:8765`.
- `python test_llm_stream.py`: manual smoke test for streaming LLM calls; requires a valid `LLM.txt`.

## Coding Style & Naming Conventions
- Use 4-space indentation, UTF-8 text, and `snake_case` for Python functions, variables, and files.
- Group imports as standard library, third-party, then local modules.
- Preserve existing type hints when touching typed functions.
- In `commands/*.json`, keep `command_id` in uppercase snake case, for example `ATTACK_TARGET`.
- Only use `use` and `target` slot names, and keep alias placeholders aligned with defined slots, for example `{target}` or `{use}`.

## Testing Guidelines
- No formal `pytest` suite or coverage gate is checked in. Use the narrowest script-based check that covers your change.
- For registry edits, run `python scripts/validate_commands.py`.
- For pipeline changes, prefer a single-command smoke test such as `python scripts/generate_training_data.py --game mmorpg --command_id CAST_ON_TARGET`.
- For dashboard changes, start `server.py` and verify the affected page and API route manually.

## Commit & Pull Request Guidelines
- Follow the commit pattern already in history: `feat: ...`, `fix: ...`, `docs: ...`.
- Keep commits focused and use imperative subjects.
- PRs should summarize behavior changes, list the commands you ran, note any `LLM.txt` or model assumptions, and include screenshots for dashboard UI changes.

## Security & Configuration Tips
- `LLM.txt` stores API endpoints and keys. Do not commit real credentials.
- Review generated samples before sharing them externally; `output/` and `logs/` may contain sensitive prompts or responses.
