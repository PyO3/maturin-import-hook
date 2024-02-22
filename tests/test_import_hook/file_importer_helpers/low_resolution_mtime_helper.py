# ruff: noqa: E402
import logging
from pathlib import Path

logging.basicConfig(format="%(name)s [%(levelname)s] %(message)s", level=logging.DEBUG)

import maturin_import_hook

maturin_import_hook.reset_logger()
maturin_import_hook.install()

import my_script  # type: ignore[missing-import]

Path("extension_path.txt").write_text(my_script.__file__)

print(f"get_num = {my_script.get_num()}")

print("SUCCESS")
