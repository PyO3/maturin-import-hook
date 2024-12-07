import os
import platform
import re
import shutil
import sys
from pathlib import Path
from textwrap import dedent

import pytest

from .common import (
    RELOAD_SUPPORTED,
    check_match,
    create_echo_script,
    get_file_times,
    get_string_between,
    missing_entrypoint_error_message_pattern,
    remove_ansii_escape_characters,
    remove_executable_from_path,
    run_concurrent_python,
    run_python,
    set_file_times,
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

    if platform.system() == "Windows" and platform.python_implementation() == "PyPy":
        # workaround for https://github.com/pypy/pypy/issues/4917
        args["interpreter"] = Path(sys.executable)

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


def test_low_resolution_mtime(workspace: Path) -> None:
    """test that the import hook works if the mtime of the filesystem has a low resolution
    (making the exact ordering of the extension module vs the source code ambiguous)

    by calling touch() in a loop, one can measure the time taken before the mtime of the written file changes.
    Writes that are close together may not update the mtime. On my Linux system I found that tmpfs and ext4 filesystems
    updated after ~3ms which is enough time to perform hundreds of small writes. On other/older filesystems the interval
    can be as large as a few seconds so this edge case is worth considering.

    If the extension module and source code have the same mtime then either one could be the last written to so a
    rebuild should be triggered. This rebuild should update the mtime so that the ambiguity is no longer present even
    if the content of the extension module is up to date and therefore not modified.
    """
    script_path = workspace / "my_script.rs"
    helper_path = shutil.copy(helpers_dir / "low_resolution_mtime_helper.py", workspace)

    shutil.copy(helpers_dir / "my_script_1.rs", script_path)

    output1, _ = run_python([str(helper_path)], cwd=workspace)
    assert 'building "my_script"' in output1
    assert 'module "my_script" will be rebuilt because: already built module not found' in output1
    assert "get_num = 10" in output1
    assert "SUCCESS" in output1

    extension_path = Path((workspace / "extension_path.txt").read_text())

    # the script is modified but assigned an mtime equal to the extension module. This simulates an edit being
    # made shortly after the extension module is built. The time window for this problem to occur varies
    # depending on the filesystem. The problem can also occur in reverse (built immediately after an edit) but this
    # is less likely since building takes significant time.
    shutil.copy(helpers_dir / "my_script_2.rs", script_path)
    times = get_file_times(extension_path)
    set_file_times(script_path, times)
    set_file_times(extension_path, times)

    output2, _ = run_python([str(helper_path)], cwd=workspace)
    assert 'building "my_script"' in output2
    assert 'module "my_script" will be rebuilt because: installation may be out of date' in output2
    assert "get_num = 20" in output2
    assert "SUCCESS" in output2

    # this time, the mtimes are identical but nothing has changed. A rebuild should be triggered and even if the
    # extension module is unchanged the mtime of the extension module should be updated to prevent any more
    # unnecessary rebuilds
    times = get_file_times(extension_path)
    set_file_times(script_path, times)
    set_file_times(extension_path, times)

    output3, _ = run_python([str(helper_path)], cwd=workspace)
    assert 'building "my_script"' in output3
    assert 'module "my_script" will be rebuilt because: installation may be out of date' in output3
    assert "get_num = 20" in output3
    assert "SUCCESS" in output3

    extension_stat = extension_path.stat()
    source_code_stat = script_path.stat()
    # extension mtime should be strictly greater to remove the ambiguity about which is newer
    assert source_code_stat.st_mtime < extension_stat.st_mtime

    output4, _ = run_python([str(helper_path)], cwd=workspace)
    assert 'building "my_script"' not in output4
    assert "get_num = 20" in output4
    assert "SUCCESS" in output4


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


@pytest.mark.skipif(not RELOAD_SUPPORTED, reason="reload not supported")
class TestReload:
    """test that importlib.reload() can be used to reload modules imported by the import hook

    The tests are organised to strike a balance between having many tests for individual behaviours and bundling
    checks together to reduce the time spent compiling.

    see docs/reloading.md for details
    """

    @staticmethod
    def _create_reload_module(output_dir: Path) -> Path:
        (output_dir / "__init__.py").touch()
        module_path = output_dir / "my_module.rs"
        shutil.copy(helpers_dir / "reload_template.rs", module_path)
        shutil.copy(helpers_dir / "reload_helper.py", output_dir / "reload_helper.py")
        other_module = dedent("""\
        import my_module
        from my_module import get_num
        import logging

        def get_twice_num_direct():
            return get_num() * 2

        def get_twice_num_indirect():
            return my_module.get_num() * 2

        logging.info('other module initialised')
        """)
        (output_dir / "other_module.py").write_text(other_module)

        return module_path

    def test_basic_reload(self, workspace: Path) -> None:
        """test several properties of reloading rs-file modules with the import hook active

        - import styles
            - `import ...` style import
                - top level
                - extension module
            - `from ... import ...` style import
                - top level
                - extension module
            - duplicate reference to module
        - module initialisation
        - classes
            - types becoming incompatible after reloading
        - calling reload
            - after making changes
            - after no changes
        """
        module_path = self._create_reload_module(workspace)
        output, _ = run_python(
            [str(helpers_dir / "reload_helper.py"), str(module_path), "_test_basic_reload"], cwd=workspace
        )
        info = "\n".join(line for line in output.splitlines() if "[INFO]" in line)

        assert "SUCCESS" in output

        e = re.escape

        # This checks that the INFO level logs are exactly these messages (with nothing in between).
        # This verifies that rebuilds and module initialisation are behaving as expected
        expected_info_parts = [
            e("reload_helper [INFO] initial import start"),
            e('maturin_import_hook [INFO] building "my_module"'),  # because: no build status
            'maturin_import_hook \\[INFO\\] rebuilt and loaded module "my_module" in [0-9.]+s',
            e("root [INFO] my_module extension module initialised"),
            e("reload_helper [INFO] initial import finish"),
            e("root [INFO] comparing Integer instances a and b"),
            e("root [INFO] comparing Integer instances a and c"),
            e("reload_helper [INFO] modifying module"),
            e("reload_helper [INFO] reload 1 start"),
            e('maturin_import_hook [INFO] building "my_module"'),
            'maturin_import_hook \\[INFO\\] rebuilt and loaded module "my_module" in [0-9.]+s',
            e("root [INFO] my_module extension module initialised"),
            e("reload_helper [INFO] reload 1 finish"),
            e("root [INFO] comparing Integer instances d and e"),
            e("reload_helper [INFO] reload 2 start"),
            e(
                "root [INFO] my_module extension module initialised"
            ),  # note: this is different from the package importer
            e("reload_helper [INFO] reload 2 finish"),
            e("reload_helper [INFO] modifying module"),
            e("reload_helper [INFO] reload 3 start"),
            e('maturin_import_hook [INFO] building "my_module"'),
            'maturin_import_hook \\[INFO\\] rebuilt and loaded module "my_module" in [0-9.]+s',
            e("root [INFO] my_module extension module initialised"),
            e("reload_helper [INFO] reload 3 finish"),
            e("reload_helper [INFO] SUCCESS"),
        ]
        expected_info_pattern = "\n".join(line for line in expected_info_parts if line)
        check_match(info, expected_info_pattern, flags=re.MULTILINE)

        # these checks ensure that the internals of the import hook are performing the expected actions
        initial_import = get_string_between(output, "initial import start", "initial import finish")
        assert initial_import is not None
        assert 'MaturinRustFileImporter searching for "my_module"\n' in initial_import
        assert 'building "my_module"' in initial_import

        assert 'handling reload of "my_module"' not in initial_import

        reload_1 = get_string_between(output, "reload 1 start", "reload 1 finish")
        assert reload_1 is not None
        assert 'MaturinRustFileImporter searching for "my_module" (reload)' in reload_1
        assert 'building "my_module"' in reload_1
        assert 'handling reload of "my_module"' in reload_1

        assert 'module up to date: "my_module"' not in reload_1

        reload_2 = get_string_between(output, "reload 2 start", "reload 2 finish")
        assert reload_2 is not None
        assert 'MaturinRustFileImporter searching for "my_module" (reload)' in reload_2
        assert 'module up to date: "my_module"' in reload_2
        assert 'handling reload of "my_module"' in reload_2

        assert 'building "my_module"' not in reload_2

        reload_3 = get_string_between(output, "reload 3 start", "reload 3 finish")
        assert reload_3 is not None
        assert 'MaturinRustFileImporter searching for "my_module" (reload)' in reload_3
        assert 'building "my_module"' in reload_3
        assert 'handling reload of "my_module"' in reload_3

        assert 'module up to date: "my_module"' not in reload_3

        assert "maturin_import_hook [DEBUG] removing temporary directory" in output

    def test_globals(self, workspace: Path) -> None:
        """tests properties of global variables initialised in python and rust modules when the package is reloaded

        - module types:
            - root module
            - extension module
            - python module
        - properties tested
            - __file__
            - adding new global
            - global initialised once
            - modifying mutable global
            - assigning to immutable global
            - global data of the extension not the PyModule
        - reload without changes
        - reload with changes changes
        """
        module_path = self._create_reload_module(workspace)
        output, _ = run_python(
            [str(helpers_dir / "reload_helper.py"), str(module_path), "_test_globals"], cwd=workspace
        )
        info = "\n".join(line for line in output.splitlines() if "[INFO]" in line)

        assert "SUCCESS" in output

        e = re.escape

        expected_info_parts = [
            e("reload_helper [INFO] initial import start"),
            e('maturin_import_hook [INFO] building "my_module"'),
            'maturin_import_hook \\[INFO\\] rebuilt and loaded module "my_module" in [0-9.]+s',
            e("root [INFO] my_module extension module initialised"),
            e("reload_helper [INFO] initial import finish"),
            e("reload_helper [INFO] checking extension module"),
            e("reload_helper [INFO] reload 1 start"),
            e("root [INFO] my_module extension module initialised"),
            e("reload_helper [INFO] reload 2 finish"),
            e("reload_helper [INFO] checking extension module"),
            e("reload_helper [INFO] modifying module"),
            e("reload_helper [INFO] reload 2 start"),
            e('maturin_import_hook [INFO] building "my_module"'),
            'maturin_import_hook \\[INFO\\] rebuilt and loaded module "my_module" in [0-9.]+s',
            e("root [INFO] my_module extension module initialised"),
            e("reload_helper [INFO] reload 2 finish"),
            e("reload_helper [INFO] checking extension module"),
            e("reload_helper [INFO] SUCCESS"),
        ]
        expected_info_pattern = "\n".join(line for line in expected_info_parts if line)
        check_match(info, expected_info_pattern, flags=re.MULTILINE)

    def test_other_module(self, workspace: Path) -> None:
        module_path = self._create_reload_module(workspace)
        output, _ = run_python(
            [str(helpers_dir / "reload_helper.py"), str(module_path), "_test_other_module"], cwd=workspace
        )

        assert "SUCCESS" in output

        e = re.escape

        expected_parts = [
            e("reload_helper [INFO] initial import start"),
            e('maturin_import_hook [DEBUG] module "my_module" will be rebuilt because: already built module not found'),
            e("root [INFO] my_module extension module initialised"),
            e("root [INFO] other module initialised"),
            e("reload_helper [INFO] initial import finish"),
            e("reload_helper [INFO] modifying module"),
            e("reload_helper [INFO] reload 1 start"),
            e('maturin_import_hook [INFO] building "my_module"'),
            e('maturin_import_hook [DEBUG] handling reload of "my_module"'),
            'maturin_import_hook \\[INFO\\] rebuilt and loaded module "my_module" in [0-9.]+s',
            e("root [INFO] my_module extension module initialised"),
            e("reload_helper [INFO] reload 1 finish"),
            e("reload_helper [INFO] reload 2 start"),
            e("root [INFO] other module initialised"),
            e("reload_helper [INFO] reload 2 finish"),
            e("reload_helper [INFO] SUCCESS"),
            e("maturin_import_hook [DEBUG] removing temporary directory"),
            "",  # end with anything
        ]
        expected_pattern = ".*".join(part for part in expected_parts)
        check_match(output, expected_pattern, flags=re.MULTILINE | re.DOTALL)

    def test_reload_without_import_hook(self, workspace: Path) -> None:
        """test when reload is used without support from the import hook"""
        module_path = self._create_reload_module(workspace)
        output, _ = run_python(
            [str(helpers_dir / "reload_helper.py"), str(module_path), "_test_reload_without_import_hook"], cwd=workspace
        )
        assert "SUCCESS" in output

        e = re.escape

        expected_parts = [
            e("reload_helper [INFO] initial import start"),
            e("reload_helper [INFO] module not found"),
            e("reload_helper [INFO] installing import hook"),
            e('module "my_module" will be rebuilt because: already built module not found'),
            e("root [INFO] my_module extension module initialised"),
            e("reload_helper [INFO] initial import finish"),
            e("reload_helper [INFO] reload module start"),
            e('maturin_import_hook [DEBUG] module "my_module" is already loaded and enable_reloading=False'),
            e("reload_helper [INFO] reload module finish"),
            e("reload_helper [INFO] modifying module"),
            e("reload_helper [INFO] reload module start"),
            e('maturin_import_hook [DEBUG] module "my_module" is already loaded and enable_reloading=False'),
            e("reload_helper [INFO] reload module finish"),
            e("reload_helper [INFO] uninstalling import hook"),
            e("reload_helper [INFO] reload module start"),
            e("reload_helper [INFO] module not found"),
            e("reload_helper [INFO] reload module finish"),
            e("reload_helper [INFO] SUCCESS\n"),
        ]
        expected_pattern = ".*".join(part for part in expected_parts)
        check_match(output, expected_pattern, flags=re.MULTILINE | re.DOTALL)

    def test_compilation_error(self, workspace: Path) -> None:
        module_path = self._create_reload_module(workspace)
        output, _ = run_python(
            [str(helpers_dir / "reload_helper.py"), str(module_path), "_test_compilation_error"], cwd=workspace
        )
        assert "SUCCESS" in output

        e = re.escape

        expected_parts = [
            e("reload_helper [INFO] initial import start"),
            e('maturin_import_hook [DEBUG] MaturinRustFileImporter searching for "my_module"'),
            e('maturin_import_hook [INFO] building "my_module"'),
            'maturin_import_hook \\[INFO\\] rebuilt and loaded module "my_module" in [0-9.]+s',
            e("root [INFO] my_module extension module initialised"),
            e("reload_helper [INFO] initial import finish"),
            e("reload_helper [INFO] modifying module"),
            e("reload_helper [INFO] reload start"),
            e('maturin_import_hook [DEBUG] MaturinRustFileImporter searching for "my_module" (reload)'),
            e('maturin_import_hook [INFO] building "my_module"'),
            e("expected expression, found `;`"),
            e("maturin failed"),
            e("reload_helper [INFO] reload failed"),
            e("reload_helper [INFO] reload finish"),
            e("reload_helper [INFO] modifying module"),
            e("reload_helper [INFO] reload start"),
            e('maturin_import_hook [DEBUG] MaturinRustFileImporter searching for "my_module" (reload)'),
            e('maturin_import_hook [INFO] building "my_module"'),
            'maturin_import_hook \\[INFO\\] rebuilt and loaded module "my_module" in [0-9.]+s',
            e("root [INFO] my_module extension module initialised"),
            e("reload_helper [INFO] reload finish"),
            e("reload_helper [INFO] SUCCESS"),
            e("maturin_import_hook [DEBUG] removing temporary directory"),
            "",  # end with anything
        ]
        expected_pattern = ".*".join(line for line in expected_parts)
        check_match(output, expected_pattern, flags=re.MULTILINE | re.DOTALL)

    def test_pickling(self, workspace: Path) -> None:
        """test the classes that can be pickled behave as expected when the module is reloaded"""
        module_path = self._create_reload_module(workspace)
        output, _ = run_python(
            [str(helpers_dir / "reload_helper.py"), str(module_path), "_test_pickling"], cwd=workspace
        )

        assert "SUCCESS" in output

        e = re.escape

        expected_parts = [
            e("reload_helper [INFO] initial import start"),
            e('maturin_import_hook [DEBUG] MaturinRustFileImporter searching for "my_module"'),
            e('maturin_import_hook [INFO] building "my_module"'),
            'maturin_import_hook \\[INFO\\] rebuilt and loaded module "my_module" in [0-9.]+s',
            e("root [INFO] my_module extension module initialised"),
            e("reload_helper [INFO] initial import finish"),
            e("reload_helper [INFO] modifying module"),
            e("reload_helper [INFO] reload start"),
            e('maturin_import_hook [DEBUG] MaturinRustFileImporter searching for "my_module" (reload)'),
            e('maturin_import_hook [INFO] building "my_module"'),
            'maturin_import_hook \\[INFO\\] rebuilt and loaded module "my_module" in [0-9.]+s',
            e("root [INFO] my_module extension module initialised"),
            e("reload_helper [INFO] reload finish"),
            e("reload_helper [INFO] SUCCESS"),
            e("maturin_import_hook [DEBUG] removing temporary directory"),
            "",  # end with anything
        ]
        expected_pattern = ".*".join(line for line in expected_parts)
        check_match(output, expected_pattern, flags=re.MULTILINE | re.DOTALL)

    def test_submodule(self, workspace: Path) -> None:
        module_path = self._create_reload_module(workspace)
        output, _ = run_python(
            [str(helpers_dir / "reload_helper.py"), str(module_path), "_test_submodule"], cwd=workspace
        )

        assert "SUCCESS" in output

        e = re.escape

        expected_parts = [
            e("reload_helper [INFO] initial import start"),
            e('maturin_import_hook [DEBUG] MaturinRustFileImporter searching for "my_module"'),
            e('maturin_import_hook [INFO] building "my_module"'),
            'maturin_import_hook \\[INFO\\] rebuilt and loaded module "my_module" in [0-9.]+s',
            e("root [INFO] my_module extension module initialised"),
            e("reload_helper [INFO] initial import finish"),
            e("reload_helper [INFO] modifying module"),
            e("reload_helper [INFO] reload start"),
            e('maturin_import_hook [DEBUG] MaturinRustFileImporter searching for "my_module" (reload)'),
            e('maturin_import_hook [INFO] building "my_module"'),
            'maturin_import_hook \\[INFO\\] rebuilt and loaded module "my_module" in [0-9.]+s',
            e("root [INFO] my_module extension module initialised"),
            e("reload_helper [INFO] reload finish"),
            e("reload_helper [INFO] reload start"),
            e("reload_helper [INFO] reload failed"),
            e("reload_helper [INFO] reload finish"),
            e("reload_helper [INFO] SUCCESS"),
            e("maturin_import_hook [DEBUG] removing temporary directory"),
            "",  # end with anything
        ]
        expected_pattern = ".*".join(line for line in expected_parts)
        check_match(output, expected_pattern, flags=re.MULTILINE | re.DOTALL)


class TestLogging:
    """test the desired messages are visible to the user in the default logging configuration."""

    def _create_clean_package(self, package_path: Path, *, reload_helper: bool = False) -> tuple[Path, Path]:
        package_path.mkdir()
        rs_path = Path(shutil.copy(helpers_dir / "my_script_1.rs", package_path / "my_script.rs"))
        if reload_helper:
            py_path = Path(shutil.copy(helpers_dir / "logging_reload_helper.py", package_path / "reload_helper.py"))
        else:
            py_path = Path(shutil.copy(helpers_dir / "logging_helper.py", package_path / "helper.py"))
        return rs_path, py_path

    def test_maturin_detection(self, workspace: Path) -> None:
        _rs_path, py_path = self._create_clean_package(workspace / "package")

        env = os.environ.copy()
        env["PATH"] = remove_executable_from_path(env["PATH"], "maturin")

        output, _ = run_python([str(py_path)], workspace, env=env)
        assert output == "building \"my_script\"\ncaught MaturinError('maturin not found')\n"

        extra_bin = workspace / "bin"
        extra_bin.mkdir()
        create_echo_script(extra_bin / "maturin", "maturin 0.1.2")

        env["PATH"] = f"{extra_bin}{os.pathsep}{env['PATH']}"

        output, _ = run_python([str(py_path)], workspace, env=env)
        assert output == (
            'building "my_script"\n'
            "caught MaturinError('unsupported maturin version: (0, 1, 2). "
            "Import hook requires >=(1, 5, 0),<(2, 0, 0)')\n"
        )

    def test_default_rebuild(self, workspace: Path) -> None:
        """By default, when a module is out of date the import hook logs messages
        before and after rebuilding but hides the underlying details.
        """
        _rs_path, py_path = self._create_clean_package(workspace / "package")

        output, _ = run_python([str(py_path)], workspace)
        pattern = 'building "my_script"\nrebuilt and loaded module "my_script" in [0-9.]+s\nget_num 10\nSUCCESS\n'
        check_match(output, pattern, flags=re.MULTILINE)

    def test_default_up_to_date(self, workspace: Path) -> None:
        """By default, when the module is up-to-date nothing is printed."""
        _rs_path, py_path = self._create_clean_package(workspace / "package")

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

    @pytest.mark.skipif(not RELOAD_SUPPORTED, reason="reload not supported")
    def test_reload(self, workspace: Path) -> None:
        _rs_path, py_path = self._create_clean_package(workspace / "package", reload_helper=True)

        output1, _ = run_python([str(py_path)], workspace)
        output1 = remove_ansii_escape_characters(output1)
        pattern = (
            "initial import start\n"
            'building "my_script"\n'
            'rebuilt and loaded module "my_script" in [0-9.]+s\n'
            "initial import finish\n"
            "reload start\n"
            'building "my_script"\n'
            'rebuilt and loaded module "my_script" in [0-9.]+s\n'
            "reload finish\n"
            "reload start\n"
            "reload finish\n"
            "get_num 10\n"
            "SUCCESS\n"
        )
        check_match(output1, pattern, flags=re.MULTILINE | re.DOTALL)

    def test_reset_logger_without_configuring(self, workspace: Path) -> None:
        """If reset_logger is called then by default logging level INFO is not printed
        (because the messages are handled by the root logger).
        """
        _rs_path, py_path = self._create_clean_package(workspace / "package")
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
