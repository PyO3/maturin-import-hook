# ruff: noqa: E402
import importlib
import logging
import pickle
import re
import sys
from pathlib import Path

import maturin_import_hook

sys.path.insert(0, str(Path.cwd()))
logging.basicConfig(format="%(name)s [%(levelname)s] %(message)s", level=logging.DEBUG)
maturin_import_hook.reset_logger()

log = logging.getLogger("reload_helper")

rs_path = Path(sys.argv[1])
assert rs_path.exists()
action = sys.argv[2]


def _modify_module_num(num: int | str) -> None:
    source = rs_path.read_text()
    source = re.sub("let num = .*;", f"let num = {num};", source)
    rs_path.write_text(source)


def _modify_module_str(string: str) -> None:
    source = rs_path.read_text()
    source = re.sub("let string = .*;", f'let string = "{string}".to_string();', source)
    rs_path.write_text(source)


def _test_basic_reload() -> None:
    maturin_import_hook.install()

    log.info("initial import start")
    import my_module  # type: ignore[missing-import]
    import my_module as module_reference_1  # type: ignore[missing-import]

    module_reference_2 = my_module
    from my_module import get_num  # type: ignore[missing-import]

    log.info("initial import finish")

    assert my_module.get_num() == 10
    assert module_reference_1.get_num() == 10
    assert module_reference_2.get_num() == 10
    assert get_num() == 10
    int_a = my_module.Integer(123, "a")
    int_b = my_module.Integer(123, "b")
    int_c = my_module.Integer(999, "c")
    assert int_a == int_b
    assert int_a != int_c
    assert type(int_a) is type(int_b)
    assert isinstance(int_b, type(int_a))

    log.info("modifying module")
    _modify_module_num(15)

    assert my_module.get_num() == 10  # change does not take effect until reload

    log.info("reload 1 start")
    importlib.reload(my_module)
    log.info("reload 1 finish")

    assert my_module.get_num() == 15
    assert module_reference_1.get_num() == 15
    assert module_reference_2.get_num() == 15
    assert get_num() == 10
    int_d = my_module.Integer(123, "d")
    int_e = my_module.Integer(123, "e")
    assert int_d != int_a  # compared by identity since different types. Integer.__richcmp__ is never called
    assert int_d == int_e
    assert type(int_a) is not type(int_d)
    assert type(int_a).__qualname__ == "Integer"
    assert type(int_d).__qualname__ == "Integer"
    assert not isinstance(int_d, type(int_a))

    log.info("reload 2 start")
    importlib.reload(my_module)
    log.info("reload 2 finish")

    assert my_module.get_num() == 15
    assert module_reference_1.get_num() == 15
    assert module_reference_2.get_num() == 15
    assert get_num() == 10

    log.info("modifying module")
    _modify_module_num(20)

    log.info("reload 3 start")
    importlib.reload(my_module)
    log.info("reload 3 finish")

    assert my_module.get_num() == 20
    assert module_reference_1.get_num() == 20
    assert module_reference_2.get_num() == 20
    assert get_num() == 10

    log.info("SUCCESS")


def _test_globals() -> None:
    maturin_import_hook.install()

    log.info("initial import start")
    import my_module  # type: ignore[missing-import]

    module_file = my_module.__file__
    log.info("initial import finish")

    log.info("checking extension module")
    assert not hasattr(my_module, "rust_extra_data")
    assert not hasattr(my_module, "__path__")
    assert my_module.data["foo"] == 123
    assert my_module.data_init_once["foo"] == 123
    assert my_module.data_str == "foo"
    assert my_module.get_global_num() == 0

    my_module.rust_extra_data = 12
    my_module.data["foo"] = 101
    my_module.data_init_once["foo"] = 102
    my_module.data_str = "bar"
    my_module.set_global_num(100)

    log.info("reload 1 start")
    importlib.reload(my_module)
    log.info("reload 2 finish")

    log.info("checking extension module")
    assert not hasattr(my_module, "__path__")
    assert my_module.__file__ == module_file
    assert my_module.rust_extra_data == 12  # data not assigned during module initialisation is not overwritten
    # all data assigned during module initialisation is reset as the module is brand new.
    # During re-initialisation the module is blank
    assert my_module.data["foo"] == 123
    assert my_module.data_init_once["foo"] == 123
    assert my_module.data_str == "foo"
    assert my_module.get_global_num() == 0

    my_module.rust_extra_data = 13
    my_module.data["foo"] = 91
    my_module.data_init_once["foo"] = 92
    my_module.data_str = "baz"
    my_module.set_global_num(100)

    log.info("modifying module")
    _modify_module_num(20)

    log.info("reload 2 start")
    importlib.reload(my_module)
    log.info("reload 2 finish")

    log.info("checking extension module")
    assert not hasattr(my_module, "__path__")
    assert my_module.__file__ == module_file
    assert my_module.rust_extra_data == 13  # data not assigned during module initialisation is not overwritten
    assert my_module.data["foo"] == 123
    assert my_module.data_init_once["foo"] == 123
    assert my_module.data_str == "foo"
    assert my_module.get_global_num() == 0

    log.info("SUCCESS")


def _test_other_module() -> None:
    maturin_import_hook.install()

    log.info("initial import start")
    import my_module  # type: ignore[missing-import]
    import other_module  # type: ignore[missing-import]
    from other_module import get_num as get_num_indirect  # type: ignore[missing-import]

    log.info("initial import finish")

    assert my_module.get_num() == 10
    assert get_num_indirect() == 10
    assert other_module.get_num() == 10
    assert other_module.get_twice_num_direct() == 20
    assert other_module.get_twice_num_indirect() == 20

    log.info("modifying module")
    _modify_module_num(15)

    assert my_module.get_num() == 10  # change does not take effect until reload

    log.info("reload 1 start")
    importlib.reload(my_module)
    log.info("reload 1 finish")

    assert my_module.get_num() == 15
    assert get_num_indirect() == 10
    assert other_module.get_num() == 10
    assert other_module.get_twice_num_direct() == 20
    assert (
        other_module.get_twice_num_indirect() == 30
    )  # updates because the module was updated: note this is different to the package reload behaviour

    log.info("reload 2 start")
    importlib.reload(other_module)
    log.info("reload 2 finish")

    assert my_module.get_num() == 15
    assert get_num_indirect() == 10
    assert other_module.get_num() == 15  # updates to use the updated extension module
    assert other_module.get_twice_num_direct() == 30  # updates to use the updated extension module
    assert other_module.get_twice_num_indirect() == 30

    log.info("SUCCESS")


def _test_reload_without_import_hook() -> None:
    log.info("initial import start")
    try:
        import my_module  # type: ignore[missing-import]
    except ModuleNotFoundError:
        log.info("module not found")
    else:
        msg = "expected to not be able to import"
        raise AssertionError(msg)

    log.info("installing import hook")
    maturin_import_hook.install(enable_reloading=False)

    import my_module  # type: ignore[missing-import]
    from my_module import get_num  # type: ignore[missing-import]

    log.info("initial import finish")

    assert my_module.get_num() == 10
    assert get_num() == 10
    assert my_module.data["foo"] == 123
    assert my_module.data_init_once["foo"] == 123

    my_module.data["foo"] = 100
    my_module.data_init_once["foo"] = 200

    log.info("reload module start")
    importlib.reload(my_module)
    log.info("reload module finish")

    # nothing is reset because the module is not re-initialised.
    # This is the same behaviour as extension modules imported directly (see tests/reference/reloading)
    assert my_module.get_num() == 10
    assert get_num() == 10
    assert my_module.data["foo"] == 100
    assert my_module.data_init_once["foo"] == 200

    log.info("modifying module")
    _modify_module_num(30)

    log.info("reload module start")
    importlib.reload(my_module)
    log.info("reload module finish")

    # again, nothing is reloaded
    assert my_module.get_num() == 10
    assert get_num() == 10
    assert my_module.data["foo"] == 100
    assert my_module.data_init_once["foo"] == 200

    log.info("uninstalling import hook")
    maturin_import_hook.uninstall()

    log.info("reload module start")
    try:
        importlib.reload(my_module)
    except ModuleNotFoundError:
        log.info("module not found")
    else:
        msg = "expected to not be able to import"
        raise AssertionError(msg)
    log.info("reload module finish")

    # again, nothing is reloaded
    assert my_module.get_num() == 10
    assert get_num() == 10
    assert my_module.data["foo"] == 100
    assert my_module.data_init_once["foo"] == 200

    log.info("SUCCESS")


def _test_compilation_error() -> None:
    from maturin_import_hook.error import MaturinError

    maturin_import_hook.install()

    log.info("initial import start")
    import my_module  # type: ignore[missing-import]

    log.info("initial import finish")

    assert my_module.get_num() == 10

    log.info("modifying module")
    _modify_module_num("")

    log.info("reload start")
    try:
        importlib.reload(my_module)
    except MaturinError:
        log.info("reload failed")
    else:
        message = "expected compilation failure"
        raise AssertionError(message)
    log.info("reload finish")

    assert my_module.get_num() == 10

    log.info("modifying module")
    _modify_module_num(20)

    log.info("reload start")
    importlib.reload(my_module)
    log.info("reload finish")

    assert my_module.get_num() == 20

    log.info("SUCCESS")


def _test_pickling() -> None:
    maturin_import_hook.install()

    log.info("initial import start")
    import my_module  # type: ignore[missing-import]

    log.info("initial import finish")

    int_a = my_module.PicklableInteger(123, "a")
    int_b = my_module.PicklableInteger(123, "b")
    int_c = my_module.PicklableInteger(999, "c")
    assert int_a == int_b
    assert int_a != int_c
    assert type(int_a) is type(int_b)
    assert isinstance(int_b, type(int_a))
    int_a_data = pickle.dumps(int_a)
    int_a_unpickled_1 = pickle.loads(int_a_data)
    assert int_a == int_a_unpickled_1
    assert type(int_a_unpickled_1) is type(int_a)

    log.info("modifying module")
    _modify_module_num(15)

    log.info("reload start")
    importlib.reload(my_module)
    log.info("reload finish")

    int_d = my_module.PicklableInteger(123, "d")
    int_e = my_module.PicklableInteger(123, "e")
    assert int_d != int_a  # compared by identity since different types. PicklableInteger.__richcmp__ is never called
    assert int_d == int_e
    assert type(int_a) is not type(int_d)
    assert type(int_a).__qualname__ == "PicklableInteger"
    assert type(int_d).__qualname__ == "PicklableInteger"
    assert not isinstance(int_d, type(int_a))
    int_a_unpickled_2 = pickle.loads(int_a_data)
    assert int_d != int_a_unpickled_1
    assert int_d == int_a_unpickled_2
    assert type(int_d) is type(int_a_unpickled_2)

    log.info("SUCCESS")


def _test_submodule() -> None:
    maturin_import_hook.install()

    log.info("initial import start")
    import my_module  # type: ignore[missing-import]

    log.info("initial import finish")

    assert my_module.child.get_str() == "foo"

    log.info("modifying module")
    _modify_module_str("bar")

    log.info("reload start")
    importlib.reload(my_module)
    log.info("reload finish")

    assert my_module.child.get_str() == "bar"

    log.info("reload start")
    try:
        importlib.reload(my_module.child)
    except ImportError as e:
        assert str(e) == "module child not in sys.modules"  # noqa: PT017
        log.info("reload failed")
    else:
        msg = "expected import to fail"
        raise AssertionError(msg)
    log.info("reload finish")

    log.info("SUCCESS")


if action == "_test_basic_reload":
    _test_basic_reload()
elif action == "_test_globals":
    _test_globals()
elif action == "_test_other_module":
    _test_other_module()
elif action == "_test_reload_without_import_hook":
    _test_reload_without_import_hook()
elif action == "_test_compilation_error":
    _test_compilation_error()
elif action == "_test_pickling":
    _test_pickling()
elif action == "_test_submodule":
    _test_submodule()
else:
    raise ValueError(action)
