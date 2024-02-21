import importlib

import maturin_import_hook
import test_project  # type: ignore[missing-import]

maturin_import_hook.install()

print("reloading")
importlib.reload(test_project)

print("reloading again")
importlib.reload(test_project)

print("value", test_project.value)
print("SUCCESS")
