# reparto-docente-m8

## Authority

Read the workspace root `AGENTS.md` first. This repo follows the workspace
Python/service policy; use workspace `.Codex/` plus this repo's `AGENTS.md`.

## Windows Python Environment

On Windows, always run Python tooling through the `fa_auth_m8` conda
environment. Activate it with:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
. C:\Users\mexse\anaconda3\shell\condabin\conda-hook.ps1
conda activate fa_auth_m8
```

Then run repo commands in that activated shell. Do not use global Python,
Windows Store Python, or the workspace POSIX `.shared-venv` from PowerShell.

## Commands

- `ruff format .`
- `ruff check .`
- `mypy . --ignore-missing-imports`
- `pytest --cov --cov-fail-under=100`
- `bandit -r . --severity-level medium`
