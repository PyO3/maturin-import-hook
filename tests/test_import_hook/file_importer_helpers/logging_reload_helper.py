# ruff: noqa: E402
import importlib
from pathlib import Path

import maturin_import_hook

maturin_import_hook.install()

print("initial import start")
import my_script  # type: ignore[missing-import]

print("initial import finish")

script_path = Path("package/my_script.rs").resolve()
assert script_path.exists()
script_path.touch()  # trigger a re-build

print("reload start")
importlib.reload(my_script)
print("reload finish")

print("reload start")
importlib.reload(my_script)
print("reload finish")

print("get_num", my_script.get_num())
print("SUCCESS")
