"""loadpy — import a Python file whose name is not a legal module name.

Most of this repo's operational scripts are hyphenated (ops/vault-drift-watch.py,
ops/store-markup-scan.py), which `import` cannot spell. Seven files had each
grown their own copy of the same importlib incantation:

    spec = importlib.util.spec_from_file_location(name, path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

Three lines, three latent faults, repeated seven times. spec_from_file_location
returns None for a path that does not exist or is not importable, and spec.loader
is itself optional — so a typo'd or moved path did not fail with "no such file",
it failed with `AttributeError: 'NoneType' object has no attribute 'exec_module'`
somewhere inside a selftest, at 2am, in a log nobody reads until morning. That is
also what the type-check tripwire was reporting: 21 of its 47 errors on
2026-08-14 were those three lines in seven files.

One helper, one error message that names the file it could not load.
"""
from __future__ import annotations

import importlib.util
import sys
from types import ModuleType


def load_module_from_path(name: str, path: str) -> ModuleType:
    """Load `path` as a module called `name`. Raises ImportError naming the path
    when it cannot be loaded, rather than an AttributeError on None."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {name} from {path} — no importable module there")
    module = importlib.util.module_from_spec(spec)
    # Register before executing: dataclasses and typing resolve the class's
    # module through sys.modules while the module body runs.
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module
