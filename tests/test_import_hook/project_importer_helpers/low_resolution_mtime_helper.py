# ruff: noqa: E402
import logging
from pathlib import Path

logging.basicConfig(format="%(name)s [%(levelname)s] %(message)s", level=logging.DEBUG)

import maturin_import_hook

maturin_import_hook.reset_logger()
maturin_import_hook.install()

import my_project  # type: ignore[missing-import]
import my_project.my_project  # type: ignore[missing-import]

Path("extension_path.txt").write_text(my_project.my_project.__file__)
Path("package_path.txt").write_text(str(Path(my_project.__file__).parent))

print(f"get_num = {my_project.get_num()}")

print("SUCCESS")
