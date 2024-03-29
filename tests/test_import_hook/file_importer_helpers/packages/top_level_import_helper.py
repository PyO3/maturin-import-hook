# ruff: noqa: E402
import logging

logging.basicConfig(format="%(name)s [%(levelname)s] %(message)s", level=logging.DEBUG)

import maturin_import_hook

maturin_import_hook.reset_logger()
maturin_import_hook.install()

import my_py_module

assert my_py_module.do_something_py(1, 2) == 3

import my_rust_module

assert my_rust_module.do_something(1, 2) == 3

import my_rust_module

assert my_rust_module.do_something(1, 2) == 3

print("SUCCESS")
