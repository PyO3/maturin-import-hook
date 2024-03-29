# ruff: noqa: E402
import logging

logging.basicConfig(format="%(name)s [%(levelname)s] %(message)s", level=logging.DEBUG)
logging.getLogger("maturin.import_hook").setLevel(logging.DEBUG)

import maturin_import_hook

maturin_import_hook.reset_logger()
maturin_import_hook.install()

import packages.subpackage.my_rust_module

assert packages.subpackage.my_rust_module.get_num() == 42

import packages.multiple_import_helper

assert packages.multiple_import_helper.foo() == 142

print("SUCCESS")
