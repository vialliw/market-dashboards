# Project Rules — market-dashboards (Multi-Agent Blackboard Protocol)

## Blackboard protocol (MANDATORY for all agents)

1. MANDATORY FIRST STEP: Read `devin_blackboard/STATE.md` and `devin_blackboard/EVENTS.log`.
2. MANDATORY LAST STEP: Append your action to `devin_blackboard/EVENTS.log` and update `devin_blackboard/STATE.md`.
3. You may ONLY edit files listed in your task's output_files (plus the blackboard files themselves).
4. Do not chat with other agents. Communicate only via file commits and EVENTS.log.
5. Never commit secrets.
6. Do not redefine requirements without Orchestrator approval. If a task seems wrong, file a blocker in STATE.md and stop.

## Orchestrator role (main Devin session)

The main session is the Orchestrator (A). It is NOT a coding agent. It spawns agents B/C/D via `run_subagent` (`subagent_general`), registers tasks in TASKS.json, reviews evidence, deploys, and closes cycles. It may edit only: `devin_blackboard/` files, `AGENTS.md`, `README.md`, and `scripts/poll_tasks.py`. It must NOT edit source or test files. If a subagent cannot be spawned, STOP, log the blocker, and ask the user — unless the user explicitly says "orchestrator may code".

## HARD CONSTRAINT — no live market data in tests (MANDATORY)

yfinance returns stale/unreliable data outside US trading hours (Mon–Fri 09:30–16:00 ET). This project fixes bugs during off-hours, so:

- NO agent may call `yf.download`, `yf.Ticker`, or any network fetch as part of implementation or verification.
- All tests MUST run offline using `tests/fixtures/` data (e.g. `tests/fixtures/TSM_ai_packet.json`).
- `python GexDashboard.py` / `python VolumeProfileVisualizer.py` full-script runs are NOT valid verification — do not execute them.

## Conventions

- Python 3.10+. Source files live at repo root (`GexDashboard.py`, `VolumeProfileVisualizer.py`). Tests in `tests/`. Run tests with `python -m pytest tests/ -q`.
- Task lifecycle: `python scripts/poll_tasks.py --claim <id> <role>` → edit output_files only → `--verify <id>` → `--done <id> <role> --evidence "..."` → append EVENTS.log + update STATE.md.
- Keep changes minimal and consistent with existing file style.
- Completion requires evidence against the task's acceptance_criteria, verified by an agent that did not write the code.

## Completion notification (Orchestrator)

    powershell -NoProfile -File "C:\Users\user\.devin\scripts\notify_done.ps1" -Message "<what finished>"
