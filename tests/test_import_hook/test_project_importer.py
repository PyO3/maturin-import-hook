import logging
import os
import re
import shutil
import site
import subprocess
import sys
from pathlib import Path
from textwrap import dedent
from typing import Iterator, Tuple

import pytest
from maturin_import_hook._building import fix_direct_url
from maturin_import_hook.project_importer import DefaultProjectFileSearcher, _load_dist_info

from .common import (
    IMPORT_HOOK_HEADER,
    TEST_CRATES_DIR,
    all_usable_test_crate_names,
    check_match,
    get_file_times,
    get_string_between,
    missing_entrypoint_error_message_pattern,
    mixed_test_crate_names,
    remove_ansii_escape_characters,
    run_concurrent_python,
    run_python,
    run_python_code,
    set_file_times_recursive,
    with_underscores,
)

"""
These tests ensure the correct functioning of the project importer import hook.
They can be run from any python environment with the necessary requirements but
the tests will need to install and uninstall packages and clear the maturin build
cache of the current environment so it is recommended to run using the `test_runner`
package which provides a clean environment and allows running the tests in parallel.
"""

script_dir = Path(__file__).parent.resolve()
helpers_dir = script_dir / "project_importer_helpers"
log = logging.getLogger(__name__)


@pytest.mark.parametrize(
    "project_name",
    # path dependencies tested separately
    sorted(set(all_usable_test_crate_names()) - {"pyo3-mixed-with-path-dep"}),
)
def test_install_from_script_inside(workspace: Path, project_name: str) -> None:
    """This test ensures that when a script is run from within a maturin project, the
    import hook can identify and install the containing project even if it is not
    already installed.

    limitation: if the project has python dependencies then those dependencies will be installed
    when the import hook triggers installation of the project but unlike the maturin project
    which the import hook handles specially, other installed projects may not become available
    until the interpreter is restarted (or the site module is reloaded)
    """
    _uninstall(project_name)

    project_dir = _get_project_copy(TEST_CRATES_DIR / project_name, workspace / project_name)

    check_installed_dir = project_dir / "check_installed"
    check_installed_path = check_installed_dir / "check_installed.py"
    check_installed_path.write_text(f"{IMPORT_HOOK_HEADER}\n\n{check_installed_path.read_text()}")

    empty_dir = workspace / "empty"
    empty_dir.mkdir()

    output1, duration1 = run_python([str(check_installed_path)], cwd=empty_dir)
    assert "SUCCESS" in output1
    assert _rebuilt_message(project_name) in output1
    assert _up_to_date_message(project_name) not in output1

    assert _is_editable_installed_correctly(project_name, project_dir, "mixed" in project_name)

    output2, duration2 = run_python([str(check_installed_path)], cwd=empty_dir)
    assert "SUCCESS" in output2
    assert _rebuilt_message(project_name) not in output2
    assert _up_to_date_message(project_name) in output2

    assert duration2 < duration1

    assert _is_editable_installed_correctly(project_name, project_dir, "mixed" in project_name)


@pytest.mark.parametrize("project_name", ["pyo3-mixed", "pyo3-pure"])
def test_do_not_install_from_script_inside(workspace: Path, project_name: str) -> None:
    """This test ensures that when the import hook works correctly when it is
    configured to not rebuild/install projects if they aren't already installed.
    """
    _uninstall(project_name)

    project_dir = _get_project_copy(TEST_CRATES_DIR / project_name, workspace / project_name)

    check_installed_path = project_dir / "check_installed/check_installed.py"
    header = dedent("""\
    import logging
    logging.basicConfig(format='%(name)s [%(levelname)s] %(message)s', level=logging.DEBUG)

    import maturin_import_hook
    maturin_import_hook.reset_logger()
    from maturin_import_hook import project_importer
    project_importer.install(enable_automatic_installation=False)
    """)
    check_installed_path.write_text(f"{header}\n\n{check_installed_path.read_text()}")

    empty_dir = workspace / "empty"
    empty_dir.mkdir()

    output1, _ = run_python([str(check_installed_path)], cwd=empty_dir, expect_error=True, quiet=True)
    assert (
        f'package "{with_underscores(project_name)}" is not already '
        f"installed and enable_automatic_installation=False. Not importing"
    ) in output1
    assert "SUCCESS" not in output1

    _install_editable(project_dir)
    assert _is_editable_installed_correctly(project_name, project_dir, "mixed" in project_name)

    output2, _ = run_python([str(check_installed_path)], cwd=empty_dir)
    assert "SUCCESS" in output2
    assert f'package "{with_underscores(project_name)}" will be rebuilt because: no build status found' in output2
    assert _rebuilt_message(project_name) in output2

    output3, _ = run_python([str(check_installed_path)], cwd=empty_dir)
    assert "SUCCESS" in output3
    assert _rebuilt_message(project_name) not in output3
    assert _up_to_date_message(project_name) in output3


@pytest.mark.parametrize("project_name", ["pyo3-mixed", "pyo3-pure"])
def test_do_not_rebuild_if_installed_non_editable(workspace: Path, project_name: str) -> None:
    """This test ensures that if a maturin project is installed in non-editable
    mode then the import hook will not rebuild it or re-install it in editable mode.
    """
    _uninstall(project_name)
    project_dir = _get_project_copy(TEST_CRATES_DIR / project_name, workspace / project_name)
    _install_non_editable(project_dir)

    check_installed_outside_project = workspace / "check_installed"
    check_installed_outside_project.mkdir()

    check_installed_dir = project_dir / "check_installed"
    check_installed_path = check_installed_dir / "check_installed.py"
    header = dedent("""\
    import sys
    import logging
    logging.basicConfig(format='%(name)s [%(levelname)s] %(message)s', level=logging.DEBUG)
    import maturin_import_hook
    maturin_import_hook.reset_logger()
    enable_automatic_installation = len(sys.argv) > 1 and sys.argv[1] == 'INSTALL_NEW'
    print(f'{enable_automatic_installation=}')
    maturin_import_hook.install(enable_automatic_installation=enable_automatic_installation)
    """)
    check_installed_path.write_text(f"{header}\n\n{check_installed_path.read_text()}")
    shutil.copy(check_installed_path, check_installed_outside_project)

    (project_dir / "src/lib.rs").write_text("")  # will break once rebuilt

    # when outside the project, can still detect non-editable installed projects via dist-info
    output1, _ = run_python(["check_installed.py"], cwd=check_installed_outside_project)
    assert "SUCCESS" in output1
    assert "enable_automatic_installation=False" in output1
    assert f'found project linked by dist-info: "{project_dir}"' in output1
    assert "package not installed in editable-mode and enable_automatic_installation=False. not rebuilding" in output1

    # when inside the project, will detect the project above
    output2, _ = run_python(["check_installed.py"], cwd=check_installed_dir)
    assert "SUCCESS" in output2
    assert "enable_automatic_installation=False" in output2
    assert "found project above the search path:" in output2
    assert "package not installed in editable-mode and enable_automatic_installation=False. not rebuilding" in output2

    output3, _ = run_python(
        ["check_installed.py", "INSTALL_NEW"],
        cwd=check_installed_outside_project,
        quiet=True,
        expect_error=True,
    )
    assert "SUCCESS" not in output3
    assert "enable_automatic_installation=True" in output3
    pattern = f"ImportError: {missing_entrypoint_error_message_pattern(with_underscores(project_name))}"
    assert re.search(pattern, output3) is not None


@pytest.mark.parametrize("initially_mixed", [False, True])
@pytest.mark.parametrize(
    "project_name",
    # path dependencies tested separately
    sorted(set(all_usable_test_crate_names()) - {"pyo3-mixed-with-path-dep"}),
)
def test_import_editable_installed_rebuild(workspace: Path, project_name: str, initially_mixed: bool) -> None:
    """This test ensures that an editable installed project is rebuilt when necessary if the import
    hook is active. This applies to mixed projects (which are installed as .pth files into
    site-packages when installed in editable mode) as well as pure projects (which are copied to site-packages
    when with a link back to the source directory when installed in editable mode).

    This is tested with the project initially being mixed and initially being pure to test that the import hook
    works even if the project changes significantly (eg from mixed to pure)
    """
    _uninstall(project_name)

    check_installed = (TEST_CRATES_DIR / project_name / "check_installed/check_installed.py").read_text()

    project_dir = _create_project_from_blank_template(project_name, workspace / project_name, mixed=initially_mixed)

    log.info("installing blank project as %s", project_name)

    _install_editable(project_dir)
    assert _is_editable_installed_correctly(project_name, project_dir, initially_mixed)

    # without the import hook the installation test is expected to fail because the project should not be installed yet
    output0, _ = run_python_code(check_installed, quiet=True, expect_error=True)
    assert "AttributeError" in output0 or "ImportError" in output0 or "ModuleNotFoundError" in output0

    check_installed = f"{IMPORT_HOOK_HEADER}\n\n{check_installed}"

    log.info("overwriting blank project with genuine project without re-installing")
    shutil.rmtree(project_dir)
    _get_project_copy(TEST_CRATES_DIR / project_name, project_dir)

    output1, duration1 = run_python_code(check_installed)
    assert "SUCCESS" in output1
    assert _rebuilt_message(project_name) in output1
    assert _up_to_date_message(project_name) not in output1

    assert _is_editable_installed_correctly(project_name, project_dir, "mixed" in project_name)

    output2, duration2 = run_python_code(check_installed)
    assert "SUCCESS" in output2
    assert _rebuilt_message(project_name) not in output2
    assert _up_to_date_message(project_name) in output2

    assert duration2 < duration1

    assert _is_editable_installed_correctly(project_name, project_dir, "mixed" in project_name)


@pytest.mark.parametrize(
    "project_name",
    # path dependencies tested separately
    sorted(set(mixed_test_crate_names()) - {"pyo3-mixed-with-path-dep"}),
)
def test_import_editable_installed_mixed_missing(workspace: Path, project_name: str) -> None:
    """This test ensures that editable installed mixed projects are rebuilt if they are imported
    and their artifacts are missing.

    This can happen when cleaning untracked files from git for example.

    This only affects mixed projects because artifacts of editable installed pure projects are
    copied to site-packages instead.
    """
    _uninstall(project_name)

    # making a copy because editable installation may write files into the project directory
    project_dir = _get_project_copy(TEST_CRATES_DIR / project_name, workspace / project_name)
    project_backup_dir = _get_project_copy(TEST_CRATES_DIR / project_name, workspace / f"backup_{project_name}")

    _install_editable(project_dir)
    assert _is_editable_installed_correctly(project_name, project_dir, "mixed" in project_name)

    check_installed = TEST_CRATES_DIR / project_name / "check_installed/check_installed.py"

    log.info("checking that check_installed works without the import hook right after installing")
    output0, _ = run_python_code(check_installed.read_text())
    assert "SUCCESS" in output0

    check_installed_script = f"{IMPORT_HOOK_HEADER}\n\n{check_installed.read_text()}"

    shutil.rmtree(project_dir)
    shutil.copytree(project_backup_dir, project_dir)

    log.info("checking that the import hook rebuilds the project")

    output1, duration1 = run_python_code(check_installed_script)
    assert "SUCCESS" in output1
    assert _rebuilt_message(project_name) in output1
    assert _up_to_date_message(project_name) not in output1

    output2, duration2 = run_python_code(check_installed_script)
    assert "SUCCESS" in output2
    assert _rebuilt_message(project_name) not in output2
    assert _up_to_date_message(project_name) in output2

    assert duration2 < duration1

    assert _is_editable_installed_correctly(project_name, project_dir, True)


@pytest.mark.parametrize("mixed", [False, True])
@pytest.mark.parametrize("initially_mixed", [False, True])
def test_concurrent_import(workspace: Path, initially_mixed: bool, mixed: bool) -> None:
    """This test ensures that if multiple scripts attempt to use the import hook concurrently,
    that the project still installs correctly and does not crash.

    This test uses a blank project initially to ensure that a rebuild is necessary to be
    able to use the project.
    """
    if mixed:
        project_name = "pyo3-mixed"
        check_installed = dedent("""\
        import pyo3_mixed
        assert pyo3_mixed.get_42() == 42
        print('SUCCESS')
        """)
    else:
        project_name = "pyo3-pure"
        check_installed = dedent("""\
        import pyo3_pure
        assert pyo3_pure.DummyClass.get_42() == 42
        print('SUCCESS')
        """)

    _uninstall(project_name)

    # increase default timeout as under heavy load on a weak machine
    # the workers may be waiting on the locks for a long time.
    original_call = "import_hook.install()"
    assert original_call in IMPORT_HOOK_HEADER
    header = IMPORT_HOOK_HEADER.replace(original_call, "import_hook.install(lock_timeout_seconds=10 * 60)")
    check_installed_with_hook = f"{header}\n\n{check_installed}"

    project_dir = _create_project_from_blank_template(project_name, workspace / project_name, mixed=initially_mixed)

    log.info("initially mixed: %s mixed: %s", initially_mixed, mixed)
    log.info("installing blank project as %s", project_name)

    _install_editable(project_dir)
    assert _is_editable_installed_correctly(project_name, project_dir, initially_mixed)

    shutil.rmtree(project_dir)
    _get_project_copy(TEST_CRATES_DIR / project_name, project_dir)

    args = {"python_script": check_installed_with_hook, "quiet": True}

    outputs = run_concurrent_python(3, run_python_code, args)

    num_compilations = 0
    num_up_to_date = 0
    num_waiting = 0
    for output in outputs:
        assert "SUCCESS" in output.output

        if "waiting on lock" in output.output:
            num_waiting += 1

        if _up_to_date_message(project_name) in output.output:
            num_up_to_date += 1

        if _rebuilt_message(project_name) in output.output:
            num_compilations += 1

    assert num_compilations == 1
    assert num_up_to_date == 2
    assert num_waiting == 2

    assert _is_editable_installed_correctly(project_name, project_dir, mixed)


def test_import_multiple_projects(workspace: Path) -> None:
    """This test ensures that the import hook can be used to load multiple projects
    in the same run.

    A single pair of projects is chosen for this test because it should not make
    any difference which projects are imported
    """
    _uninstall("pyo3-mixed")
    _uninstall("pyo3-pure")

    mixed_dir = _create_project_from_blank_template("pyo3-mixed", workspace / "pyo3-mixed", mixed=True)
    pure_dir = _create_project_from_blank_template("pyo3-pure", workspace / "pyo3-pure", mixed=False)

    _install_editable(mixed_dir)
    assert _is_editable_installed_correctly("pyo3-mixed", mixed_dir, True)
    _install_editable(pure_dir)
    assert _is_editable_installed_correctly("pyo3-pure", pure_dir, False)

    shutil.rmtree(mixed_dir)
    shutil.rmtree(pure_dir)
    _get_project_copy(TEST_CRATES_DIR / "pyo3-mixed", mixed_dir)
    _get_project_copy(TEST_CRATES_DIR / "pyo3-pure", pure_dir)

    check_installed = "{}\n\n{}\n\n{}".format(
        IMPORT_HOOK_HEADER,
        (mixed_dir / "check_installed/check_installed.py").read_text(),
        (pure_dir / "check_installed/check_installed.py").read_text(),
    )

    output1, duration1 = run_python_code(check_installed)
    assert "SUCCESS" in output1
    assert _rebuilt_message("pyo3-mixed") in output1
    assert _rebuilt_message("pyo3-pure") in output1
    assert _up_to_date_message("pyo3-mixed") not in output1
    assert _up_to_date_message("pyo3-pure") not in output1

    output2, duration2 = run_python_code(check_installed)
    assert "SUCCESS" in output2
    assert _rebuilt_message("pyo3-mixed") not in output2
    assert _rebuilt_message("pyo3-pure") not in output2
    assert _up_to_date_message("pyo3-mixed") in output2
    assert _up_to_date_message("pyo3-pure") in output2

    assert duration2 < duration1

    assert _is_editable_installed_correctly("pyo3-mixed", mixed_dir, True)
    assert _is_editable_installed_correctly("pyo3-pure", pure_dir, False)


def test_rebuild_on_change_to_path_dependency(workspace: Path) -> None:
    """This test ensures that the imported project is rebuilt if any of its path
    dependencies are edited.
    """
    project_name = "pyo3-mixed-with-path-dep"
    _uninstall(project_name)

    project_dir = _get_project_copy(TEST_CRATES_DIR / project_name, workspace / project_name)
    _get_project_copy(TEST_CRATES_DIR / "some_path_dep", workspace / "some_path_dep")
    transitive_dep_dir = _get_project_copy(TEST_CRATES_DIR / "transitive_path_dep", workspace / "transitive_path_dep")

    _install_editable(project_dir)
    assert _is_editable_installed_correctly(project_name, project_dir, True)

    check_installed = f"""
{IMPORT_HOOK_HEADER}

import pyo3_mixed_with_path_dep

assert pyo3_mixed_with_path_dep.get_42() == 42, 'get_42 did not return 42'

print('21 is half 42:', pyo3_mixed_with_path_dep.is_half(21, 42))
print('21 is half 63:', pyo3_mixed_with_path_dep.is_half(21, 63))
"""

    output1, duration1 = run_python_code(check_installed)
    assert "21 is half 42: True" in output1
    assert "21 is half 63: False" in output1

    transitive_dep_lib = transitive_dep_dir / "src/lib.rs"
    transitive_dep_lib.write_text(transitive_dep_lib.read_text().replace("x + y == sum", "x + x + y == sum"))

    output2, duration2 = run_python_code(check_installed)
    assert "21 is half 42: False" in output2
    assert "21 is half 63: True" in output2

    assert _is_editable_installed_correctly(project_name, project_dir, True)


@pytest.mark.parametrize("is_mixed", [False, True])
def test_rebuild_on_settings_change(workspace: Path, is_mixed: bool) -> None:
    """When the source code has not changed but the import hook uses different maturin flags
    the project is rebuilt.
    """
    _uninstall("my-project")

    project_dir = _create_project_from_blank_template("my-project", workspace / "my-project", mixed=is_mixed)
    shutil.copy(helpers_dir / "my_project.rs", project_dir / "src/lib.rs")
    manifest_path = project_dir / "Cargo.toml"
    manifest_path.write_text(f"{manifest_path.read_text()}\n[features]\nlarge_number = []\n")

    _install_editable(project_dir)
    assert _is_editable_installed_correctly("my-project", project_dir, is_mixed)

    helper_path = helpers_dir / "rebuild_on_settings_change_helper.py"

    output1, _ = run_python([str(helper_path)], cwd=workspace)
    assert "building with default settings" in output1
    assert "get_num = 10" in output1
    assert "SUCCESS" in output1
    assert 'package "my_project" will be rebuilt because: no build status found' in output1

    output2, _ = run_python([str(helper_path)], cwd=workspace)
    assert "get_num = 10" in output2
    assert "SUCCESS" in output2
    assert 'package up to date: "my_project"' in output2

    output3, _ = run_python([str(helper_path), "LARGE_NUMBER"], cwd=workspace)
    assert "building with large_number feature enabled" in output3
    assert (
        'package "my_project" will be rebuilt because: current maturin args do not match the previous build'
    ) in output3
    assert "get_num = 100" in output3
    assert "SUCCESS" in output3

    output4, _ = run_python([str(helper_path), "LARGE_NUMBER"], cwd=workspace)
    assert "building with large_number feature enabled" in output4
    assert 'package up to date: "my_project"' in output4
    assert "get_num = 100" in output4
    assert "SUCCESS" in output4


def test_low_resolution_mtime(workspace: Path) -> None:
    """see test_rust_file_importer.test_low_resolution_mtime"""
    # managing the times manually with this timer file ensures that cargo does not do any unwanted caching of its own
    timer_path = workspace / "timer"
    timer_path.touch()

    _uninstall("my-project")

    project_dir = _create_project_from_blank_template("my-project", workspace / "my-project", mixed=False)
    source_root = project_dir / "src"
    lib_path = source_root / "lib.rs"
    shutil.copy(helpers_dir / "my_project.rs", lib_path)

    _install_editable(project_dir)
    assert _is_editable_installed_correctly("my-project", project_dir, False)

    helper_path = helpers_dir / "low_resolution_mtime_helper.py"

    output1, _ = run_python([str(helper_path)], cwd=workspace)
    assert 'building "my_project"' in output1
    assert 'package "my_project" will be rebuilt because: no build status found' in output1
    assert "get_num = 10" in output1
    assert "SUCCESS" in output1

    package_path = Path((workspace / "package_path.txt").read_text())
    extension_path = Path((workspace / "extension_path.txt").read_text())

    def set_mtimes_equal() -> None:
        s = DefaultProjectFileSearcher()
        oldest_package_path = min((p for p in s.get_installation_paths(package_path)), key=lambda p: p.stat().st_mtime)
        times = get_file_times(oldest_package_path)
        set_file_times_recursive(package_path, times)
        set_file_times_recursive(source_root, times)

    lib_path.write_text(lib_path.read_text().replace("let num = 10;", "let num = 20;"))
    set_mtimes_equal()

    output2, _ = run_python([str(helper_path)], cwd=workspace)
    assert 'building "my_project"' in output2
    assert 'package "my_project" will be rebuilt because: installation may be out of date' in output2
    assert "get_num = 20" in output2
    assert "SUCCESS" in output2

    # set the mtimes equal again but this time nothing has changed. A rebuild should still be triggered
    set_mtimes_equal()

    output3, _ = run_python([str(helper_path)], cwd=workspace)
    assert 'building "my_project"' in output3
    assert 'package "my_project" will be rebuilt because: installation may be out of date' in output3
    assert "get_num = 20" in output3
    assert "SUCCESS" in output3

    extension_stat = extension_path.stat()
    source_code_stat = lib_path.stat()
    # extension mtime should be strictly greater to remove the ambiguity about which is newer
    assert source_code_stat.st_mtime < extension_stat.st_mtime

    output4, _ = run_python([str(helper_path)], cwd=workspace)
    assert 'building "my_project"' not in output4
    assert "get_num = 20" in output4
    assert "SUCCESS" in output4


class TestReload:
    """test that importlib.reload() can be used to reload modules imported by the import hook

    The tests are organised to strike a balance between having many tests for individual behaviours and bundling
    checks together to reduce the time spent compiling.

    see docs/reloading.md for details
    """

    @staticmethod
    def _create_reload_project(output_dir: Path, mixed: bool) -> Tuple[Path, Path]:
        project_dir = _create_project_from_blank_template("my-project", output_dir / "my-project", mixed=mixed)
        if mixed:
            init = dedent("""\
            import logging
            from .my_project import *
            logging.info('my_project __init__ initialised')
            """)
            (project_dir / "my_project/__init__.py").write_text(init)

            other_module = dedent("""\
            from . import my_project
            from .my_project import get_num
            import logging

            other_data = {"foo": 123}

            try:
                other_data_init_once
            except NameError:
                other_data_init_once = {"foo": 123}

            other_data_str = "hi"

            def get_twice_num_direct():
                return get_num() * 2

            def get_twice_num_indirect():
                return my_project.get_num() * 2

            logging.info('my_project other_module initialised')
            """)
            (project_dir / "my_project/other_module.py").write_text(other_module)

        lib_path = project_dir / "src/lib.rs"
        shutil.copy(helpers_dir / "reload_template.rs", lib_path)
        _install_editable(project_dir)
        return project_dir, lib_path

    @pytest.mark.parametrize("is_mixed", [False, True])
    def test_basic_reload(self, workspace: Path, is_mixed: bool) -> None:
        """test several properties of reloading maturin packages with the import hook active

        - import styles
            - `import ...` style import
                - top level
                - extension module
            - `from ... import ...` style import
                - top level
                - extension module
            - duplicate reference to module
        - package styles
            - pure rust
            - mixed python/rust
        - module initialisation
            - top level module
            - extension module
        - classes
            - types becoming incompatible after reloading
        - calling reload
            - on the top level module
                - after making changes
                - after no changes
            - on the extension module
                - after making changes (intentionally does nothing)
        """
        _uninstall("my-project")
        _project_dir, lib_path = self._create_reload_project(workspace, is_mixed)

        output, _ = run_python(
            [str(helpers_dir / "reload_helper.py"), str(lib_path), "_test_basic_reload"], cwd=workspace
        )
        info = "\n".join(line for line in output.splitlines() if "[INFO]" in line)

        assert "SUCCESS" in output

        e = re.escape

        # This checks that the INFO level logs are exactly these messages (with nothing in between).
        # This verifies that rebuilds and module initialisation are behaving as expected
        expected_info_parts = [
            e("reload_helper [INFO] initial import start"),
            e('maturin_import_hook [INFO] building "my_project"'),  # because: no build status
            'maturin_import_hook \\[INFO\\] rebuilt and loaded package "my_project" in [0-9.]+s',
            e("root [INFO] my_project extension module initialised"),
            e("root [INFO] my_project __init__ initialised" if is_mixed else ""),
            e("reload_helper [INFO] initial import finish"),
            e("root [INFO] comparing Integer instances a and b"),
            e("root [INFO] comparing Integer instances a and c"),
            e("reload_helper [INFO] modifying project"),
            e("reload_helper [INFO] reload 1 start"),
            e('maturin_import_hook [INFO] building "my_project"'),
            'maturin_import_hook \\[INFO\\] rebuilt and loaded package "my_project" in [0-9.]+s',
            e("root [INFO] my_project extension module initialised"),
            e("root [INFO] my_project __init__ initialised" if is_mixed else ""),
            e("reload_helper [INFO] reload 1 finish"),
            e("root [INFO] comparing Integer instances d and e"),
            e("reload_helper [INFO] reload 2 start"),
            e("root [INFO] my_project __init__ initialised" if is_mixed else ""),
            e("reload_helper [INFO] reload 2 finish"),
            e("reload_helper [INFO] modifying project"),
            e("reload_helper [INFO] reload 3 start"),
            e('maturin_import_hook [INFO] building "my_project"'),
            'maturin_import_hook \\[INFO\\] rebuilt and loaded package "my_project" in [0-9.]+s',
            e("root [INFO] my_project extension module initialised"),
            e("root [INFO] my_project __init__ initialised" if is_mixed else ""),
            e("reload_helper [INFO] reload 3 finish"),
            e("reload_helper [INFO] reload 4 start"),
            e("reload_helper [INFO] reload 4 finish"),
            e("reload_helper [INFO] SUCCESS"),
        ]
        expected_info_pattern = "\n".join(line for line in expected_info_parts if line)
        check_match(info, expected_info_pattern, flags=re.MULTILINE)

        # these checks ensure that the internals of the import hook are performing the expected actions
        initial_import = get_string_between(output, "initial import start", "initial import finish")
        assert initial_import is not None
        assert 'MaturinProjectImporter searching for "my_project"' in initial_import
        assert 'building "my_project"' in initial_import

        assert 'handling reload of "my_project"' not in initial_import

        reload_1 = get_string_between(output, "reload 1 start", "reload 1 finish")
        assert reload_1 is not None
        assert 'MaturinProjectImporter searching for "my_project" (reload)' in reload_1
        assert 'building "my_project"' in reload_1
        assert 'handling reload of "my_project"' in reload_1
        assert "unloading 1 modules: ['my_project.my_project']" in reload_1

        assert 'package up to date: "my_project"' not in reload_1

        reload_2 = get_string_between(output, "reload 2 start", "reload 2 finish")
        assert reload_2 is not None
        assert 'MaturinProjectImporter searching for "my_project" (reload)' in reload_2
        assert 'package up to date: "my_project"' in reload_2
        assert 'handling reload of "my_project"' in reload_2
        assert "unloading 1 modules: ['my_project.my_project']" in reload_2

        assert 'building "my_project"' not in reload_2

        reload_3 = get_string_between(output, "reload 3 start", "reload 3 finish")
        assert reload_3 is not None
        assert 'MaturinProjectImporter searching for "my_project" (reload)' in reload_3
        assert 'building "my_project"' in reload_3
        assert 'handling reload of "my_project"' in reload_3
        assert "unloading 1 modules: ['my_project.my_project']" in reload_3

        assert 'package up to date: "my_project"' not in reload_3

        reload_4 = get_string_between(output, "reload 4 start", "reload 4 finish")
        assert reload_4 is not None
        assert 'MaturinProjectImporter searching for "my_project"' not in reload_4

    def test_globals(self, workspace: Path) -> None:
        """tests properties of global variables initialised in python and rust modules when the package is reloaded

        - module types:
            - root module
            - extension module
            - python module
        - properties tested
            - __path__
            - adding new global
            - global initialised once
            - modifying mutable global
            - assigning to immutable global
        - reload without changes
        - reload with changes changes
        """
        _uninstall("my-project")
        _project_dir, lib_path = self._create_reload_project(workspace, mixed=True)

        output, _ = run_python([str(helpers_dir / "reload_helper.py"), str(lib_path), "_test_globals"], cwd=workspace)
        info = "\n".join(line for line in output.splitlines() if "[INFO]" in line)

        assert "SUCCESS" in output

        e = re.escape

        expected_info_parts = [
            e("reload_helper [INFO] initial import start"),
            e('maturin_import_hook [INFO] building "my_project"'),
            'maturin_import_hook \\[INFO\\] rebuilt and loaded package "my_project" in [0-9.]+s',
            e("root [INFO] my_project extension module initialised"),
            e("root [INFO] my_project __init__ initialised"),
            e("root [INFO] my_project other_module initialised"),
            e("reload_helper [INFO] initial import finish"),
            e("reload_helper [INFO] checking extension module"),
            e("reload_helper [INFO] checking root module"),
            e("reload_helper [INFO] checking other_module"),
            e("reload_helper [INFO] reload 1 start"),
            e("root [INFO] my_project __init__ initialised"),
            e("reload_helper [INFO] reload 2 finish"),
            e("reload_helper [INFO] checking extension module"),
            e("reload_helper [INFO] checking root module"),
            e("reload_helper [INFO] checking other_module"),
            e("reload_helper [INFO] modifying project"),
            e("reload_helper [INFO] reload 2 start"),
            e('maturin_import_hook [INFO] building "my_project"'),
            'maturin_import_hook \\[INFO\\] rebuilt and loaded package "my_project" in [0-9.]+s',
            e("root [INFO] my_project extension module initialised"),
            e("root [INFO] my_project __init__ initialised"),
            e("reload_helper [INFO] reload 2 finish"),
            e("reload_helper [INFO] checking extension module"),
            e("reload_helper [INFO] checking root module"),
            e("reload_helper [INFO] checking other_module"),
            e("reload_helper [INFO] SUCCESS"),
        ]
        expected_info_pattern = "\n".join(line for line in expected_info_parts if line)
        check_match(info, expected_info_pattern, flags=re.MULTILINE)

    def test_other_module(self, workspace: Path) -> None:
        """test the behaviour of reloading a mixed python/rust package with python modules
        that import the extension module
        """
        _uninstall("my-project")
        _project_dir, lib_path = self._create_reload_project(workspace, mixed=True)

        output, _ = run_python(
            [str(helpers_dir / "reload_helper.py"), str(lib_path), "_test_other_module"], cwd=workspace
        )
        info = "\n".join(line for line in output.splitlines() if "[INFO]" in line)

        assert "SUCCESS" in output

        e = re.escape

        expected_info_parts = [
            e("reload_helper [INFO] initial import start"),
            e('maturin_import_hook [INFO] building "my_project"'),  # because: no build status
            'maturin_import_hook \\[INFO\\] rebuilt and loaded package "my_project" in [0-9.]+s',
            e("root [INFO] my_project extension module initialised"),
            e("root [INFO] my_project __init__ initialised"),
            e("root [INFO] my_project other_module initialised"),
            e("reload_helper [INFO] initial import finish"),
            e("reload_helper [INFO] modifying project"),
            e("reload_helper [INFO] reload other_module start"),
            e("root [INFO] my_project other_module initialised"),
            e("reload_helper [INFO] reload other_module finish"),
            e("reload_helper [INFO] reload package start"),
            e('maturin_import_hook [INFO] building "my_project"'),
            'maturin_import_hook \\[INFO\\] rebuilt and loaded package "my_project" in [0-9.]+s',
            e("root [INFO] my_project extension module initialised"),
            e("root [INFO] my_project __init__ initialised"),
            e("reload_helper [INFO] reload package finish"),
            e("reload_helper [INFO] reload other_module start"),
            e("root [INFO] my_project other_module initialised"),
            e("reload_helper [INFO] reload other_module finish"),
            e("reload_helper [INFO] SUCCESS"),
        ]
        expected_info_pattern = "\n".join(line for line in expected_info_parts if line)
        check_match(info, expected_info_pattern, flags=re.MULTILINE)

    @pytest.mark.parametrize("is_mixed", [False, True])
    def test_reload_without_import_hook(self, workspace: Path, is_mixed: bool) -> None:
        """test when reload is used without support from the import hook"""
        _uninstall("my-project")
        _project_dir, lib_path = self._create_reload_project(workspace, is_mixed)

        output, _ = run_python(
            [str(helpers_dir / "reload_helper.py"), str(lib_path), "_test_reload_without_import_hook"], cwd=workspace
        )
        assert "SUCCESS" in output

        e = re.escape

        expected_parts = [
            e("reload_helper [INFO] initial import start"),
            e("root [INFO] my_project extension module initialised"),
            e("root [INFO] my_project __init__ initialised" if is_mixed else ""),
            e("reload_helper [INFO] initial import finish"),
            e("reload_helper [INFO] reload package start"),
            e("root [INFO] my_project __init__ initialised" if is_mixed else ""),
            e("reload_helper [INFO] reload package finish"),
            e("reload_helper [INFO] installing import hook"),
            e("reload_helper [INFO] modifying project"),
            e("reload_helper [INFO] reload package start"),
            e('maturin_import_hook [DEBUG] package "my_project" is already loaded and enable_reloading=False'),
            e("root [INFO] my_project __init__ initialised" if is_mixed else ""),
            e("reload_helper [INFO] reload package finish"),
            e("reload_helper [INFO] reload extension module start"),
            e("reload_helper [INFO] reload extension module finish"),
            e("reload_helper [INFO] uninstalling import hook"),
            e("reload_helper [INFO] reload package start"),
            e("root [INFO] my_project __init__ initialised" if is_mixed else ""),
            e("reload_helper [INFO] reload package finish"),
            e("reload_helper [INFO] SUCCESS\n"),
        ]
        expected_pattern = "\n".join(line for line in expected_parts if line)
        check_match(output, expected_pattern, flags=re.MULTILINE)

    @pytest.mark.parametrize("is_mixed", [False, True])
    def test_install_after_import(self, workspace: Path, is_mixed: bool) -> None:
        """test using reload on packages that are imported before the import hook was installed
        (should not make a difference)
        """
        _uninstall("my-project")
        _project_dir, lib_path = self._create_reload_project(workspace, is_mixed)

        output, _ = run_python(
            [str(helpers_dir / "reload_helper.py"), str(lib_path), "_test_install_after_import"], cwd=workspace
        )
        info = "\n".join(line for line in output.splitlines() if "[INFO]" in line)

        assert "SUCCESS" in output

        e = re.escape

        expected_parts = [
            e("reload_helper [INFO] initial import start"),
            e("root [INFO] my_project extension module initialised"),
            e("root [INFO] my_project __init__ initialised" if is_mixed else ""),
            e("reload_helper [INFO] initial import finish"),
            e("reload_helper [INFO] installing import hook"),
            e("reload_helper [INFO] modifying project"),
            e("reload_helper [INFO] reload start"),
            e('maturin_import_hook [INFO] building "my_project"'),
            'maturin_import_hook \\[INFO\\] rebuilt and loaded package "my_project" in [0-9.]+s',
            e("root [INFO] my_project extension module initialised"),
            e("root [INFO] my_project __init__ initialised" if is_mixed else ""),
            e("reload_helper [INFO] reload finish"),
            e("reload_helper [INFO] SUCCESS"),
        ]
        expected_pattern = "\n".join(line for line in expected_parts if line)
        check_match(info, expected_pattern, flags=re.MULTILINE)

    def test_compilation_error(self, workspace: Path) -> None:
        _uninstall("my-project")
        _project_dir, lib_path = self._create_reload_project(workspace, mixed=False)

        output, _ = run_python(
            [str(helpers_dir / "reload_helper.py"), str(lib_path), "_test_compilation_error"], cwd=workspace
        )
        assert "SUCCESS" in output

        e = re.escape

        expected_parts = [
            e("reload_helper [INFO] initial import start"),
            e('maturin_import_hook [DEBUG] MaturinProjectImporter searching for "my_project"'),
            e('maturin_import_hook [INFO] building "my_project"'),
            'maturin_import_hook \\[INFO\\] rebuilt and loaded package "my_project" in [0-9.]+s',
            e("root [INFO] my_project extension module initialised"),
            e("reload_helper [INFO] initial import finish"),
            e("reload_helper [INFO] modifying project"),
            e("reload_helper [INFO] reload start"),
            e('maturin_import_hook [DEBUG] MaturinProjectImporter searching for "my_project" (reload)'),
            e('maturin_import_hook [INFO] building "my_project"'),
            e("expected expression, found `;`"),
            e("maturin failed"),
            e("reload_helper [INFO] reload failed"),
            e("reload_helper [INFO] reload finish"),
            e("reload_helper [INFO] modifying project"),
            e("reload_helper [INFO] reload start"),
            e('maturin_import_hook [DEBUG] MaturinProjectImporter searching for "my_project" (reload)'),
            e('maturin_import_hook [INFO] building "my_project"'),
            'maturin_import_hook \\[INFO\\] rebuilt and loaded package "my_project" in [0-9.]+s',
            e("reload_helper [INFO] reload finish"),
            e("reload_helper [INFO] SUCCESS\n"),
        ]
        expected_pattern = ".*".join(line for line in expected_parts if line)
        check_match(output, expected_pattern, flags=re.MULTILINE | re.DOTALL)

    def test_pickling(self, workspace: Path) -> None:
        """test the classes that can be pickled behave as expected when the module is reloaded"""
        _uninstall("my-project")
        _project_dir, lib_path = self._create_reload_project(workspace, mixed=False)

        output, _ = run_python([str(helpers_dir / "reload_helper.py"), str(lib_path), "_test_pickling"], cwd=workspace)
        assert "SUCCESS" in output

        e = re.escape

        expected_parts = [
            e("reload_helper [INFO] initial import start"),
            e('maturin_import_hook [DEBUG] MaturinProjectImporter searching for "my_project"'),
            e('maturin_import_hook [INFO] building "my_project"'),
            'maturin_import_hook \\[INFO\\] rebuilt and loaded package "my_project" in [0-9.]+s',
            e("root [INFO] my_project extension module initialised"),
            e("reload_helper [INFO] initial import finish"),
            e("reload_helper [INFO] modifying project"),
            e("reload_helper [INFO] reload start"),
            e('maturin_import_hook [DEBUG] MaturinProjectImporter searching for "my_project" (reload)'),
            e('maturin_import_hook [INFO] building "my_project"'),
            'maturin_import_hook \\[INFO\\] rebuilt and loaded package "my_project" in [0-9.]+s',
            e("reload_helper [INFO] reload finish"),
            e("reload_helper [INFO] SUCCESS\n"),
        ]
        expected_pattern = ".*".join(line for line in expected_parts if line)
        check_match(output, expected_pattern, flags=re.MULTILINE | re.DOTALL)

    def test_submodule(self, workspace: Path) -> None:
        _uninstall("my-project")
        _project_dir, lib_path = self._create_reload_project(workspace, mixed=False)

        output, _ = run_python([str(helpers_dir / "reload_helper.py"), str(lib_path), "_test_submodule"], cwd=workspace)
        assert "SUCCESS" in output

        e = re.escape

        expected_parts = [
            e("reload_helper [INFO] initial import start"),
            e('maturin_import_hook [DEBUG] MaturinProjectImporter searching for "my_project"'),
            e('maturin_import_hook [INFO] building "my_project"'),
            'maturin_import_hook \\[INFO\\] rebuilt and loaded package "my_project" in [0-9.]+s',
            e("root [INFO] my_project extension module initialised"),
            e("reload_helper [INFO] initial import finish"),
            e("reload_helper [INFO] modifying project"),
            e("reload_helper [INFO] reload start"),
            e('maturin_import_hook [DEBUG] MaturinProjectImporter searching for "my_project" (reload)'),
            e('maturin_import_hook [INFO] building "my_project"'),
            'maturin_import_hook \\[INFO\\] rebuilt and loaded package "my_project" in [0-9.]+s',
            e("root [INFO] my_project extension module initialised"),
            e("reload_helper [INFO] reload finish"),
            e("reload_helper [INFO] reload start"),
            e("reload_helper [INFO] reload failed"),
            e("reload_helper [INFO] reload finish"),
            e("reload_helper [INFO] SUCCESS\n"),
        ]
        expected_pattern = ".*".join(line for line in expected_parts if line)
        check_match(output, expected_pattern, flags=re.MULTILINE | re.DOTALL)


class TestLogging:
    """These tests ensure that the desired messages are visible to the user in the default logging configuration."""

    @staticmethod
    def _logging_helper() -> str:
        return (helpers_dir / "logging_helper.py").read_text()

    @staticmethod
    def _logging_reload_helper() -> str:
        return (helpers_dir / "logging_reload_helper.py").read_text()

    @staticmethod
    def _create_clean_project(tmp_dir: Path, is_mixed: bool) -> Path:
        _uninstall("test-project")
        project_dir = _create_project_from_blank_template("test-project", tmp_dir / "test-project", mixed=is_mixed)
        _install_editable(project_dir)
        assert _is_editable_installed_correctly("test-project", project_dir, is_mixed)

        lib_path = project_dir / "src/lib.rs"
        lib_src = lib_path.read_text().replace("_m:", "m:").replace("Ok(())", 'm.add("value", 10)?;Ok(())')
        lib_path.write_text(lib_src)

        return project_dir

    def test_maturin_detection(self, workspace: Path) -> None:
        self._create_clean_project(workspace, True)

        output, _ = run_python_code(self._logging_helper(), env={"PATH": ""})
        assert output == "building \"test_project\"\ncaught MaturinError('maturin not found')\n"

        extra_bin = workspace / "bin"
        extra_bin.mkdir()
        mock_maturin_path = extra_bin / "maturin"
        mock_maturin_path.write_text('#!/usr/bin/env bash\necho "maturin 0.1.2"')
        mock_maturin_path.chmod(0o777)

        output, _ = run_python_code(self._logging_helper(), env={"PATH": f"{extra_bin}:/usr/bin"})
        assert output == (
            'building "test_project"\n'
            "caught MaturinError('unsupported maturin version: (0, 1, 2). "
            "Import hook requires >=(1, 4, 0),<(2, 0, 0)')\n"
        )

    @pytest.mark.parametrize("is_mixed", [False, True])
    def test_default_rebuild(self, workspace: Path, is_mixed: bool) -> None:
        """By default, when a module is out of date the import hook logs messages
        before and after rebuilding but hides the underlying details.
        """
        self._create_clean_project(workspace, is_mixed)

        output, _ = run_python_code(self._logging_helper())
        pattern = (
            'building "test_project"\n'
            'rebuilt and loaded package "test_project" in [0-9.]+s\n'
            "value 10\n"
            "SUCCESS\n"
        )
        check_match(output, pattern, flags=re.MULTILINE)

    @pytest.mark.parametrize("is_mixed", [False, True])
    def test_default_up_to_date(self, workspace: Path, is_mixed: bool) -> None:
        """By default, when the module is up-to-date nothing is printed."""
        self._create_clean_project(workspace / "project", is_mixed)

        run_python_code(self._logging_helper())  # run once to rebuild

        output, _ = run_python_code(self._logging_helper())
        assert output == "value 10\nSUCCESS\n"

    @pytest.mark.parametrize("is_mixed", [False, True])
    def test_default_compile_error(self, workspace: Path, is_mixed: bool) -> None:
        """If compilation fails then the error message from maturin is printed and an ImportError is raised."""
        project_dir = self._create_clean_project(workspace / "project", is_mixed)

        lib_path = project_dir / "src/lib.rs"
        lib_path.write_text(lib_path.read_text().replace("Ok(())", ""))

        output, _ = run_python_code(self._logging_helper())
        pattern = (
            'building "test_project"\n'
            'maturin_import_hook \\[ERROR\\] command ".*" returned non-zero exit status: 1\n'
            "maturin_import_hook \\[ERROR\\] maturin output:\n"
            ".*"
            "expected `Result<\\(\\), PyErr>`, found `\\(\\)`"
            ".*"
            "maturin failed"
            ".*"
            "caught MaturinError\\('Failed to build package with maturin'\\)\n"
        )
        check_match(output, pattern, flags=re.MULTILINE | re.DOTALL)

    @pytest.mark.parametrize("is_mixed", [False, True])
    def test_default_compile_warning(self, workspace: Path, is_mixed: bool) -> None:
        """If compilation succeeds with warnings then the output of maturin is printed.
        If the module is already up to date but warnings were raised when it was first
        built, the warnings will be printed again.
        """
        project_dir = self._create_clean_project(workspace / "project", is_mixed)
        lib_path = project_dir / "src/lib.rs"
        lib_path.write_text(lib_path.read_text().replace("Ok(())", "#[warn(unused_variables)]{let x = 12;}; Ok(())"))

        output1, _ = run_python_code(self._logging_helper())
        output1 = remove_ansii_escape_characters(output1)
        pattern = (
            'building "test_project"\n'
            'maturin_import_hook \\[WARNING\\] build of "test_project" succeeded with warnings:\n'
            ".*"
            "warning: unused variable: `x`"
            ".*"
            'rebuilt and loaded package "test_project" in [0-9.]+s\n'
            "value 10\n"
            "SUCCESS\n"
        )
        check_match(output1, pattern, flags=re.MULTILINE | re.DOTALL)

        output2, _ = run_python_code(self._logging_helper())
        output2 = remove_ansii_escape_characters(output2)
        pattern = (
            'maturin_import_hook \\[WARNING\\] the last build of "test_project" succeeded with warnings:\n'
            ".*"
            "warning: unused variable: `x`"
            ".*"
            "value 10\n"
            "SUCCESS\n"
        )
        check_match(output2, pattern, flags=re.MULTILINE | re.DOTALL)

    def test_reload(self, workspace: Path) -> None:
        self._create_clean_project(workspace, is_mixed=False)

        output, _ = run_python_code(self._logging_reload_helper())
        pattern = (
            "reload start\n"
            'building "test_project"\n'
            'rebuilt and loaded package "test_project" in [0-9.]+s\n'
            "reload finish\n"
            "reload start\n"
            "reload finish\n"
            "value 10\n"
            "SUCCESS\n"
        )
        check_match(output, pattern, flags=re.MULTILINE)

    @pytest.mark.parametrize("is_mixed", [False, True])
    def test_reset_logger_without_configuring(self, workspace: Path, is_mixed: bool) -> None:
        """If reset_logger is called then by default logging level INFO is not printed
        (because the messages are handled by the root logger).
        """
        self._create_clean_project(workspace / "project", is_mixed)
        output, _ = run_python_code(self._logging_helper(), args=["RESET_LOGGER"])
        assert output == "value 10\nSUCCESS\n"

    @pytest.mark.parametrize("is_mixed", [False, True])
    def test_successful_compilation_but_not_valid(self, workspace: Path, is_mixed: bool) -> None:
        """If the project compiles but does not import correctly an ImportError is raised."""
        project_dir = self._create_clean_project(workspace / "project", is_mixed)
        lib_path = project_dir / "src/lib.rs"
        lib_path.write_text(lib_path.read_text().replace("test_project", "test_project_new_name"))

        output, _ = run_python_code(self._logging_helper(), quiet=True)
        pattern = (
            'building "test_project"\n'
            'rebuilt and loaded package "test_project" in [0-9.]+s\n'
            f"caught ImportError\\('{missing_entrypoint_error_message_pattern('test_project')}'\\)\n"
        )
        check_match(output, pattern, flags=re.MULTILINE)


class TestDefaultProjectFileSearcher:
    class TestGetSourcePaths:
        def test_missing_extension(self, workspace: Path) -> None:
            s = DefaultProjectFileSearcher(
                source_excluded_dir_names=set(),
                source_excluded_dir_markers=set(),
                source_excluded_file_extensions=set(),
            )
            assert list(s.get_source_paths(workspace, [], workspace / "missing")) == []
            extension_dir = workspace / "extension"
            extension_dir.mkdir()
            assert list(s.get_source_paths(workspace, [], extension_dir)) == []

        def test_missing_paths(self, workspace: Path) -> None:
            s = DefaultProjectFileSearcher(
                source_excluded_dir_names=set(),
                source_excluded_dir_markers=set(),
                source_excluded_file_extensions=set(),
            )
            (workspace / "extension").touch()
            with pytest.raises(FileNotFoundError):
                list(s.get_source_paths(workspace, [workspace / "missing"], workspace / "extension"))

            with pytest.raises(FileNotFoundError):
                list(s.get_source_paths(workspace / "missing", [], workspace / "extension"))

        def test_simple(self, workspace: Path) -> None:
            s = DefaultProjectFileSearcher(
                source_excluded_dir_names=set(),
                source_excluded_dir_markers=set(),
                source_excluded_file_extensions=set(),
            )
            src_dir = workspace / "src"
            src_dir.mkdir()
            source_file_path = src_dir / "source_file.rs"
            source_file_path.touch()
            (workspace / "extension_module").touch()
            paths = set(s.get_source_paths(workspace, [], workspace / "extension_module"))
            assert paths == {source_file_path}

            (workspace / "extension_module").unlink()
            (workspace / "extension_module").mkdir()
            (workspace / "extension_module/stuff").touch()

            paths = set(s.get_source_paths(workspace, [], workspace / "extension_module"))
            assert paths == {source_file_path}

            s = DefaultProjectFileSearcher(
                source_excluded_dir_names={"src"},
                source_excluded_dir_markers=set(),
                source_excluded_file_extensions=set(),
            )
            paths = set(s.get_source_paths(workspace, [], workspace / "extension_module"))
            assert paths == set()

        def test_simple_path_dep(self, workspace: Path) -> None:
            project_a = workspace / "a"
            project_b = workspace / "b"
            project_a.mkdir()
            project_b.mkdir()

            (project_a / "source.py").touch()
            extension_dir = project_a / "extension"
            extension_dir.mkdir()
            (extension_dir / "extension.so").touch()
            (project_b / "source.py").touch()
            (project_b / "__pycache__").mkdir()
            (project_b / "__pycache__/source.pyc").touch()

            s = DefaultProjectFileSearcher(
                source_excluded_dir_names=set(),
                source_excluded_dir_markers=set(),
                source_excluded_file_extensions=set(),
            )
            paths = set(s.get_source_paths(project_a, [project_b], extension_dir))
            assert paths == {project_a / "source.py", project_b / "source.py", project_b / "__pycache__/source.pyc"}

            s = DefaultProjectFileSearcher(
                source_excluded_dir_names={"__pycache__"},
                source_excluded_dir_markers=set(),
                source_excluded_file_extensions=set(),
            )
            paths = set(s.get_source_paths(project_a, [project_b], extension_dir))
            assert paths == {project_a / "source.py", project_b / "source.py"}

            s = DefaultProjectFileSearcher(
                source_excluded_dir_names=set(),
                source_excluded_dir_markers=set(),
                source_excluded_file_extensions={".pyc"},
            )
            paths = set(s.get_source_paths(project_a, [project_b], extension_dir))
            assert paths == {project_a / "source.py", project_b / "source.py"}

        def test_extension_outside_project_source(self, tmp_path: Path) -> None:
            project_dir = tmp_path / "project"
            installed_dir = tmp_path / "site-packages"
            project_dir.mkdir()
            installed_dir.mkdir()

            (project_dir / "source").touch()
            extension_path = installed_dir / "extension"
            extension_path.touch()

            s = DefaultProjectFileSearcher(
                source_excluded_dir_names=set(),
                source_excluded_dir_markers=set(),
                source_excluded_file_extensions=set(),
            )
            paths = set(s.get_source_paths(project_dir, [], extension_path))
            assert paths == {project_dir / "source"}

    def test_get_installation_paths(self, workspace: Path) -> None:
        s = DefaultProjectFileSearcher(
            source_excluded_dir_names={"foo"},
            source_excluded_dir_markers=set(),
            source_excluded_file_extensions={".so"},
        )
        assert set(s.get_installation_paths(workspace)) == set()
        assert set(s.get_installation_paths(workspace / "missing")) == set()

        (workspace / "extension.so").touch()
        (workspace / "misc").touch()
        (workspace / "empty_subdir").mkdir()
        (workspace / "subdir").mkdir()
        (workspace / "subdir/file.py").touch()
        (workspace / "subdir/__pycache__").mkdir()
        (workspace / "subdir/__pycache__/file.pyc").touch()
        (workspace / "__pycache__").mkdir()
        (workspace / "__pycache__/__init__.pyc").touch()

        assert set(s.get_installation_paths(workspace / "extension.so")) == {workspace / "extension.so"}
        assert set(s.get_installation_paths(workspace)) == {
            workspace / "extension.so",
            workspace / "misc",
            workspace / "subdir/file.py",
        }


def _up_to_date_message(project_name: str) -> str:
    return f'package up to date: "{with_underscores(project_name)}"'


def _rebuilt_message(project_name: str) -> str:
    return f'rebuilt and loaded package "{with_underscores(project_name)}"'


def _uninstall(project_name: str) -> None:
    log.info("uninstalling %s", project_name)
    subprocess.check_call([sys.executable, "-m", "pip", "uninstall", "--disable-pip-version-check", "-y", project_name])


def _install_editable(project_dir: Path) -> None:
    """Install the given project to the virtualenv in editable mode."""
    log.info("installing %s in editable/unpacked mode", project_dir.name)
    env = os.environ.copy()
    env["VIRTUAL_ENV"] = sys.exec_prefix
    subprocess.check_call(["maturin", "develop"], cwd=project_dir, env=env)
    # TODO(matt): remove once maturin develop creates editable installs
    fix_direct_url(project_dir, with_underscores(project_dir.name))


def _install_non_editable(project_dir: Path) -> None:
    log.info("installing %s in non-editable mode", project_dir.name)
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", str(project_dir)])


def _is_installed_as_pth(project_name: str) -> bool:
    package_name = with_underscores(project_name)
    return any((Path(path) / f"{package_name}.pth").exists() for path in site.getsitepackages())


def _is_installed_editable_with_direct_url(project_name: str, project_dir: Path) -> bool:
    package_name = with_underscores(project_name)
    for path in site.getsitepackages():
        linked_path, is_editable = _load_dist_info(Path(path), package_name)
        if linked_path == project_dir:
            if not is_editable:
                log.info('project "%s" is installed but not in editable mode', project_name)
            return is_editable
        elif linked_path is not None:
            log.info('found linked path "%s" for project "%s". Expected "%s"', linked_path, project_name, project_dir)
            return False
    return False


def _is_editable_installed_correctly(project_name: str, project_dir: Path, is_mixed: bool) -> bool:
    log.info("checking if %s is installed correctly", project_name)
    installed_as_pth = _is_installed_as_pth(project_name)
    installed_editable_with_direct_url = _is_installed_editable_with_direct_url(project_name, project_dir)
    log.info(
        "is_mixed=%s, installed_as_pth=%s installed_editable_with_direct_url=%s",
        is_mixed,
        installed_as_pth,
        installed_editable_with_direct_url,
    )

    proc = subprocess.run(
        [sys.executable, "-m", "pip", "show", "--disable-pip-version-check", "-f", project_name],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    output = "None" if proc.stdout is None else proc.stdout.decode()
    log.info("pip output (returned %s):\n%s", proc.returncode, output)
    return installed_editable_with_direct_url and (installed_as_pth == is_mixed)


def _get_project_copy(project_dir: Path, output_path: Path) -> Path:
    for relative_path in _get_relative_files_tracked_by_git(project_dir):
        output_file_path = output_path / relative_path
        output_file_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(project_dir / relative_path, output_file_path)
    return output_path


def _get_relative_files_tracked_by_git(root: Path) -> Iterator[Path]:
    """This is used to ignore built artifacts to create a clean copy."""
    output = subprocess.check_output(["git", "ls-tree", "--name-only", "-z", "-r", "HEAD"], cwd=root)
    for relative_path_bytes in output.split(b"\x00"):
        relative_path = Path(os.fsdecode(relative_path_bytes))
        if (root / relative_path).is_file():
            yield relative_path


def _create_project_from_blank_template(project_name: str, output_path: Path, *, mixed: bool) -> Path:
    project_dir = _get_project_copy(helpers_dir / "blank-project", output_path)
    project_name = project_name.replace("_", "-")
    package_name = project_name.replace("-", "_")
    for path in [
        project_dir / "pyproject.toml",
        project_dir / "Cargo.toml",
        project_dir / "src/lib.rs",
    ]:
        path.write_text(path.read_text().replace("blank-project", project_name).replace("blank_project", package_name))
    if mixed:
        (project_dir / package_name).mkdir()
        (project_dir / package_name / "__init__.py").write_text(f"from .{package_name} import *")
    return project_dir
