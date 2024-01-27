import sys

import maturin_import_hook

if False:  # enable for debugging but it will cause the tests to fail since they are expecting default logs
    import logging

    logging.basicConfig(format="%(name)s [%(levelname)s] %(message)s", level=logging.DEBUG)
    maturin_import_hook.reset_logger()

if len(sys.argv) > 1 and sys.argv[1] == "RESET_LOGGER":
    maturin_import_hook.reset_logger()

maturin_import_hook.install()

try:
    import my_script  # type: ignore[missing-import]
except ImportError as e:
    # catch instead of printing the traceback since that may depend on the interpreter
    print(f"caught ImportError: {e}")
else:
    print("get_num", my_script.get_num())
    print("SUCCESS")
