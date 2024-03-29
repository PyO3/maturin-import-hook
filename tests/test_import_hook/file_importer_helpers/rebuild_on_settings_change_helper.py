# ruff: noqa: E402
import logging
import sys

logging.basicConfig(format="%(name)s [%(levelname)s] %(message)s", level=logging.DEBUG)

import maturin_import_hook
from maturin_import_hook.settings import MaturinSettings

maturin_import_hook.reset_logger()

if len(sys.argv) > 1 and sys.argv[1] == "LARGE_NUMBER":
    print("building with large_number feature enabled")
    settings = MaturinSettings(features=["pyo3/extension-module", "large_number"])
else:
    print("building with default settings")
    settings = MaturinSettings.default()

maturin_import_hook.install(settings=settings)


from PROJECT_NAME import get_num  # type: ignore[missing-import]

print(f"get_num = {get_num()}")
print("SUCCESS")
