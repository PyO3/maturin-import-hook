# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)

## [Unreleased]

## [0.3.0]

- fix issue with running scripts installed on Windows (can handle non-directories in `sys.path`)
  [#21](https://github.com/PyO3/maturin-import-hook/issues/21)
  [#23](https://github.com/PyO3/maturin-import-hook/pull/23)
- improve handling of TOML parsing errors [#20](https://github.com/PyO3/maturin-import-hook/pull/20)
- raise minimum maturin to 1.8.4 [#22](https://github.com/PyO3/maturin-import-hook/pull/22)
    - remove `--detect-uv` argument to `maturin_import_hook site install` because maturin now automatically detects uv
    environments

## [0.2.0]

- many improvements to `maturin_import_hook site install` [#11](https://github.com/PyO3/maturin-import-hook/pull/11)
    - `--args="..."` to specify which arguments should be used with maturin when installing into `sitecustomize.py`
    - automatically detect if `--uv` should be used
    - enable/disable project or rs file importer with
      `--project-importer/--no-project-importer` and `--rs-file-importer/--no-rs-file-importer`
- ignore directories with `.maturin_hook_ignore` file marker and ignore `.py` files by default [#10](https://github.com/PyO3/maturin-import-hook/pull/10)
- caching and optimisation to greatly reduce overhead when searching for maturin packages [#8](https://github.com/PyO3/maturin-import-hook/pull/8)
- support clearing cache with `importlib.invalidate_caches()` [#8](https://github.com/PyO3/maturin-import-hook/pull/8)
- upgrade to support maturin 1.7.8 [#9](https://github.com/PyO3/maturin-import-hook/pull/9)
- option to install into usercustomize [#5](https://github.com/PyO3/maturin-import-hook/pull/5)
- `maturin_import_hook site install --force` option to overwrite previous installation [#5](https://github.com/PyO3/maturin-import-hook/pull/5)
- ignore `ImportError` in `sitecustomize.py` (in case user uninstalls `maturin_import_hook`) [#5](https://github.com/PyO3/maturin-import-hook/pull/5)

## [0.1.0]

Initial release of the import hook.

[Unreleased]: https://github.com/pyo3/maturin-import-hook/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/pyo3/maturin-import-hook/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/pyo3/maturin-import-hook/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/pyo3/maturin-import-hook/compare/c2689735a61a322998f7304a113b7c74b8108ab3...v0.1.0
