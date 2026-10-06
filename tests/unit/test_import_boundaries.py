"""Import-boundary test for the `media_domain` package.

Rule 5' says dependencies point downward only:

    handlers  ->  services  ->  adapters

and it says this test is *grown slice by slice* — as each slice lands, not
written up front. So this asserts only the boundaries that exist today. There
is deliberately no `handlers/` directory yet (rule 2' forbids empty packages),
and no assertion about one; the handler rules appear in the commit that adds
the first endpoint.

Static analysis via AST rather than grep: it resolves what a name actually is,
so a lazy `import flask` inside a function is caught just like a top-level one.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[2] / "media_domain"
LAYERS = ("handlers", "services", "adapters")


def layer_of(path: Path) -> str | None:
    rel = path.relative_to(PACKAGE)
    return rel.parts[0] if len(rel.parts) > 1 else None


def imported_modules(path: Path) -> set[str]:
    """Every module name imported anywhere in the file, at any nesting depth."""
    tree = ast.parse(path.read_text(), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import inside the package
                base = "." * node.level + (node.module or "")
                found.add(base)
                found.update(f"{base}.{a.name}" for a in node.names)
            elif node.module:
                found.add(node.module)
                found.update(f"{node.module}.{a.name}" for a in node.names)
    return found


def python_files() -> list[Path]:
    return sorted(p for p in PACKAGE.rglob("*.py"))


def test_the_package_exists_and_has_the_layers_this_test_knows_about():
    assert PACKAGE.is_dir(), f"missing {PACKAGE}"
    present = {layer_of(p) for p in python_files()} - {None}
    assert present <= set(LAYERS), f"unknown layer(s) {present - set(LAYERS)}"
    # services and adapters exist as of the URL-intake slice.
    assert {"services", "adapters"} <= present


@pytest.mark.parametrize("path", python_files(), ids=lambda p: p.name)
def test_nothing_in_the_package_imports_app_or_a_foreign_domain(path: Path):
    """The package must be reusable by the next slice without dragging app.py
    back in, and must not reach sideways into rooms/sockets/db."""
    for name in imported_modules(path):
        root = name.split(".")[0]
        assert root not in {"app"}, f"{path.name} imports the app monolith"
        assert root not in {"rooms", "socket_events", "db"}, (
            f"{path.name} reaches sideways into {root}"
        )


@pytest.mark.parametrize(
    "path", [p for p in python_files() if layer_of(p) == "services"],
    ids=lambda p: p.name,
)
def test_services_never_import_the_web_frameworks(path: Path):
    for name in imported_modules(path):
        root = name.split(".")[0]
        assert root not in {"flask", "socketio"}, (
            f"services/{path.name} imports {root}; services must stay framework-free"
        )


@pytest.mark.parametrize(
    "path", [p for p in python_files() if layer_of(p) == "adapters"],
    ids=lambda p: p.name,
)
def test_adapters_never_import_upward(path: Path):
    """adapters/ is the bottom layer: it may not know that services exist."""
    for name in imported_modules(path):
        assert "services" not in name, (
            f"adapters/{path.name} imports upward into {name}"
        )
        assert "handlers" not in name, (
            f"adapters/{path.name} imports upward into {name}"
        )


@pytest.mark.parametrize(
    "path", [p for p in python_files() if layer_of(p) == "services"],
    ids=lambda p: p.name,
)
def test_services_do_not_import_handlers_upward(path: Path):
    for name in imported_modules(path):
        assert "handlers" not in name, f"services/{path.name} imports upward into {name}"


def test_services_may_depend_on_adapters_but_not_the_reverse():
    """Pin the direction that *is* legal, so a later edit cannot silently
    invert the layering while still passing the checks above."""
    downward = {
        name for p in python_files() if layer_of(p) == "services"
        for name in imported_modules(p) if "adapters" in name
    }
    upward = {
        name for p in python_files() if layer_of(p) == "adapters"
        for name in imported_modules(p) if "services" in name
    }
    assert not upward, f"adapters must not import services: {sorted(upward)}"
    # url_intake resolves a local mirror, so at least one legal downward edge
    # exists and is exercised.
    assert downward, "expected services -> adapters edges to be present"