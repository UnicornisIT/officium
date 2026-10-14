"""Import the extension-less root updater script as a module for unit tests."""

import importlib
import importlib.machinery
import importlib.util
import sys
import types
from pathlib import Path


RUNNER_PATH = Path(__file__).resolve().parents[1] / 'deployment' / 'officium-update-runner'


def load_runner():
    # fcntl, grp and pwd exist only on POSIX; the functions under test do not
    # use them, so Windows developer machines get empty stand-ins for import.
    stand_ins = []
    for name in ('fcntl', 'grp', 'pwd'):
        try:
            importlib.import_module(name)
        except ImportError:
            sys.modules[name] = types.ModuleType(name)
            stand_ins.append(name)
    try:
        loader = importlib.machinery.SourceFileLoader('officium_update_runner', str(RUNNER_PATH))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
    finally:
        for name in stand_ins:
            sys.modules.pop(name, None)
    return module
