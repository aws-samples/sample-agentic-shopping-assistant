import ast
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import aria_catalog
import handler
from aria_catalog.contract import product_v1
from aria_catalog.definition import example_products, load_definition, to_card

ACTOR = "a" * 64
OTHER = "b" * 64
# The example catalog's cards by ID: what every saved item must be.
CARDS = {card["id"]: card for card in (to_card(load_definition(), product) for product in example_products())}


def context(tool="saved-items___get_saved_items", headers=None):
    custom = {"bedrockAgentCoreToolName": tool}
    if headers is not None:
        custom["bedrockAgentCorePropagatedHeaders"] = headers
    return SimpleNamespace(client_context=SimpleNamespace(custom=custom))


def test_returns_product_v1_cards_from_the_example_catalog():
    result = handler.lambda_handler({}, context(headers={"x-aria-actor-id": ACTOR}))
    assert result["schemaVersion"] == "product.v1"
    assert result["count"] == len(result["products"]) == handler.DEFAULT_LIMIT
    for item in result["products"]:
        assert item == CARDS[item["id"]] and product_v1(item) == item


def test_the_sample_draws_on_the_whole_example_catalog():
    assert len(CARDS) == len(example_products()) == 150
    assert [card["id"] for card in handler._catalog()] == list(CARDS)


def test_each_shopper_gets_a_stable_sample():
    first = handler.lambda_handler({}, context(headers={"x-aria-actor-id": ACTOR}))
    again = handler.lambda_handler({}, context(headers={"X-Aria-Actor-Id": ACTOR}))
    assert first == again
    assert [p["id"] for p in first["products"]] == ["nordly-126", "nordly-018", "nordly-023", "nordly-106", "nordly-064"]


def test_shoppers_get_different_samples():
    mine = handler.saved_items(ACTOR, handler.MAX_LIMIT)
    theirs = handler.saved_items(OTHER, handler.MAX_LIMIT)
    assert [p["id"] for p in mine] != [p["id"] for p in theirs]


def test_how_many_items_a_shopper_has_saved_depends_on_the_actor():
    # 1 + the actor's first byte modulo the catalog size, capped by the limit.
    assert len(handler.saved_items("01" + "c" * 62, handler.MAX_LIMIT)) == 2
    assert len(handler.saved_items("00" + "c" * 62, handler.MAX_LIMIT)) == 1


CATALOG_ROOT = Path(aria_catalog.__file__).parents[1]


def load_time_imports(tree: ast.Module) -> list[str]:
    """The modules a module imports as it loads: import statements anywhere but inside a function.

    `from package import name` also names `package.name`, in case it's a submodule.
    """
    names, nodes = [], list(tree.body)
    while nodes:
        node = nodes.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0 and node.module, "relative imports aren't followed"
            names += [node.module, *(f"{node.module}.{alias.name}" for alias in node.names)]
        nodes.extend(ast.iter_child_nodes(node))
    return names


def loaded_packages(entry: Path) -> set[str]:
    """The top-level packages importing `entry` loads, following aria_catalog's own modules as Python
    does: importing `aria_catalog.x` runs `aria_catalog/__init__.py`, then `x`."""
    outside, seen, files = set(), set(), [entry]
    while files:
        path = files.pop()
        if path in seen:
            continue
        seen.add(path)
        for name in load_time_imports(ast.parse(path.read_text(encoding="utf-8"))):
            parts = name.split(".")
            if parts[0] != "aria_catalog":
                outside.add(parts[0])
                continue
            for depth in range(1, len(parts) + 1):
                module = CATALOG_ROOT.joinpath(*parts[:depth])
                for candidate in (module / "__init__.py", module.with_suffix(".py")):
                    if candidate.is_file():
                        files.append(candidate)
    return outside


def test_the_handler_needs_only_the_standard_library_and_aria_catalog():
    # The Lambda bundles the handler and aria_catalog alone, with no other dependency. The imports are
    # read from the source, following aria_catalog as it loads, since this test process has botocore
    # loaded already.
    outside = {name for name in loaded_packages(Path(handler.__file__)) if not name.startswith("_")}
    assert outside <= set(sys.stdlib_module_names), outside - set(sys.stdlib_module_names)


def test_the_import_reader_catches_a_dependency(tmp_path):
    # aria_catalog's providers import boto3 when they load; the handler's own imports never reach them.
    providers = CATALOG_ROOT / "aria_catalog" / "providers.py"
    assert "boto3" in loaded_packages(providers)
    module = tmp_path / "module.py"
    module.write_text("import json\nfrom aria_catalog import errors\n\ndef later():\n    import requests\n", encoding="utf-8")
    # aria_catalog's package init runs too, with its standard-library imports; the function's import doesn't.
    loaded = loaded_packages(module)
    assert "json" in loaded and "tomllib" in loaded and "requests" not in loaded
    assert loaded <= set(sys.stdlib_module_names)
    module.write_text("try:\n    import botocore\nexcept ImportError:\n    botocore = None\n", encoding="utf-8")
    assert loaded_packages(module) == {"botocore"}


@pytest.mark.parametrize("headers", [None, {}, {"x-aria-actor-id": ""}, {"x-aria-actor-id": "alice"}, {"x-shopper-id": ACTOR}])
def test_refuses_without_a_valid_actor(headers):
    with pytest.raises(PermissionError):
        handler.lambda_handler({}, context(headers=headers))


def test_limit_is_respected_and_capped():
    assert handler.lambda_handler({"limit": 1}, context(headers={"x-aria-actor-id": OTHER}))["count"] == 1
    assert len(handler.saved_items(OTHER, 500)) <= handler.MAX_LIMIT


def test_rejects_unknown_tools_and_arguments():
    with pytest.raises(ValueError, match="Unknown"):
        handler.lambda_handler({}, context(tool="saved-items___delete_all", headers={"x-aria-actor-id": ACTOR}))
    with pytest.raises(ValueError, match="Unsupported"):
        handler.lambda_handler({"actor": OTHER}, context(headers={"x-aria-actor-id": ACTOR}))


@pytest.mark.parametrize("value,logged", [("6740a1b24bf92f3577b34da6a3ce929d", "trace=6740a1b24bf92f3577b34da6a3ce929d"),
                                          ("forged\nline", "trace=-"), (None, "trace=-")])
def test_logs_the_agents_trace_id_for_correlation(caplog, value, logged):
    headers = {"x-aria-actor-id": ACTOR} | ({} if value is None else {"X-Correlation-Id": value})
    with caplog.at_level(logging.INFO):
        handler.lambda_handler({}, context(headers=headers))
    assert any(logged in record.getMessage() for record in caplog.records)
    assert not any("forged" in record.getMessage() for record in caplog.records)
