---
name: documenting-python
description: Write professional Google-style docstrings for all Python code and loop pydoclint until clean.
---

# Documenting Python

Professional Google docstrings are enforced on ALL `src/knowledge_engine/**/*.py`
(public + private) by pre-commit `pydoclint`. Run until clean.

## Template (no type hints in docstring — signatures carry types)

```python
"""One-line summary."""

def func(arg1, arg2) -> str:
    """One-line summary.

    Longer description if behavior is non-obvious.

    Args:
        arg1: What it is.
        arg2: What it is.

    Returns:
        ResultName: What is returned.

    Raises:
        ValueError: When and why.
    """
```

Classes document attributes:

```python
class Foo:
    """One-line summary.

    Attributes:
        name: What it holds.
    """
```

Rules: every arg in signature appears in `Args` in order; every non-`None`
return has `Returns` with a leading name (`ResultName: ...`); every `raise`
has a matching `Raises` entry; `__init__` args are documented (init docstrings
allowed); keep lines <= 119 cols; never touch `# type: ignore[...]` / `# noqa:`.

## Loop

1. `uvx --from pydoclint==0.11.0 pydoclint --style google --arg-type-hints-in-docstring 0 --skip-checking-raises 0 --skip-checking-short-docstrings 0 --skip-checking-private-functions 0 --allow-init-docstring 1 --check-arg-order 1 --check-return-types 1 --check-class-attributes 1 src/knowledge_engine`
2. Fix the reported batch (docs only, no logic).
3. Repeat until `No violations`.
4. `make lint && make types` — docstring lines must still pass ruff + mypy.
