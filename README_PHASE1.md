# Jarvis Edge AI — Phase 1 Project Scaffold

This scaffold adds PostgreSQL storage without replacing the existing camera,
detector, event bus, memory service, or identity history code.

## Install

From `~/jarvis-edge-ai`:

```bash
unzip ~/Downloads/jarvis_project_scaffold.zip -d /tmp/jarvis_scaffold
cp -R /tmp/jarvis_scaffold/jarvis_project_scaffold/. .
git status --short
```

Load the database URL and create the schema:

```bash
set -a
source .env.jarvis
set +a
python3 scripts/create_schema.py
python3 scripts/check_database.py
```

Run tests:

```bash
PYTHONPATH="$PWD/src:$PYTHONPATH" python3 -m unittest discover -s tests -p "test_storage_*.py" -v
```
