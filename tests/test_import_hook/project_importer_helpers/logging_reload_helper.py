import importlib

import maturin_import_hook
import test_project  # type: ignore[missing-import]

maturin_import_hook.install()  # install after importing so that the first reload triggers a build

print("reload start")
importlib.reload(test_project)
print("reload finish")

print("reload start")
importlib.reload(test_project)
print("reload finish")

print("value", test_project.value)
print("SUCCESS")
