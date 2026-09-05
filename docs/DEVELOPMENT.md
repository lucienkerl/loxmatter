# Development

[Back to the README](../README.md)

## Running the test suite

```bash
uv sync
uv run pytest
```

The test suite runs without hardware and without network access.

## Checks that CI runs

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -v
```
