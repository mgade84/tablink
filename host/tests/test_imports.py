import importlib
import pkgutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tablink  # noqa: E402


class ImportTest(unittest.TestCase):
    """Catches syntax/import errors in modules the other tests don't touch."""

    def test_all_modules_import(self):
        for mod in pkgutil.iter_modules(tablink.__path__):
            with self.subTest(module=mod.name):
                importlib.import_module(f"tablink.{mod.name}")


if __name__ == "__main__":
    unittest.main()
