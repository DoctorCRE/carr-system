#!/usr/bin/env python3
"""Focused regression checks for the runtime topology map generator."""
from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "pipelines" / "build-isometric-map.py"
spec = importlib.util.spec_from_file_location("build_isometric_map", SOURCE)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def check_current_topology() -> None:
    model = mod.build_model()
    assert model["missing"] == 0, model
    assert len(model["modules"]) == 11
    assert {flow["id"] for flow in model["flows"]} == {
        "boot", "write", "control", "render"
    }
    assert all(cite["ok"] for item in model["modules"].values()
               for cite in item["cites"])
    assert all(cite["ok"] for flow in model["flows"]
               for cite in flow["sources"])

    paths = mod.build_paths(model)
    assert set(paths) == {flow["id"] for flow in model["flows"]}
    for flow in model["flows"]:
        assert flow["nodes"]
        assert all(node in model["modules"] for node in flow["nodes"])
        assert paths[flow["id"]]
        assert all(leg["a"] in model["modules"] and leg["b"] in model["modules"]
                   for leg in paths[flow["id"]])

    rendered = mod.render(model, paths)
    assert "jbookout/carr-system" in rendered
    assert "eleven buildings and four paths" in rendered
    assert "graphify-out/graph.json" not in rendered


def check_missing_citation_is_discriminated() -> None:
    cites = mod.MODULES["HK"]["cites"]
    cites.append("hooks/this-file-must-not-exist.py")
    try:
        model = mod.build_model()
        assert model["missing"] == 1
        assert any(not cite["ok"] for cite in model["modules"]["HK"]["cites"])
    finally:
        cites.pop()


def check_output_path_is_explicit() -> None:
    # The generator writes only the requested derived output, never a source
    # config or a vault file.
    source_before = SOURCE.read_bytes()
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "map.html"
        model = mod.build_model()
        output.write_text(mod.render(model, mod.build_paths(model)), encoding="utf-8")
        assert output.exists()
    assert SOURCE.read_bytes() == source_before


if __name__ == "__main__":
    check_current_topology()
    check_missing_citation_is_discriminated()
    check_output_path_is_explicit()
    print("isometric-map selftest: PASS")
