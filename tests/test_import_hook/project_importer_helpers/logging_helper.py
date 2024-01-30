import os
import sys

import maturin_import_hook

if False:  # enable for debugging but it will cause the tests to fail since they are expecting default logs
    import logging

    logging.basicConfig(format="%(name)s [%(levelname)s] %(message)s", level=logging.DEBUG)
    maturin_import_hook.reset_logger()

if len(sys.argv) > 1:
    arg = sys.argv[1]
    if arg == "RESET_LOGGER":
        maturin_import_hook.reset_logger()
    elif arg == "CLEAR_PATH":
        os.environ["PATH"] = ""
    else:
        raise ValueError(arg)

maturin_import_hook.install()

try:
    import test_project  # type: ignore[missing-import]
except ImportError as e:
    # catch instead of printing the traceback since that may depend on the interpreter
    print(f"caught ImportError: {e}")
else:
    print("value", test_project.value)
    print("SUCCESS")
