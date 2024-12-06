import hashlib
import logging
import os
import platform
import re
import subprocess
import time
from pathlib import Path

import pytest

from maturin_import_hook._building import BuildCache, BuildStatus, Freshness, get_installation_freshness
from maturin_import_hook._resolve_project import _ProjectResolveError, _resolve_project, _TomlFile
from maturin_import_hook.error import ImportHookError
from maturin_import_hook.project_importer import _load_dist_info, _uri_to_path
from maturin_import_hook.settings import MaturinSettings

from .common import (
    TEST_CRATES_DIR,
    ResolvedPackage,
    capture_logs,
    get_file_times,
    get_string_between,
    map_optional,
    resolved_packages,
    set_file_times,
)

log = logging.getLogger(__name__)


def test_maturin_unchanged() -> None:
    """if new options have been added to maturin then the import hook needs to be updated to match"""
    env = {"PATH": os.environ["PATH"], "COLUMNS": "120"}

    build_help = subprocess.check_output("stty rows 50 cols 120; maturin build --help", shell=True, env=env)  # noqa: S602
    assert hashlib.sha1(build_help).hexdigest() == "ea47a884c90c9376047687aed98ab1dca29b433a"

    develop_help = subprocess.check_output("stty rows 50 cols 120; maturin develop --help", shell=True, env=env)  # noqa: S602
    assert hashlib.sha1(develop_help).hexdigest() == "ad7036a829c6801224933d589b1f9848678c9458"


def test_settings() -> None:
    assert MaturinSettings().to_args("develop") == []
    assert MaturinSettings().to_args("build") == []

    settings = MaturinSettings(
        release=True,
        strip=True,
        quiet=True,
        jobs=1,
        profile="profile1",
        features=["feature1", "feature2"],
        all_features=True,
        no_default_features=True,
        target="target1",
        ignore_rust_version=True,
        color=True,
        frozen=True,
        locked=True,
        offline=True,
        config={"key1": "value1", "key2": "value2"},
        unstable_flags=["unstable1", "unstable2"],
        verbose=2,
        rustc_flags=["flag1", "--flag2"],
    )
    # fmt: off
    expected_args = [
        "--release",
        "--strip",
        "--quiet",
        "--jobs", "1",
        "--profile", "profile1",
        "--features", "feature1,feature2",
        "--all-features",
        "--no-default-features",
        "--target", "target1",
        "--ignore-rust-version",
        "--color", "always",
        "--frozen",
        "--locked",
        "--offline",
        "--config", "key1=value1",
        "--config", "key2=value2",
        "-Z", "unstable1",
        "-Z", "unstable2",
        "-vv",
        "--",
        "flag1",
        "--flag2",
    ]
    # fmt: on
    assert settings.to_args("develop") == expected_args
    assert settings.to_args("build") == expected_args

    assert MaturinSettings.from_args(expected_args) == settings

    build_settings = MaturinSettings(auditwheel="skip", zig=True)
    expected_args = [
        "--auditwheel",
        "skip",
        "--zig",
    ]
    assert build_settings.to_args("build") == expected_args
    assert build_settings.to_args("develop") == []
    assert MaturinSettings.from_args(expected_args) == build_settings

    develop_settings = MaturinSettings(
        extras=["extra1", "extra2"],
        skip_install=True,
    )
    expected_args = [
        "--extras",
        "extra1,extra2",
        "--skip-install",
    ]
    assert develop_settings.to_args("develop") == expected_args
    assert develop_settings.to_args("build") == []
    assert MaturinSettings.from_args(expected_args) == develop_settings

    mixed_settings = MaturinSettings(
        color=True,
        extras=["extra1", "extra2"],
        skip_install=True,
        zig=True,
        rustc_flags=["flag1", "--flag2"],
    )
    assert mixed_settings.to_args("develop") == [
        "--color",
        "always",
        "--extras",
        "extra1,extra2",
        "--skip-install",
        "--",
        "flag1",
        "--flag2",
    ]
    assert mixed_settings.to_args("build") == [
        "--color",
        "always",
        "--zig",
        "--",
        "flag1",
        "--flag2",
    ]


class TestGetInstallationFreshness:
    def _build_status(self, mtime: float) -> BuildStatus:
        return BuildStatus(build_mtime=mtime, source_path=Path("/"), maturin_args=[], maturin_output="")

    def _build_status_for_file(self, path: Path) -> BuildStatus:
        return BuildStatus(build_mtime=path.stat().st_mtime, source_path=Path("/"), maturin_args=[], maturin_output="")

    def test_missing_installation(self, tmp_path: Path) -> None:
        (tmp_path / "source").touch()
        (tmp_path / "install").touch()
        s = self._build_status_for_file(tmp_path / "install")

        freshness = get_installation_freshness([tmp_path / "source"], [], s)
        assert freshness == Freshness(False, "no installed files found", None, None)

        with capture_logs() as cap:
            freshness = get_installation_freshness([tmp_path / "source"], [tmp_path / "missing"], s)
        assert freshness == Freshness(False, "failed to read installed files", None, None)
        expected_logs = (
            "error reading installed file mtimes: "
            f"FileNotFoundError(2, '{_file_not_found_message()}') ({tmp_path / 'missing'})\n"
        )
        assert cap.getvalue() == expected_logs

        with capture_logs() as cap:
            freshness = get_installation_freshness(
                [tmp_path / "source"], [tmp_path / "install", tmp_path / "missing"], s
            )
        assert freshness == Freshness(False, "failed to read installed files", None, None)
        assert cap.getvalue() == expected_logs

    def test_missing_source(self, tmp_path: Path) -> None:
        (tmp_path / "source").touch()
        (tmp_path / "install").touch()
        s = self._build_status_for_file(tmp_path / "install")

        with pytest.raises(ImportHookError, match="no source files found"):
            get_installation_freshness([], [tmp_path / "install"], s)

        expected_error = re.escape(
            "error reading source file mtimes: "
            f"FileNotFoundError(2, '{_file_not_found_message()}') ({tmp_path / 'missing'})"
        )
        with pytest.raises(ImportHookError, match=expected_error):
            get_installation_freshness([tmp_path / "missing"], [tmp_path / "install"], s)

        with pytest.raises(ImportHookError, match=expected_error):
            get_installation_freshness([tmp_path / "source", tmp_path / "missing"], [tmp_path / "install"], s)

    def test_mismatched_build_status(self, tmp_path: Path) -> None:
        (tmp_path / "source").touch()
        (tmp_path / "install").touch()
        s = self._build_status(0)

        freshness = get_installation_freshness([tmp_path / "source"], [tmp_path / "install"], s)
        assert freshness == Freshness(
            False, "installation mtime does not match build status mtime", tmp_path / "install", None
        )

        (tmp_path / "install_1").touch()
        (tmp_path / "install_2").touch()
        _set_strictly_ordered_mtimes([tmp_path / "install_1", tmp_path / "install_2", tmp_path / "source"])

        s = self._build_status_for_file(tmp_path / "install_2")

        freshness = get_installation_freshness(
            [tmp_path / "source"], [tmp_path / "install_1", tmp_path / "install_2"], s
        )
        assert freshness == Freshness(
            False, "installation mtime does not match build status mtime", tmp_path / "install_1", None
        )

    def test_read_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        unreadable_dir = tmp_path / "unreadable"
        unreadable_dir.mkdir()
        (unreadable_dir / "source").touch()

        (unreadable_dir / "install").touch()
        unreadable_status = self._build_status_for_file(unreadable_dir / "install")

        readable_dir = tmp_path / "readable"
        readable_dir.mkdir()

        (readable_dir / "install").touch()
        readable_status = self._build_status_for_file(readable_dir / "install")

        _mock_directory_as_unreadable(unreadable_dir, monkeypatch)

        with capture_logs() as cap:
            freshness = get_installation_freshness([], [unreadable_dir / "install"], unreadable_status)
        expected_logs = (
            "error reading installed file mtimes: "
            f"PermissionError(13, 'Permission denied') ({unreadable_dir / 'install'})\n"
        )
        assert cap.getvalue() == expected_logs
        assert freshness == Freshness(False, "failed to read installed files", None, None)

        expected_error = re.escape(
            f"error reading source file mtimes: PermissionError(13, 'Permission denied') ({unreadable_dir / 'source'})"
        )
        with pytest.raises(ImportHookError, match=expected_error):
            get_installation_freshness([unreadable_dir / "source"], [readable_dir / "install"], readable_status)

    def test_equal_mtime(self, tmp_path: Path) -> None:
        (tmp_path / "source").touch()
        (tmp_path / "install").touch()
        set_file_times(tmp_path / "source", get_file_times(tmp_path / "install"))
        s = self._build_status_for_file(tmp_path / "install")

        assert (tmp_path / "source").stat().st_mtime == (tmp_path / "install").stat().st_mtime

        freshness = get_installation_freshness([tmp_path / "source"], [tmp_path / "install"], s)
        assert freshness == Freshness(
            False, "installation may be out of date", tmp_path / "install", tmp_path / "source"
        )

    def test_simple_cases(self, tmp_path: Path) -> None:
        source_1 = tmp_path / "source_1"
        source_2 = tmp_path / "source_2"
        install_1 = tmp_path / "install_1"
        install_2 = tmp_path / "install_2"
        source_1.touch()
        source_2.touch()
        install_1.touch()
        install_2.touch()

        _set_strictly_ordered_mtimes([source_1, source_2, install_1, install_2])
        s = self._build_status_for_file(tmp_path / "install_1")
        freshness = get_installation_freshness([source_1, source_2], [install_1, install_2], s)
        assert freshness == Freshness(True, "", install_1, source_2)

        _set_strictly_ordered_mtimes([source_1, install_1, source_2, install_2])
        s = self._build_status_for_file(tmp_path / "install_1")
        freshness = get_installation_freshness([source_1, source_2], [install_1, install_2], s)
        assert freshness == Freshness(False, "installation is out of date", install_1, source_2)

        _set_strictly_ordered_mtimes([install_1, install_2, source_1, source_2])
        s = self._build_status_for_file(tmp_path / "install_1")
        freshness = get_installation_freshness([source_1, source_2], [install_1, install_2], s)
        assert freshness == Freshness(False, "installation is out of date", install_1, source_2)


def test_set_strictly_ordered_mtimes(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    c = tmp_path / "c"
    d = tmp_path / "d"
    a.touch()
    b.touch()
    c.touch()
    d.touch()
    _set_strictly_ordered_mtimes([a, c, b, d])
    assert a.stat().st_mtime < c.stat().st_mtime < b.stat().st_mtime < d.stat().st_mtime


def _set_strictly_ordered_mtimes(paths: list[Path]) -> None:
    atime, mtime = get_file_times(paths[0])
    for i, p in enumerate(reversed(paths)):
        set_file_times(p, (atime, mtime - i))


@pytest.mark.parametrize("project_name", sorted(resolved_packages().keys()))
def test_resolve_project(project_name: str) -> None:
    ground_truth = resolved_packages()[project_name]

    log.info("ground truth:")
    log.info(map_optional(ground_truth, lambda x: x.to_json()))

    project_dir = TEST_CRATES_DIR / project_name

    try:
        resolved = _resolve_project(project_dir)
    except _ProjectResolveError:
        calculated = None
    else:

        def _relative_path(path: Path) -> Path:
            return path.relative_to(project_dir)

        calculated = ResolvedPackage(
            cargo_manifest_path=_relative_path(resolved.cargo_manifest_path),
            python_dir=_relative_path(resolved.python_dir),
            python_module=map_optional(resolved.python_module, _relative_path),
            extension_module_dir=map_optional(resolved.extension_module_dir, _relative_path),
            module_full_name=resolved.module_full_name,
        )
    log.info("calculated:")
    log.info(map_optional(calculated, lambda x: x.to_json()))

    assert ground_truth == calculated


def test_build_cache(tmp_path: Path) -> None:
    cache = BuildCache(tmp_path / "build", lock_timeout_seconds=1)

    with cache.lock() as locked_cache:
        dir_1 = locked_cache.tmp_project_dir(tmp_path / "my_module", "my_module")
        dir_2 = locked_cache.tmp_project_dir(tmp_path / "other_place", "my_module")
        assert dir_1 != dir_2

        status1 = BuildStatus(1.2, tmp_path / "source1", ["arg1"], "output1")
        status2 = BuildStatus(1.2, tmp_path / "source2", ["arg2"], "output2")
        locked_cache.store_build_status(status1)
        locked_cache.store_build_status(status2)
        assert locked_cache.get_build_status(tmp_path / "source1") == status1
        assert locked_cache.get_build_status(tmp_path / "source2") == status2
        assert locked_cache.get_build_status(tmp_path / "source3") is None

        status1b = BuildStatus(1.3, tmp_path / "source1", ["arg1b"], "output1b")
        locked_cache.store_build_status(status1b)
        assert locked_cache.get_build_status(tmp_path / "source1") == status1b


def test_uri_to_path() -> None:
    if platform.system() == "Windows":
        assert _uri_to_path("file:///C:/abc/d%20e%20f") == Path(r"C:\abc\d e f")
    else:
        assert _uri_to_path("file:///abc/d%20e%20f") == Path("/abc/d e f")


def test_load_dist_info(tmp_path: Path) -> None:
    dist_info = tmp_path / "package_foo-1.0.0.dist-info"
    dist_info.mkdir(parents=True)
    if platform.system() == "Windows":
        uri = "file:///C:/some%20directory/foo"
        path = Path(r"C:\some directory\foo")
    else:
        uri = "file:///somewhere/some%20directory/foo"
        path = Path("/somewhere/some directory/foo")

    (dist_info / "direct_url.json").write_text('{"dir_info": {"editable": true}, "url": "' + uri + '"}')

    linked_path, is_editable = _load_dist_info(tmp_path, "package_foo", require_project_target=False)
    assert linked_path == path
    assert is_editable


def test_toml_file_loading(tmp_path: Path) -> None:
    toml_path = tmp_path / "my_file.toml"
    toml_path.write_text('[foo]\nbar = 12\nbaz = ["a"]')
    toml_file = _TomlFile.load(toml_path)
    assert toml_file.path == toml_path
    assert toml_file.data == {"foo": {"bar": 12, "baz": ["a"]}}


def test_toml_file(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)

    path = Path("/toml_path")
    toml_file = _TomlFile(path, {"foo": {"bar": 12, "baz": ["a"]}})

    with pytest.raises(AssertionError):
        toml_file.get_value([], int)

    assert toml_file.get_value(["foo"], dict) == {"bar": 12, "baz": ["a"]}
    assert toml_file.get_value(["foo", "bar"], int) == 12
    assert toml_file.get_value(["foo", "baz"], list) == ["a"]
    assert toml_file.get_value(["foo", "xyz"], int) is None

    assert caplog.messages == []

    assert toml_file.get_value(["foo", "bar"], str) is None
    assert caplog.messages == [f"failed to get str value at 'foo.bar' from toml file: '{path}'"]
    caplog.clear()

    assert toml_file.get_value(["foo", "bar", "xyz"], int) is None
    assert caplog.messages == [f"failed to get int value at 'foo.bar.xyz' from toml file: '{path}'"]
    caplog.clear()

    assert toml_file.get_value(["foo", "baz", "xyz"], int) is None
    assert caplog.messages == [f"failed to get int value at 'foo.baz.xyz' from toml file: '{path}'"]
    caplog.clear()


def test_get_string_between() -> None:
    assert get_string_between("11aaabbbccc11", "aaa", "ccc") == "bbb"
    assert get_string_between("11aaabbbccc11", "xxx", "ccc") is None
    assert get_string_between("11aaabbbccc11", "aaa", "xxx") is None
    assert get_string_between("11aaabbbccc11", "xxx", "xxx") is None


def test_set_file_times(tmp_path: Path) -> None:
    for _ in range(10):
        file_a = tmp_path / "a"
        file_a.touch()
        time.sleep(0.1)
        file_b = tmp_path / "b"
        file_b.touch()
        assert file_a.stat().st_mtime != file_b.stat().st_mtime
        times = get_file_times(file_a)
        set_file_times(file_b, times)
        assert file_a.stat().st_mtime == file_b.stat().st_mtime


def _mock_directory_as_unreadable(dir_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """cause PermissionError to be raised when calling `pathlib.Path.stat()` on a file inside the given directory

    on POSIX, could use `os.chmod(0o000)` but on Windows there is no easy mechanism for causing a PermissionError
    to be raised when accessing a file, so monkey patch instead
    """

    original_stat = Path.stat

    def patched_stat(self: Path) -> object:
        if Path.is_relative_to(self, dir_path):
            e = PermissionError(13, "Permission denied")
            e.filename = str(self)
            raise e
        return original_stat(self)

    monkeypatch.setattr(Path, "stat", patched_stat)

    with pytest.raises(PermissionError, match="Permission denied") as e_info:
        (dir_path / "abc").stat()
    assert e_info.value.errno == 13
    assert e_info.value.filename == str(dir_path / "abc")


def _file_not_found_message() -> str:
    return (
        "The system cannot find the file specified" if platform.system() == "Windows" else "No such file or directory"
    )
