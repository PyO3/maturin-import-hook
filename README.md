# Maturin Import Hook

A python import hook to automatically rebuild [maturin](https://www.maturin.rs/) projects and import stand-alone rust files.

Using this import hook reduces friction when developing mixed python/rust codebases because changes made to rust
components take effect automatically like changes to python components do.

The import hook also provides conveniences such as
[importlib.reload()](https://docs.python.org/3/library/importlib.html#importlib.reload) support for maturin projects.

## Usage

After installing `maturin`, install the import hook into a python virtual environment with:

```shell
pip install maturin_import_hook
```

Then, optionally make the hook activate automatically with:

```shell
python -m maturin_import_hook site install
```

This only has to be run once for each virtual environment.

Alternatively, put the following at the top of each python script where you want to use the hook:

```python
import maturin_import_hook

# install the import hook with default settings.
# this call must be before any imports that you want the hook to be active for.
maturin_import_hook.install()

# when a rust package that is installed in editable mode is imported,
# that package will be automatically recompiled if necessary.
import pyo3_pure

# when a .rs file is imported a project will be created for it in the
# maturin build cache and the resulting library will be loaded.
#
# assuming subpackage/my_rust_script.rs defines a pyo3 module:
import subpackage.my_rust_script
```

Once the hook is active, any `import` statement that imports an editable-installed maturin project will be
automatically rebuilt if necessary before it is imported.

## CLI

The package provides a CLI interface for getting information such as the location and size of the build cache and
managing the installation into `sitecustomize.py`. For details, run:

```shell
python -m maturin_import_hook --help
```

## Debugging

If you encounter a problem, a good way to learn more about what is going on is to enable the debug logging for the
import hook. This can be done by putting the following lines above the import that is causing an issue:

```python
# configure logging if you haven't already (make sure DEBUG level is visible)
logging.basicConfig(format="%(name)s [%(levelname)s] %(message)s", level=logging.DEBUG)
maturin_import_hook.reset_logger()

import some_package
```

## License

Licensed under either of:

- Apache License, Version 2.0, ([LICENSE-APACHE](https://github.com/PyO3/maturin-import-hook/blob/main/license-apache)
  or <http://www.apache.org/licenses/LICENSE-2.0>)
- MIT license ([LICENSE-MIT](https://github.com/PyO3/maturin-import-hook/blob/main/license-mit)
  or <http://opensource.org/licenses/MIT>)

at your option.
