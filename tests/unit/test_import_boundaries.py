"""Import-boundary test for the extracted domain packages.

Rule 5' says dependencies point downward only:

    handlers  ->  services  ->  adapters

and it says this test is *grown slice by slice* -- as each slice lands, not
written up front. So it asserts only the boundaries that exist today:

* ``media_domain`` (URL-intake slice): services + adapters.
* ``playlist_domain`` (item-construction slice): services only.

There is deliberately no ``handlers/`` directory in either, because rule 2'
forbids empty packages; it appears in the commit that adds the first endpoint,
and the handler rules are added there.

Static analysis via AST rather than grep: it resolves what a name actually is,
so a lazy ``import flask`` inside a function is caught just like a top-level one.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LAYERS = ("handlers", "services", "adapters")

#: package name -> the layers that are allowed to exist in it right now.
#: Grown one package at a time, alongside the slice that creates each.
PACKAGES = {
    "media_domain": {"services", "adapters"},
    "playlist_domain": {"services"},
}


def layer_of(path: Path) -> str | None:
    rel = path.relative_to(ROOT / path.relative_to(ROOT).parts[0])
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


@pytest.fixture(params=sorted(PACKAGES))
def package(request) -> Path:
    path = ROOT / request.param
    assert path.is_dir(), f"missing {path}"
    return path


def python_files(package: Path) -> list[Path]:
    return sorted(p for p in package.rglob("*.py"))


def files_in(package: Path, layer: str) -> list[Path]:
    return [p for p in python_files(package) if layer_of(p) == layer]


def test_the_package_has_exactly_the_layers_that_are_allowed(package):
    present = {layer_of(p) for p in python_files(package)} - {None}
    assert present <= set(LAYERS), f"unknown layer(s) {present - set(LAYERS)}"
    assert present == PACKAGES[package.name], (
        f"{package.name}: layers {present} do not match the expected "
        f"{PACKAGES[package.name]}; update this test in the slice that changes them"
    )


@pytest.mark.parametrize("path", [
    p for pkg in sorted(PACKAGES) for p in python_files(ROOT / pkg)
], ids=lambda p: str(p.relative_to(ROOT)))
def test_nothing_in_a_domain_imports_app_or_the_monolith_internals(path: Path):
    """The packages must stay reusable by the next slice without dragging app.py
    back in, and must not reach sideways into rooms/sockets/db."""
    for name in imported_modules(path):
        root = name.split(".")[0]
        assert root != "app", f"{path.name} imports the app monolith"
        assert root not in {"rooms", "socket_events", "db"}, (
            f"{path.name} reaches sideways into {root}"
        )


@pytest.mark.parametrize("layer", ["services"])
def test_services_never_import_the_web_frameworks(package, layer):
    for path in files_in(package, layer):
        for name in imported_modules(path):
            root = name.split(".")[0]
            assert root not in {"flask", "socketio"}, (
                f"{package.name}/{layer}/{path.name} imports {root}; "
                f"services must stay framework-free"
            )


#: Rule 5' as one predicate: a module may depend on its own layer or any layer
#: below it, never on one above. Index is the height, higher is further up.
LAYER_HEIGHT = {"adapters": 0, "services": 1, "handlers": 2}


def test_nothing_imports_a_layer_above_its_own(package):
    for path in python_files(package):
        own = layer_of(path)
        if own is None:
            continue
        for name in imported_modules(path):
            for layer, height in LAYER_HEIGHT.items():
                if layer == own or height <= LAYER_HEIGHT[own]:
                    continue
                assert f".{layer}." not in f".{name}." and name != layer, (
                    f"{package.name}/{own}/{path.name} imports {name}, which is "
                    f"in the {layer} layer above it"
                )


def test_a_downward_edge_exists_wherever_adapters_do(package):
    """media_domain's url_intake resolves a local mirror, so at least one legal
    services -> adapters edge exists and is exercised. playlist_domain has no
    adapters layer yet, so it has no such edge to assert."""
    if "adapters" not in PACKAGES[package.name]:
        pytest.skip(f"{package.name} has no adapters layer yet")
    downward = {
        name for p in files_in(package, "services")
        for name in imported_modules(p) if "adapters" in name
    }
    assert downward, "expected services -> adapters edges to be present"


def test_playlist_services_do_not_depend_on_media_services_for_their_rules():
    """playlist_domain/services/items.py borrows ``url_ext`` from
    media_domain, which is a deliberate cross-domain edge and is fine. What is
    not fine is playlist logic silently moving back into the media package, so
    the direction is pinned: media must not import playlist."""
    for path in python_files(ROOT / "media_domain"):
        for name in imported_modules(path):
            assert not name.startswith("playlist_domain"), (
                f"media_domain/{path.name} imports {name}; dependencies must "
                f"not point sideways between domains"
            )