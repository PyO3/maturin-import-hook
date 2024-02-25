# ruff: noqa: E402
import importlib
import logging
import pickle
import re
import sys
from pathlib import Path

import maturin_import_hook

logging.basicConfig(format="%(name)s [%(levelname)s] %(message)s", level=logging.DEBUG)
maturin_import_hook.reset_logger()

log = logging.getLogger("reload_helper")

rs_path = Path(sys.argv[1])
assert rs_path.exists()
action = sys.argv[2]


def _modify_project_num(num: int | str) -> None:
    source = rs_path.read_text()
    source = re.sub("let num = .*;", f"let num = {num};", source)
    rs_path.write_text(source)


def _modify_project_str(string: str) -> None:
    source = rs_path.read_text()
    source = re.sub("let string = .*;", f'let string = "{string}".to_string();', source)
    rs_path.write_text(source)


def _test_basic_reload() -> None:
    maturin_import_hook.install()

    log.info("initial import start")
    import my_project  # type: ignore[missing-import]
    import my_project as project_reference_1  # type: ignore[missing-import]

    project_reference_2 = my_project
    import my_project.my_project  # type: ignore[missing-import]
    from my_project import get_num  # type: ignore[missing-import]
    from my_project.my_project import get_num as get_num_direct  # type: ignore[missing-import]

    log.info("initial import finish")

    assert my_project.get_num() == 10
    assert project_reference_1.get_num() == 10
    assert project_reference_2.get_num() == 10
    assert get_num() == 10
    assert get_num_direct() == 10
    assert my_project.my_project.get_num() == 10  # calling the extension module directly
    int_a = my_project.Integer(123, "a")
    int_b = my_project.Integer(123, "b")
    int_c = my_project.Integer(999, "c")
    assert int_a == int_b
    assert int_a != int_c
    assert type(int_a) is type(int_b)
    assert isinstance(int_b, type(int_a))

    log.info("modifying project")
    _modify_project_num(15)

    assert my_project.get_num() == 10  # change does not take effect until reload

    log.info("reload 1 start")
    importlib.reload(my_project)
    log.info("reload 1 finish")

    assert my_project.get_num() == 15  # reloading the package reloads the extension module inside
    assert project_reference_1.get_num() == 15  # reloading updates the module in-place so aliases also update
    assert project_reference_2.get_num() == 15
    # reloading the package also reloads direct references to the extension module
    assert my_project.my_project.get_num() == 15
    assert get_num() == 10  # reloading the module does not affect names imported from the module before reloading
    assert get_num_direct() == 10
    int_d = my_project.Integer(123, "d")
    int_e = my_project.Integer(123, "e")
    assert int_d != int_a  # compared by identity since different types. Integer.__richcmp__ is never called
    assert int_d == int_e
    assert type(int_a) is not type(int_d)
    assert type(int_a).__qualname__ == "Integer"
    assert type(int_d).__qualname__ == "Integer"
    assert not isinstance(int_d, type(int_a))

    log.info("reload 2 start")
    importlib.reload(my_project)
    log.info("reload 2 finish")

    assert my_project.get_num() == 15
    assert project_reference_1.get_num() == 15
    assert project_reference_2.get_num() == 15
    assert my_project.my_project.get_num() == 15
    assert get_num() == 10
    assert get_num_direct() == 10

    log.info("modifying project")
    _modify_project_num(20)

    log.info("reload 3 start")
    importlib.reload(my_project)
    log.info("reload 3 finish")

    assert my_project.get_num() == 20
    assert project_reference_1.get_num() == 20
    assert project_reference_2.get_num() == 20
    assert my_project.my_project.get_num() == 20
    assert get_num() == 10
    assert get_num_direct() == 10

    _modify_project_num(30)

    log.info("reload 4 start")
    importlib.reload(my_project.my_project)  # reloading the extension module directly has no effect
    log.info("reload 4 finish")

    assert my_project.get_num() == 20
    assert project_reference_1.get_num() == 20
    assert project_reference_2.get_num() == 20
    assert my_project.my_project.get_num() == 20
    assert get_num() == 10
    assert get_num_direct() == 10

    log.info("SUCCESS")


def _test_globals() -> None:
    maturin_import_hook.install()

    log.info("initial import start")
    import my_project  # type: ignore[missing-import]
    import my_project.my_project  # type: ignore[missing-import]
    import my_project.other_module  # type: ignore[missing-import]

    root_path = my_project.__path__
    root_file = my_project.__file__
    log.info("initial import finish")

    log.info("checking extension module")
    assert not hasattr(my_project.my_project, "rust_extra_data")
    assert not hasattr(my_project.my_project, "__path__")
    assert my_project.my_project.data["foo"] == 123
    assert my_project.my_project.data_init_once["foo"] == 123
    assert my_project.my_project.data_str == "foo"
    assert my_project.my_project.get_global_num() == 0

    my_project.my_project.rust_extra_data = 12
    my_project.my_project.data["foo"] = 101
    my_project.my_project.data_init_once["foo"] = 102
    my_project.my_project.data_str = "bar"
    my_project.my_project.set_global_num(100)

    log.info("checking root module")
    assert not hasattr(my_project, "python_extra_data")
    assert my_project.data["foo"] == 101  # imported from extension module (modification transfers)
    assert my_project.data_init_once["foo"] == 102  # imported from extension module (modification transfers)
    assert my_project.data_str == "foo"  # imported from extension module (assignment does not transfer)
    assert my_project.get_global_num() == 100

    my_project.python_extra_data = 13
    my_project.data["foo"] = 201
    my_project.data_init_once["foo"] = 202
    my_project.data_str = "xyz"
    my_project.set_global_num(200)

    log.info("checking other_module")
    assert not hasattr(my_project.other_module, "python_extra_data")
    assert not hasattr(my_project.other_module, "__path__")
    assert my_project.other_module.other_data["foo"] == 123
    assert my_project.other_module.other_data_init_once["foo"] == 123
    assert my_project.other_module.other_data_str == "hi"

    my_project.other_module.python_extra_data = 14
    my_project.other_module.other_data["foo"] = 103
    my_project.other_module.other_data_init_once["foo"] = 104
    my_project.other_module.other_data_str = "xyz"

    log.info("reload 1 start")
    importlib.reload(my_project)
    log.info("reload 2 finish")

    log.info("checking extension module")
    assert not hasattr(my_project.my_project, "__path__")
    assert my_project.my_project.rust_extra_data == 12
    assert my_project.my_project.data["foo"] == 201
    assert my_project.my_project.data_init_once["foo"] == 202
    assert my_project.my_project.data_str == "bar"
    assert my_project.my_project.get_global_num() == 200

    my_project.my_project.rust_extra_data = 12
    my_project.my_project.data["foo"] = 91
    my_project.my_project.data_init_once["foo"] = 92
    my_project.my_project.data_str = "baz"
    my_project.my_project.set_global_num(100)

    log.info("checking root module")
    # even if nothing has changed, a new symlink is created. This is simpler than locating the last used symlink
    # if the package has already been reloaded before
    assert my_project.__path__ != root_path
    assert my_project.__file__ != root_file
    root_path_2 = my_project.__path__
    root_file_2 = my_project.__file__
    # module contents are not cleared
    assert my_project.python_extra_data == 13
    assert my_project.data["foo"] == 91
    assert my_project.data_init_once["foo"] == 92
    assert my_project.data_str == "bar"
    assert my_project.my_project.get_global_num() == 100

    log.info("checking other_module")
    assert not hasattr(my_project.other_module, "__path__")
    assert my_project.other_module.python_extra_data == 14
    assert my_project.other_module.other_data["foo"] == 103
    assert my_project.other_module.other_data_init_once["foo"] == 104
    assert my_project.other_module.other_data_str == "xyz"

    log.info("modifying project")
    _modify_project_num(20)

    log.info("reload 2 start")
    importlib.reload(my_project)
    log.info("reload 2 finish")

    log.info("checking extension module")
    assert not hasattr(my_project.my_project, "__path__")
    assert not hasattr(my_project.my_project, "rust_extra_data")
    assert my_project.my_project.data["foo"] == 123
    assert my_project.my_project.data_init_once["foo"] == 123
    assert my_project.my_project.data_str == "foo"
    assert my_project.my_project.get_global_num() == 0

    log.info("checking root module")
    assert my_project.__path__ != root_path
    assert my_project.__path__ != root_path_2
    assert my_project.__file__ != root_file
    assert my_project.__file__ != root_file_2
    assert my_project.python_extra_data == 13
    assert my_project.data["foo"] == 123
    assert my_project.data_init_once["foo"] == 123
    assert my_project.data_str == "foo"
    assert my_project.my_project.get_global_num() == 0

    log.info("checking other_module")
    assert not hasattr(my_project.other_module, "__path__")
    assert my_project.other_module.python_extra_data == 14
    assert my_project.other_module.other_data["foo"] == 103
    assert my_project.other_module.other_data_init_once["foo"] == 104
    assert my_project.other_module.other_data_str == "xyz"

    log.info("SUCCESS")


def _test_other_module() -> None:
    maturin_import_hook.install()

    log.info("initial import start")
    import my_project  # type: ignore[missing-import]
    import my_project.other_module  # type: ignore[missing-import]

    log.info("initial import finish")

    assert my_project.get_num() == 10
    assert my_project.other_module.get_twice_num_direct() == 20
    assert my_project.other_module.get_twice_num_indirect() == 20
    assert my_project.other_module.other_data_str == "hi"
    my_project.other_module.other_data_str = "hi2"
    assert my_project.other_module.other_data_init_once == {"foo": 123}
    my_project.other_module.other_data_init_once = "bar"

    log.info("modifying project")
    _modify_project_num(30)

    assert my_project.get_num() == 10
    assert my_project.other_module.get_twice_num_direct() == 20
    assert my_project.other_module.get_twice_num_indirect() == 20
    assert my_project.other_module.other_data_str == "hi2"
    assert my_project.other_module.other_data_init_once == "bar"

    log.info("reload other_module start")
    importlib.reload(my_project.other_module)
    log.info("reload other_module finish")

    assert my_project.get_num() == 10  # reloading other_module does not trigger rebuild
    assert my_project.other_module.get_twice_num_direct() == 20
    assert my_project.other_module.get_twice_num_indirect() == 20
    assert my_project.other_module.other_data_str == "hi"  # other_module itself is reloaded
    my_project.other_module.other_data_str = "hi3"
    assert my_project.other_module.other_data_init_once == "bar"  # not reset

    log.info("reload package start")
    importlib.reload(my_project)
    log.info("reload package finish")

    assert my_project.get_num() == 30  # top level package and extension module are reloaded
    # other modules that import the extension module do not reload
    assert my_project.other_module.get_twice_num_direct() == 20
    assert my_project.other_module.get_twice_num_indirect() == 20
    assert my_project.other_module.other_data_str == "hi3"
    assert my_project.other_module.other_data_init_once == "bar"  # not reset

    log.info("reload other_module start")
    importlib.reload(my_project.other_module)
    log.info("reload other_module finish")

    assert my_project.get_num() == 30
    # when other modules are reloaded, they import the reloaded extension module
    assert my_project.other_module.get_twice_num_direct() == 60
    assert my_project.other_module.get_twice_num_indirect() == 60
    assert my_project.other_module.other_data_str == "hi"
    assert my_project.other_module.other_data_init_once == "bar"  # not reset

    log.info("SUCCESS")


def _test_reload_without_import_hook() -> None:
    log.info("initial import start")
    import my_project  # type: ignore[missing-import]
    import my_project.my_project  # type: ignore[missing-import]

    log.info("initial import finish")

    assert my_project.get_num() == 10
    assert my_project.my_project.get_num() == 10

    log.info("reload package start")
    importlib.reload(my_project)
    log.info("reload package finish")

    assert my_project.get_num() == 10  # not reloaded
    assert my_project.my_project.get_num() == 10

    log.info("installing import hook")
    maturin_import_hook.install(enable_reloading=False)

    log.info("modifying project")
    _modify_project_num(30)

    log.info("reload package start")
    importlib.reload(my_project)
    log.info("reload package finish")

    assert my_project.get_num() == 10  # not reloaded
    assert my_project.my_project.get_num() == 10

    log.info("reload extension module start")
    importlib.reload(my_project.my_project)
    log.info("reload extension module finish")

    assert my_project.get_num() == 10  # not reloaded
    assert my_project.my_project.get_num() == 10
    assert my_project.my_project.get_num() == 10

    log.info("uninstalling import hook")
    maturin_import_hook.uninstall()

    log.info("reload package start")
    importlib.reload(my_project)
    log.info("reload package finish")

    assert my_project.get_num() == 10  # not reloaded
    assert my_project.my_project.get_num() == 10

    log.info("SUCCESS")


def _test_install_after_import() -> None:
    log.info("initial import start")
    import my_project  # type: ignore[missing-import]
    from my_project import get_num  # type: ignore[missing-import]

    log.info("initial import finish")

    log.info("installing import hook")
    maturin_import_hook.install()

    my_project.extra_data = 12

    assert my_project.get_num() == 10
    assert get_num() == 10
    assert my_project.data["foo"] == 123
    my_project.data["foo"] = 100

    log.info("modifying project")
    _modify_project_num(15)

    log.info("reload start")
    importlib.reload(my_project)
    log.info("reload finish")

    assert my_project.get_num() == 15
    assert get_num() == 10
    assert my_project.data["foo"] == 123
    assert my_project.extra_data == 12

    log.info("SUCCESS")


def _test_compilation_error() -> None:
    from maturin_import_hook.error import MaturinError

    maturin_import_hook.install()

    log.info("initial import start")
    import my_project  # type: ignore[missing-import]

    log.info("initial import finish")

    assert my_project.get_num() == 10

    log.info("modifying project")
    _modify_project_num("")

    log.info("reload start")
    try:
        importlib.reload(my_project)
    except MaturinError:
        log.info("reload failed")
    else:
        message = "expected compilation failure"
        raise AssertionError(message)
    log.info("reload finish")

    assert my_project.get_num() == 10

    log.info("modifying project")
    _modify_project_num(20)

    log.info("reload start")
    importlib.reload(my_project)
    log.info("reload finish")

    assert my_project.get_num() == 20

    log.info("SUCCESS")


def _test_pickling() -> None:
    maturin_import_hook.install()

    log.info("initial import start")
    import my_project  # type: ignore[missing-import]

    log.info("initial import finish")

    int_a = my_project.PicklableInteger(123, "a")
    int_b = my_project.PicklableInteger(123, "b")
    int_c = my_project.PicklableInteger(999, "c")
    assert int_a == int_b
    assert int_a != int_c
    assert type(int_a) is type(int_b)
    assert isinstance(int_b, type(int_a))
    int_a_data = pickle.dumps(int_a)
    int_a_unpickled_1 = pickle.loads(int_a_data)
    assert int_a == int_a_unpickled_1
    assert type(int_a_unpickled_1) is type(int_a)

    log.info("modifying project")
    _modify_project_num(15)

    log.info("reload start")
    importlib.reload(my_project)
    log.info("reload finish")

    int_d = my_project.PicklableInteger(123, "d")
    int_e = my_project.PicklableInteger(123, "e")
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
    import my_project  # type: ignore[missing-import]

    log.info("initial import finish")

    assert my_project.child.get_str() == "foo"

    log.info("modifying project")
    _modify_project_str("bar")

    log.info("reload start")
    importlib.reload(my_project)
    log.info("reload finish")

    assert my_project.child.get_str() == "bar"

    log.info("reload start")
    try:
        importlib.reload(my_project.child)
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
elif action == "_test_install_after_import":
    _test_install_after_import()
elif action == "_test_compilation_error":
    _test_compilation_error()
elif action == "_test_pickling":
    _test_pickling()
elif action == "_test_submodule":
    _test_submodule()
else:
    raise ValueError(action)
