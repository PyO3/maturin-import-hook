# ruff: noqa: INP001
import importlib
import importlib.machinery
import logging
import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from io import StringIO
from pathlib import Path
from textwrap import dedent

script_dir = Path(__file__).parent.resolve()
logging.basicConfig(format="%(name)s [%(levelname)s] %(message)s", level=logging.DEBUG)


def _build_module() -> None:
    paths = sysconfig.get_paths()
    module_name = f"c_module{importlib.machinery.EXTENSION_SUFFIXES[0]}"
    i = sys.version_info
    lib_name = f"python{i.major}.{i.minor}"
    cmd = [
        "gcc",
        "-shared",
        "-fPIC",
        "-o",
        module_name,
        "-I",
        paths["include"],
        "-L",
        paths["stdlib"],
        "c_module.c",
        f"-l{lib_name}",
    ]
    print("building module")
    print(subprocess.list2cmdline(cmd))
    subprocess.check_call(cmd)


def main() -> None:
    captured_log = StringIO()
    handler = logging.StreamHandler(captured_log)
    logging.getLogger().addHandler(handler)

    tmp_path = Path(tempfile.mkdtemp(prefix="extension_module_test"))
    c_module_path = tmp_path / "c_module.c"
    shutil.copy(script_dir / "c_module.c", c_module_path)
    os.chdir(tmp_path)
    sys.path.insert(0, str(tmp_path))
    _build_module()

    logging.info("initial import start")
    import c_module  # type: ignore[missing-import]
    from c_module import get_num  # type: ignore[missing-import]

    logging.info("initial import end")

    c_module_path.write_text(c_module_path.read_text().replace("int num = 10;", "int num = 20;"))

    assert c_module.get_num() == 10
    assert get_num() == 10
    assert c_module.data["foo"] == 123
    assert c_module.data_init_once["foo"] == 123

    c_module.data["foo"] = 100
    c_module.data_init_once["foo"] = 200

    logging.info("reload start")
    _build_module()
    importlib.reload(c_module)
    logging.info("reload end")

    # as expected (see docs/reloading.md) reloading an extension module does not load new functionality
    assert c_module.get_num() == 10
    assert get_num() == 10
    # global variables of extension modules are not reset when reloaded
    # (because the module init function is not called)
    assert c_module.data["foo"] == 100
    assert c_module.data_init_once["foo"] == 200

    # the extension module is not re-initialised when reload is called
    logs = captured_log.getvalue()
    assert logs == dedent("""\
    initial import start
    init c module
    initial import end
    reload start
    reload end
    """)


if __name__ == "__main__":
    main()
