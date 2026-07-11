import itertools
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, TypeVar

from maturin_import_hook._logging import logger

_T = TypeVar("_T")


class _TomlFile:
    def __init__(self, path: Path, data: dict[Any, Any]) -> None:
        self.path = path
        self.data = data

    @staticmethod
    def load(path: Path) -> "_TomlFile":
        with path.open("rb") as f:
            data = tomllib.load(f)
        return _TomlFile(path, data)

    @staticmethod
    def from_string(path: Path, data_str: str) -> "_TomlFile":
        return _TomlFile(path, tomllib.loads(data_str))

    def get_value_or_default(self, keys: list[str], required_type: type[_T], default: _T) -> _T:
        value = self.get_value(keys, required_type)
        return default if value is None else value

    def get_value(self, keys: list[str], required_type: type[_T]) -> _T | None:
        assert keys
        current_data: Any = self.data
        num_keys = len(keys)
        parent_invalid = False
        for i, key in enumerate(keys):
            current_data = current_data.get(key)
            if current_data is None:
                return None
            elif i < num_keys - 1 and not isinstance(current_data, dict):
                parent_invalid = True
                break

        if parent_invalid or not isinstance(current_data, required_type):
            logger.error(
                "failed to get %s value at '%s' from toml file: '%s'",
                required_type.__name__,
                ".".join(keys),
                self.path,
            )
            return None
        else:
            return current_data


def find_cargo_manifest(project_dir: Path) -> Path | None:
    pyproject_path = project_dir / "pyproject.toml"
    if pyproject_path.is_file():
        pyproject_data = pyproject_path.read_text()
        if "manifest-path" in pyproject_data:
            try:
                pyproject = _TomlFile.from_string(pyproject_path, pyproject_data)
            except tomllib.TOMLDecodeError:
                logger.info("failed to parse '%s' as TOML", pyproject_path)
                return None
            relative_manifest_path = pyproject.get_value(["tool", "maturin", "manifest-path"], str)
            if relative_manifest_path is not None:
                return project_dir / relative_manifest_path

    manifest_path = project_dir / "Cargo.toml"
    if manifest_path.is_file():
        return manifest_path
    manifest_path = project_dir / "rust/Cargo.toml"
    if manifest_path.is_file():
        return manifest_path
    return None


def is_maybe_maturin_project(directory: Path) -> bool:
    """note: this function does not check if this really is a maturin project for simplicity."""
    return (directory / "pyproject.toml").is_file() and find_cargo_manifest(directory) is not None


class ProjectResolver:
    def __init__(self) -> None:
        self._resolved_project_cache: dict[Path, MaturinProject | None] = {}

    def clear_cache(self) -> None:
        self._resolved_project_cache.clear()

    def resolve(self, project_dir: Path) -> Optional["MaturinProject"]:
        if project_dir not in self._resolved_project_cache:
            resolved = None
            try:
                resolved = _resolve_project(project_dir)
            except _ProjectResolveError as e:
                logger.info('failed to resolve project "%s": %s', project_dir, e)
            self._resolved_project_cache[project_dir] = resolved
        else:
            resolved = self._resolved_project_cache[project_dir]
        return resolved


@dataclass
class MaturinProject:
    cargo_manifest_path: Path
    # the name of the compiled extension module without any suffix
    # (i.e. "some_package.my_module" instead of "some_package/my_module.cpython-311-x86_64-linux-gnu")
    module_full_name: str
    # the root of the python part of the project (or the project root if there is none)
    python_dir: Path
    # the path to the top level python package if the project is mixed
    python_module: Path | None
    # the location that the compiled extension module is written to when installed in editable/unpacked mode
    # None for bin projects (no compiled extension module)
    extension_module_dir: Path | None
    # path dependencies listed in the Cargo.toml of the main project
    immediate_path_dependencies: list[Path]
    # the maturin bindings type
    bindings: str
    # the names of binaries produced by this project (for bindings="bin" projects)
    binary_names: list[str]
    # all path dependencies including transitive dependencies
    _all_path_dependencies: list[Path] | None = None

    @property
    def package_name(self) -> str:
        return self.module_full_name.split(".")[0]

    @property
    def module_name(self) -> str:
        return self.module_full_name.split(".")[-1]

    @property
    def is_mixed(self) -> bool:
        """Whether the project installs a .pth file (has a Python module directory alongside Rust code)."""
        return self.extension_module_dir is not None or (self.bindings == "bin" and self.python_module is not None)

    @property
    def all_path_dependencies(self) -> list[Path]:
        if self._all_path_dependencies is None:
            self._all_path_dependencies = _find_all_path_dependencies(self.immediate_path_dependencies)
        return self._all_path_dependencies


def _find_all_path_dependencies(immediate_path_dependencies: list[Path]) -> list[Path]:
    if not immediate_path_dependencies:
        return []
    all_path_dependencies: set[Path] = set()
    to_search = immediate_path_dependencies.copy()
    while to_search:
        dependency_project_dir = to_search.pop()
        if dependency_project_dir in all_path_dependencies:
            continue
        all_path_dependencies.add(dependency_project_dir)
        manifest_path = dependency_project_dir / "Cargo.toml"
        if manifest_path.exists():
            try:
                cargo = _TomlFile.load(manifest_path)
            except tomllib.TOMLDecodeError:
                logger.info("failed to parse '%s' as TOML", manifest_path)
            else:
                to_search.extend(_get_immediate_path_dependencies(dependency_project_dir, cargo))
    return sorted(all_path_dependencies)


class _ProjectResolveError(Exception):
    pass


def _resolve_project(project_dir: Path) -> MaturinProject:
    """This follows the same logic as project_layout.rs.

    module_writer::write_bindings_module() is the function that copies the extension file to `rust_module / so_filename`
    """
    pyproject_path = project_dir / "pyproject.toml"
    if not pyproject_path.exists():
        msg = "no pyproject.toml found"
        raise _ProjectResolveError(msg)
    try:
        pyproject = _TomlFile.load(pyproject_path)
    except tomllib.TOMLDecodeError as e:
        msg = f"pyproject.toml failed to parse as TOML: {e!r}"
        raise _ProjectResolveError(msg) from None

    if not _is_valid_pyproject(pyproject):
        msg = "pyproject.toml is invalid (does not have required fields)"
        raise _ProjectResolveError(msg)

    manifest_path = find_cargo_manifest(project_dir)
    if manifest_path is None:
        msg = "no Cargo.toml found"
        raise _ProjectResolveError(msg)
    try:
        cargo = _TomlFile.load(manifest_path)
    except tomllib.TOMLDecodeError as e:
        msg = f"Cargo.toml failed to parse as TOML: {e!r}"
        raise _ProjectResolveError(msg) from None

    module_full_name = _resolve_module_name(pyproject, cargo)
    if module_full_name is None:
        msg = "could not resolve module_full_name"
        raise _ProjectResolveError(msg)

    bindings = _resolve_bindings(pyproject, cargo)
    binary_names = _resolve_binary_names(cargo, manifest_path.parent) if bindings == "bin" else []

    python_dir = _resolve_py_root(project_dir, pyproject)
    python_source_explicit = pyproject.get_value(["tool", "maturin", "python-source"], str) is not None

    extension_module_dir: Path | None
    python_module: Path | None
    python_module, extension_module_dir, _extension_module_name = _resolve_rust_module(python_dir, module_full_name)
    immediate_path_dependencies = _get_immediate_path_dependencies(manifest_path.parent, cargo)

    if not python_module.exists():
        if python_source_explicit:
            msg = f"python-source '{python_dir}' does not contain module '{module_full_name}'"
            raise _ProjectResolveError(msg)
        extension_module_dir = None
        python_module = None

    return MaturinProject(
        cargo_manifest_path=manifest_path,
        module_full_name=module_full_name,
        python_dir=python_dir,
        python_module=python_module,
        extension_module_dir=extension_module_dir,
        immediate_path_dependencies=immediate_path_dependencies,
        bindings=bindings,
        binary_names=binary_names,
    )


def _is_valid_pyproject(pyproject: _TomlFile) -> bool:
    """in maturin serde is used to load into a `PyProjectToml` struct.
    This function should match whether the toml would parse correctly"""
    # it should be sufficient to check the required fields rather than match the serde parsing logic exactly
    return pyproject.get_value(["build-system", "requires"], list) is not None


def _resolve_rust_module(python_dir: Path, module_name: str) -> tuple[Path, Path, str]:
    """This follows the same logic as project_layout.rs (ProjectLayout::determine).

    rust_module is the directory that the extension library gets written to when the package is
    installed in editable mode
    """
    parts = module_name.split(".")
    if len(parts) > 1:
        python_module = python_dir / parts[0]
        extension_module_dir = python_dir / Path(*parts[:-1])
        extension_module_name = parts[-1]
    else:
        python_module = python_dir / module_name
        extension_module_dir = python_dir / module_name
        extension_module_name = module_name
    return python_module, extension_module_dir, extension_module_name


def _resolve_module_name(pyproject: _TomlFile, cargo: _TomlFile) -> str | None:
    """This follows the same logic as project_layout.rs (ProjectResolver::resolve).

    Precedence:
     * Explicitly declared pyproject.toml `tool.maturin.module-name`
     * Cargo.toml `lib.name`
     * pyproject.toml `project.name`
     * Cargo.toml `package.name`

    """
    module_name = pyproject.get_value(["tool", "maturin", "module-name"], str)
    if module_name is not None:
        return module_name
    module_name = cargo.get_value(["lib", "name"], str)
    if module_name is not None:
        return module_name
    module_name = pyproject.get_value(["project", "name"], str)
    if module_name is not None:
        return module_name
    return cargo.get_value(["package", "name"], str)


def _get_immediate_path_dependencies(manifest_dir_path: Path, cargo: _TomlFile) -> list[Path]:
    path_dependencies: list[Path] = []
    for dependency in cargo.get_value_or_default(["dependencies"], dict, {}).values():
        if isinstance(dependency, dict):
            relative_path: Any = dependency.get("path", None)
            if relative_path is not None and isinstance(relative_path, str):
                path_dependencies.append((manifest_dir_path / relative_path).resolve())
    return path_dependencies


def has_experimental_inspect(project_dir: Path) -> bool:
    """Check if the project has the pyo3 `experimental-inspect` feature enabled.

    This feature enables automatic stub generation when combined with
    `maturin develop --generate-stubs`.
    """
    manifest_path = find_cargo_manifest(project_dir)
    if manifest_path is None:
        return False
    try:
        cargo = _TomlFile.load(manifest_path)
    except tomllib.TOMLDecodeError:
        logger.info("failed to parse '%s' as TOML", manifest_path)
        return False

    cargo_deps = cargo.get_value_or_default(["dependencies"], dict, {})
    pyo3_dep = cargo_deps.get("pyo3")
    if isinstance(pyo3_dep, dict):
        features: Any = pyo3_dep.get("features", [])
        if isinstance(features, list) and "experimental-inspect" in features:
            return True
    return False


def _resolve_py_root(project_dir: Path, pyproject: _TomlFile) -> Path:
    """This follows the same logic as project_layout.rs."""
    py_root = pyproject.get_value(["tool", "maturin", "python-source"], str)
    if py_root is not None:
        return project_dir / py_root
    project_name = pyproject.get_value(["project", "name"], str)
    if project_name is None:
        return project_dir

    rust_cargo_toml_found = (project_dir / "rust/Cargo.toml").exists()

    python_packages = pyproject.get_value_or_default(["tool", "maturin", "python-packages"], list, [])

    package_name = project_name.replace("-", "_")
    python_src_found = any(
        (project_dir / p / "__init__.py").is_file() for p in itertools.chain((f"src/{package_name}/",), python_packages)
    )
    if rust_cargo_toml_found and python_src_found:
        return project_dir / "src"
    else:
        return project_dir


def _resolve_bindings(pyproject: _TomlFile, cargo: _TomlFile) -> str:
    """Resolve the maturin bindings type.

    Matches maturin's bridge detection logic (bridge/detection.rs):
    """
    bindings = pyproject.get_value(["tool", "maturin", "bindings"], str)
    if bindings is not None:
        return bindings

    cargo_deps = cargo.get_value_or_default(["dependencies"], dict, {})

    if "pyo3" in cargo_deps or "pyo3-ffi" in cargo_deps:
        return "pyo3"

    if "uniffi" in cargo_deps:
        return "uniffi"

    lib = cargo.get_value(["lib"], dict)
    if lib is not None:
        crate_types = lib.get("crate-type")
        if isinstance(crate_types, list) and "cdylib" in crate_types:
            return "cffi"

    bins = cargo.get_value(["bin"], list)
    if bins is not None and len(bins) > 0:
        return "bin"

    return "pyo3"


def _resolve_binary_names(cargo: _TomlFile, project_dir: Path) -> list[str]:
    """Resolve binary names from Cargo.toml."""
    bins = cargo.get_value(["bin"], list)
    if bins is not None:
        return [
            str(bin_entry.get("name"))
            for bin_entry in bins
            if isinstance(bin_entry, dict) and bin_entry.get("name") is not None
        ]

    binary_names: list[str] = []
    src_dir = project_dir / "src"
    if (src_dir / "main.rs").is_file():
        package_name = cargo.get_value(["package", "name"], str)
        if package_name is not None:
            binary_names.append(package_name)
    bin_dir = src_dir / "bin"
    if bin_dir.is_dir():
        binary_names.extend(
            rs_file.stem for rs_file in bin_dir.iterdir() if rs_file.is_file() and rs_file.suffix == ".rs"
        )

    if binary_names:
        return sorted(binary_names)

    package_name = cargo.get_value(["package", "name"], str)
    if package_name is not None:
        return [package_name]
    return []
