import re
import shutil
from pathlib import Path
from typing import Tuple

from .common import (
    check_match,
    missing_entrypoint_error_message_pattern,
    remove_ansii_escape_characters,
    run_concurrent_python,
    run_python,
)

"""
These tests ensure the correct functioning of the rust file importer import hook.
The tests are intended to be run as part of the tests in `run.rs`
which provides a clean virtual environment for these tests to use.
"""

script_dir = Path(__file__).parent.resolve()
helpers_dir = script_dir / "file_importer_helpers"


def test_absolute_import(workspace: Path) -> None:
    """Test imports of the form `import ab.cd.ef`."""
    helper_path = helpers_dir / "absolute_import_helper.py"

    output1, duration1 = run_python([str(helper_path)], cwd=workspace)
    assert "SUCCESS" in output1
    assert "module up to date" not in output1
    assert "creating project for" in output1

    output2, duration2 = run_python([str(helper_path)], cwd=workspace)
    assert "SUCCESS" in output2
    assert "module up to date" in output2
    assert "creating project for" not in output2

    assert duration2 < duration1


def test_relative_import() -> None:
    """Test imports of the form `from .ab import cd`."""
    output1, duration1 = run_python(["-m", f"{helpers_dir.name}.relative_import_helper"], cwd=script_dir)
    assert "SUCCESS" in output1
    assert "module up to date" not in output1
    assert "creating project for" in output1

    output2, duration2 = run_python(["-m", f"{helpers_dir.name}.relative_import_helper"], cwd=script_dir)
    assert "SUCCESS" in output2
    assert "module up to date" in output2
    assert "creating project for" not in output2

    assert duration2 < duration1


def test_top_level_import(workspace: Path) -> None:
    """Test imports of the form `import ab`."""
    helper_path = helpers_dir / "packages/top_level_import_helper.py"

    output1, duration1 = run_python([str(helper_path)], cwd=workspace)
    assert "SUCCESS" in output1
    assert "module up to date" not in output1
    assert "creating project for" in output1

    output2, duration2 = run_python([str(helper_path)], cwd=workspace)
    assert "SUCCESS" in output2
    assert "module up to date" in output2
    assert "creating project for" not in output2

    assert duration2 < duration1


def test_multiple_imports(workspace: Path) -> None:
    """Test importing the same rs file multiple times by different names in the same session."""
    helper_path = helpers_dir / "multiple_import_helper.py"

    output, _ = run_python([str(helper_path)], cwd=workspace)
    assert "SUCCESS" in output
    assert 'rebuilt and loaded module "packages.subpackage.my_rust_module"' in output
    assert output.count("importing rust file") == 1


def test_concurrent_import() -> None:
    """Test multiple processes attempting to import the same modules at the same time."""
    args = {
        "args": [f"{helpers_dir.name}/concurrent_import_helper.py"],
        "cwd": script_dir,
        "quiet": True,
    }

    outputs = run_concurrent_python(3, run_python, args)

    assert all(o.success for o in outputs)

    num_compilations = 0
    num_up_to_date = 0
    num_waiting = 0
    for output in outputs:
        assert "SUCCESS" in output.output
        assert "importing rust file" in output.output
        if "waiting on lock" in output.output:
            num_waiting += 1
        if "creating project for" in output.output:
            num_compilations += 1
        if "module up to date" in output.output:
            num_up_to_date += 1

    assert num_compilations == 1
    assert num_up_to_date == 2
    assert num_waiting == 2


def test_rebuild_on_change(workspace: Path) -> None:
    """Test that modules are rebuilt if they are edited."""
    script_path = workspace / "my_script.rs"
    helper_path = shutil.copy(helpers_dir / "rebuild_on_change_helper.py", workspace)

    shutil.copy(helpers_dir / "my_script_1.rs", script_path)

    output1, _ = run_python([str(helper_path)], cwd=workspace)
    assert "get_num = 10" in output1
    assert "failed to import get_other_num" in output1
    assert "SUCCESS" in output1

    assert "module up to date" not in output1
    assert "creating project for" in output1

    shutil.copy(helpers_dir / "my_script_2.rs", script_path)

    output2, _ = run_python([str(helper_path)], cwd=workspace)
    assert "get_num = 20" in output2
    assert "get_other_num = 100" in output2
    assert "SUCCESS" in output2

    assert "module up to date" not in output2
    assert "creating project for" in output2


def test_rebuild_on_settings_change(workspace: Path) -> None:
    """Test that modules are rebuilt if the settings (eg maturin flags) used by the import hook changes."""
    script_path = workspace / "my_script.rs"
    helper_path = workspace / "helper.py"
    helper_path.write_text(
        (helpers_dir / "rebuild_on_settings_change_helper.py").read_text().replace("PROJECT_NAME", "my_script")
    )

    shutil.copy(helpers_dir / "my_script_3.rs", script_path)

    output1, _ = run_python([str(helper_path)], cwd=workspace)
    assert "get_num = 10" in output1
    assert "SUCCESS" in output1
    assert "building with default settings" in output1
    assert "module up to date" not in output1
    assert "creating project for" in output1

    output2, _ = run_python([str(helper_path)], cwd=workspace)
    assert "get_num = 10" in output2
    assert "SUCCESS" in output2
    assert "module up to date" in output2

    output3, _ = run_python([str(helper_path), "LARGE_NUMBER"], cwd=workspace)
    assert "building with large_number feature enabled" in output3
    assert "module up to date" not in output3
    assert "creating project for" in output3
    assert "get_num = 100" in output3
    assert "SUCCESS" in output3

    output4, _ = run_python([str(helper_path), "LARGE_NUMBER"], cwd=workspace)
    assert "building with large_number feature enabled" in output4
    assert "module up to date" in output4
    assert "get_num = 100" in output4
    assert "SUCCESS" in output4


class TestLogging:
    """test the desired messages are visible to the user in the default logging configuration."""

    def _create_clean_package(self, package_path: Path) -> Tuple[Path, Path]:
        package_path.mkdir()
        rs_path = Path(shutil.copy(helpers_dir / "my_script_1.rs", package_path / "my_script.rs"))
        py_path = Path(shutil.copy(helpers_dir / "logging_helper.py", package_path / "loader.py"))
        return rs_path, py_path

    def test_maturin_detection(self, workspace: Path) -> None:
        rs_path, py_path = self._create_clean_package(workspace / "package")

        output, _ = run_python([str(py_path)], workspace, env={"PATH": ""})
        assert output == "building \"my_script\"\ncaught MaturinError('maturin not found')\n"

        extra_bin = workspace / "bin"
        extra_bin.mkdir()
        mock_maturin_path = extra_bin / "maturin"
        mock_maturin_path.write_text('#!/usr/bin/env bash\necho "maturin 0.1.2"')
        mock_maturin_path.chmod(0o777)

        output, _ = run_python([str(py_path)], workspace, env={"PATH": f"{extra_bin}:/usr/bin"})
        assert output == (
            'building "my_script"\n'
            "caught MaturinError('unsupported maturin version: (0, 1, 2). "
            "Import hook requires >=(1, 4, 0),<(2, 0, 0)')\n"
        )

    def test_default_rebuild(self, workspace: Path) -> None:
        """By default, when a module is out of date the import hook logs messages
        before and after rebuilding but hides the underlying details.
        """
        rs_path, py_path = self._create_clean_package(workspace / "package")

        output, _ = run_python([str(py_path)], workspace)
        pattern = 'building "my_script"\nrebuilt and loaded module "my_script" in [0-9.]+s\nget_num 10\nSUCCESS\n'
        check_match(output, pattern, flags=re.MULTILINE)

    def test_default_up_to_date(self, workspace: Path) -> None:
        """By default, when the module is up-to-date nothing is printed."""
        rs_path, py_path = self._create_clean_package(workspace / "package")

        run_python([str(py_path)], workspace)  # run once to rebuild

        output, _ = run_python([str(py_path)], workspace)
        assert output == "get_num 10\nSUCCESS\n"

    def test_default_compile_error(self, workspace: Path) -> None:
        """If compilation fails then the error message from maturin is printed and an ImportError is raised."""
        rs_path, py_path = self._create_clean_package(workspace / "package")

        rs_path.write_text(rs_path.read_text().replace("10", ""))
        output, _ = run_python([str(py_path)], workspace, quiet=True)
        pattern = (
            'building "my_script"\n'
            'maturin_import_hook \\[ERROR\\] command ".*" returned non-zero exit status: 1\n'
            "maturin_import_hook \\[ERROR\\] maturin output:\n"
            ".*"
            "expected `usize`, found `\\(\\)`"
            ".*"
            "maturin failed"
            ".*"
            "caught MaturinError\\('Failed to build wheel with maturin'\\)\n"
        )
        check_match(output, pattern, flags=re.MULTILINE | re.DOTALL)

    def test_default_compile_warning(self, workspace: Path) -> None:
        """If compilation succeeds with warnings then the output of maturin is printed.
        If the module is already up to date but warnings were raised when it was first
        built, the warnings will be printed again.
        """
        rs_path, py_path = self._create_clean_package(workspace / "package")
        rs_path.write_text(rs_path.read_text().replace("10", "#[warn(unused_variables)]{let x = 12;}; 20"))

        output1, _ = run_python([str(py_path)], workspace)
        output1 = remove_ansii_escape_characters(output1)
        pattern = (
            'building "my_script"\n'
            'maturin_import_hook \\[WARNING\\] build of "my_script" succeeded with warnings:\n'
            ".*"
            "warning: unused variable: `x`"
            ".*"
            'rebuilt and loaded module "my_script" in [0-9.]+s\n'
            "get_num 20\n"
            "SUCCESS\n"
        )
        check_match(output1, pattern, flags=re.MULTILINE | re.DOTALL)

        output2, _ = run_python([str(py_path)], workspace)
        output2 = remove_ansii_escape_characters(output2)
        pattern = (
            'maturin_import_hook \\[WARNING\\] the last build of "my_script" succeeded with warnings:\n'
            ".*"
            "warning: unused variable: `x`"
            ".*"
            "get_num 20\n"
            "SUCCESS\n"
        )
        check_match(output2, pattern, flags=re.MULTILINE | re.DOTALL)

    def test_reset_logger_without_configuring(self, workspace: Path) -> None:
        """If reset_logger is called then by default logging level INFO is not printed
        (because the messages are handled by the root logger).
        """
        rs_path, py_path = self._create_clean_package(workspace / "package")
        output, _ = run_python([str(py_path), "RESET_LOGGER"], workspace)
        assert output == "get_num 10\nSUCCESS\n"

    def test_successful_compilation_but_not_valid(self, workspace: Path) -> None:
        """If the script compiles but does not import correctly an ImportError is raised."""
        rs_path, py_path = self._create_clean_package(workspace / "package")
        rs_path.write_text(rs_path.read_text().replace("my_script", "my_script_new_name"))
        output, _ = run_python([str(py_path)], workspace, quiet=True)
        pattern = (
            'building "my_script"\n'
            'rebuilt and loaded module "my_script" in [0-9.]+s\n'
            f"caught ImportError\\('{missing_entrypoint_error_message_pattern('my_script')}'\\)\n"
        )
        check_match(output, pattern, flags=re.MULTILINE)
