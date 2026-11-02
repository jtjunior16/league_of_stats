# league_of_stats
League of Legends data analysis for fun and for practice.

## Setup
Run these in PowerShell from the project folder:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

To confirm the venv is active, run:

```powershell
python -c "import sys; print(sys.prefix != sys.base_prefix, sys.prefix)"
```

It should print `True` and the path to `.venv`.
