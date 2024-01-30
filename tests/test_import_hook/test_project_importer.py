import logging
import os
import re
import shutil
import site
import subprocess
import sys
from pathlib import Path
from textwrap import dedent
from typing import Iterator

import pytest
from maturin_import_hook.project_importer import _load_dist_info

from .common import (
    IMPORT_HOOK_HEADER,
    TEST_CRATES_DIR,
    all_usable_test_crate_names,
    check_match,
    missing_entrypoint_error_message_pattern,
    mixed_test_crate_names,
    remove_ansii_escape_characters,
    run_concurrent_python,
    run_python,
    run_python_code,
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
    project_importer.install(install_new_packages=False)
    """)
    check_installed_path.write_text(f"{header}\n\n{check_installed_path.read_text()}")

    empty_dir = workspace / "empty"
    empty_dir.mkdir()

    output1, _ = run_python([str(check_installed_path)], cwd=empty_dir, expect_error=True, quiet=True)
    assert (
        f'package "{with_underscores(project_name)}" is not already '
        f"installed and install_new_packages=False. Not importing"
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
    header = dedent("""
    import sys
    import logging
    logging.basicConfig(format='%(name)s [%(levelname)s] %(message)s', level=logging.DEBUG)
    import maturin_import_hook
    maturin_import_hook.reset_logger()
    install_new_packages = len(sys.argv) > 1 and sys.argv[1] == 'INSTALL_NEW'
    print(f'{install_new_packages=}')
    maturin_import_hook.install(install_new_packages=install_new_packages)
    """)
    check_installed_path.write_text(f"{header}\n\n{check_installed_path.read_text()}")
    shutil.copy(check_installed_path, check_installed_outside_project)

    (project_dir / "src/lib.rs").write_text("")  # will break once rebuilt

    # when outside the project, can still detect non-editable installed projects via dist-info
    output1, _ = run_python(["check_installed.py"], cwd=check_installed_outside_project)
    assert "SUCCESS" in output1
    assert "install_new_packages=False" in output1
    assert f'found project linked by dist-info: "{project_dir}"' in output1
    assert "package not installed in editable-mode and install_new_packages=False. not rebuilding" in output1

    # when inside the project, will detect the project above
    output2, _ = run_python(["check_installed.py"], cwd=check_installed_dir)
    assert "SUCCESS" in output2
    assert "install_new_packages=False" in output2
    assert "found project above the search path:" in output2
    assert "package not installed in editable-mode and install_new_packages=False. not rebuilding" in output2

    output3, _ = run_python(
        ["check_installed.py", "INSTALL_NEW"],
        cwd=check_installed_outside_project,
        quiet=True,
        expect_error=True,
    )
    assert "SUCCESS" not in output3
    assert "install_new_packages=True" in output3
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


class TestLogging:
    """These tests ensure that the desired messages are visible to the user in the default logging configuration."""

    @staticmethod
    def _loader_script() -> str:
        return (helpers_dir / "logging_helper.py").read_text()

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

        output, _ = run_python_code(self._loader_script(), env={"PATH": ""})
        assert output == "building \"test_project\"\ncaught MaturinError('maturin not found')\n"

        extra_bin = workspace / "bin"
        extra_bin.mkdir()
        mock_maturin_path = extra_bin / "maturin"
        mock_maturin_path.write_text('#!/usr/bin/env bash\necho "maturin 0.1.2"')
        mock_maturin_path.chmod(0o777)

        output, _ = run_python_code(self._loader_script(), env={"PATH": f"{extra_bin}:/usr/bin"})
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

        output, _ = run_python_code(self._loader_script())
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

        run_python_code(self._loader_script())  # run once to rebuild

        output, _ = run_python_code(self._loader_script())
        assert output == "value 10\nSUCCESS\n"

    @pytest.mark.parametrize("is_mixed", [False, True])
    def test_default_compile_error(self, workspace: Path, is_mixed: bool) -> None:
        """If compilation fails then the error message from maturin is printed and an ImportError is raised."""
        project_dir = self._create_clean_project(workspace / "project", is_mixed)

        lib_path = project_dir / "src/lib.rs"
        lib_path.write_text(lib_path.read_text().replace("Ok(())", ""))

        output, _ = run_python_code(self._loader_script())
        pattern = (
            'building "test_project"\n'
            'maturin_import_hook \\[ERROR\\] command ".*" returned non-zero exit status: 1\n'
            "maturin_import_hook \\[ERROR\\] maturin output:\n"
            ".*"
            "expected `Result<\\(\\), PyErr>`, found `\\(\\)`"
            ".*"
            "maturin failed"
            ".*"
            "caught ImportError: Failed to build package with maturin\n"
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

        output1, _ = run_python_code(self._loader_script())
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

        output2, _ = run_python_code(self._loader_script())
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

    @pytest.mark.parametrize("is_mixed", [False, True])
    def test_reset_logger_without_configuring(self, workspace: Path, is_mixed: bool) -> None:
        """If reset_logger is called then by default logging level INFO is not printed
        (because the messages are handled by the root logger).
        """
        self._create_clean_project(workspace / "project", is_mixed)
        output, _ = run_python_code(self._loader_script(), args=["RESET_LOGGER"])
        assert output == "value 10\nSUCCESS\n"

    @pytest.mark.parametrize("is_mixed", [False, True])
    def test_successful_compilation_but_not_valid(self, workspace: Path, is_mixed: bool) -> None:
        """If the project compiles but does not import correctly an ImportError is raised."""
        project_dir = self._create_clean_project(workspace / "project", is_mixed)
        lib_path = project_dir / "src/lib.rs"
        lib_path.write_text(lib_path.read_text().replace("test_project", "test_project_new_name"))

        output, _ = run_python_code(self._loader_script(), quiet=True)
        pattern = (
            'building "test_project"\n'
            'rebuilt and loaded package "test_project" in [0-9.]+s\n'
            f"caught ImportError: {missing_entrypoint_error_message_pattern('test_project')}\n"
        )
        check_match(output, pattern, flags=re.MULTILINE)


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
    subprocess.check_call(["maturin", "develop"], cwd=project_dir, env=env)  # noqa: S607


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
    output = subprocess.check_output(["git", "ls-tree", "--name-only", "-z", "-r", "HEAD"], cwd=root)  # noqa: S607
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
