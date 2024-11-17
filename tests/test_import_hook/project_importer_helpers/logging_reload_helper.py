import importlib

import test_project  # type: ignore[missing-import]

import maturin_import_hook

maturin_import_hook.install()  # install after importing so that the first reload triggers a build

print("reload start", flush=True)
importlib.reload(test_project)
print("reload finish", flush=True)

print("reload start", flush=True)
importlib.reload(test_project)
print("reload finish", flush=True)

print("value", test_project.value, flush=True)
print("SUCCESS", flush=True)
