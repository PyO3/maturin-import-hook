# ruff: noqa: E402
import logging

logging.basicConfig(format="%(name)s [%(levelname)s] %(message)s", level=logging.DEBUG)

import maturin_import_hook

maturin_import_hook.reset_logger()
maturin_import_hook.install()

from my_script import get_num  # type: ignore[missing-import]

print(f"get_num = {get_num()}")

try:
    from my_script import get_other_num  # type: ignore[missing-import]
except ImportError:
    print("failed to import get_other_num")
else:
    print(f"get_other_num = {get_other_num()}")

print("SUCCESS")
