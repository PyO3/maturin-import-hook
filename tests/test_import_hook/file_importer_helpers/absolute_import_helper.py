# ruff: noqa: E402
import logging

logging.basicConfig(format="%(name)s [%(levelname)s] %(message)s", level=logging.DEBUG)

import maturin_import_hook

maturin_import_hook.reset_logger()
maturin_import_hook.install()

import packages.my_py_module

assert packages.my_py_module.do_something_py(1, 2) == 3

import packages.my_rust_module

assert packages.my_rust_module.do_something(1, 2) == 3

from packages import my_rust_module  # type: ignore[missing-import]

assert my_rust_module.do_something(1, 2) == 3


# modules with the same name do not clash
import packages.subpackage.my_rust_module

assert packages.subpackage.my_rust_module.get_num() == 42

print("SUCCESS")
