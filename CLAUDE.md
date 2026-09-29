# CLAUDE.md

## Project Overview

Command-line firmware upgrades for Angry Miao devices

This project is on GitHub: <https://github.com/acolomba/angrymiao>

## Claude Code

- Prefer using the LSP plugin over textual search.
- When a sandbox operation fails, stop to ask the user. Avoid disabling the sandbox.
- Create plans under the `docs/plans/` directory.

## Development Setup

The project uses `pyproject.toml` for dependency management. Development dependencies are defined as optional dependencies.

### Setup

Create a virtual environment and install development dependencies:

```bash
# create virtual environment
python3 -m venv venv

# activate it
source venv/bin/activate

# install package in editable mode with dev dependencies
pip install -e ".[dev]"

# install pre-commit hooks
pre-commit install

# install commit-msg hook for gitlint
pre-commit install --hook-type commit-msg
```

Pre-commit hooks will automatically run on `git commit` to check code quality, format code, and scan for secrets.

## Guidelines

### Comments

Python docstrings and inline code comments in Python, YAML, shell, etc. are lowercase. The word "TODO" remains all-caps. Entities such as file names etc. preserve their casing.

Comments must be in the third-person, e.g. "installs", not "install", because they are descriptive. Avoid the imperative.

Keep comments concise, and non-obvious. Avoid documenting what everybody is expected to know.

### Python

Prefer Python idiomatic ("pythonic") style.

Always use type annotations.

### Code Formatting

Code formatting is handled automatically by pre-commit hooks (Black for Python, yamlfmt for YAML).

### Git

- Git commit message titles must be at least 5 characters and no more than 72 characters. Body lines must be no more than 80 characters.
- You can expect pre-commit hooks to fail when attempting to commit. Fix the errors.
- NEVER use `--no-verify` to skip the hooks.

## Testing

### Test Structure

- `test/`: Pytest-based unit tests
- `features/`: Behave-based BDD integration tests

### Running Tests

```bash
# unit tests
pytest test/ -v

# integration tests
behave
```
