# Basics

`maturin_import_hook` is a package that provides the capability for python `import` statements to trigger a rebuild
when importing a maturin project that is installed in editable mode (eg with `maturin develop` or `pip install -e`).
This makes development much more convenient as it brings the workflow of
developing Rust modules closer to the workflow of developing regular python modules.

The hook supports importing editable-installed pure Rust and mixed Rust/Python project
layouts as well as importing standalone `.rs` files.

## Installation

Install into a virtual environment then install so that the import hook is always active.

```shell
pip install maturin_import_hook
python -m maturin_import_hook site install  # install into the active environment
```

Alternatively, instead of using `site install`, put calls to `maturin_import_hook.install()` into any script where you
want to use the import hook.

## Usage

```python
import maturin_import_hook

# install the import hook with default settings.
# this must be called before importing any maturin project
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

The maturin project importer and the rust file importer can be used separately

```python
from maturin_import_hook import rust_file_importer
rust_file_importer.install()

from maturin_import_hook import project_importer
project_importer.install()
```

The import hook can be configured to control its behaviour

```python
import maturin_import_hook
from maturin_import_hook.settings import MaturinSettings

maturin_import_hook.install(
    enable_project_importer=True,
    enable_rs_file_importer=True,
    settings=MaturinSettings(
        release=True,
        strip=True,
        # ...
    ),
    show_warnings=True,
    # ...
)
```

Since the import hook is intended for use in development environments and not for
production environments, it may be a good idea to put the call to `maturin_import_hook.install()`
into `site-packages/sitecustomize.py` of your development virtual environment
([documentation](https://docs.python.org/3/library/site.html)). This will
enable the hook for every script run by that interpreter without calling `maturin_import_hook.install()`
in every script, meaning the scripts do not need alteration before deployment.

Installation into `sitecustomize.py` can be managed with the import hook cli using
`python -m maturin_import_hook site install`. The CLI can also manage uninstallation.

## CLI

The package provides a CLI interface for getting information such as the location and size of the build cache and
managing the installation into `sitecustomize.py`. For details, run:

```shell
python -m maturin_import_hook --help
```

## Environment Variables

The import hook can be disabled by setting `MATURIN_IMPORT_HOOK_ENABLED=0`. This can be used to disable
the import hook in production if you want to leave calls to `import_hook.install()` in place.

Build files will be stored in an appropriate place for the current system but can be overridden
by setting `MATURIN_BUILD_DIR`. These files can be deleted without causing any issues (unless a build is in progress).
The precedence for storing build files is:

- `MATURIN_BUILD_DIR`
- `<virtualenv_dir>/maturin_build_cache`
- `<system_cache_dir>/maturin_build_cache`
    - e.g. `~/.cache/maturin_build_cache` on POSIX

See the location being used with the CLI: `python -m maturin_import_hook cache info`

## Logging

By default the `maturin_import_hook` logger does not propagate to the root logger. This is so that `INFO` level messages
are shown to the user without them having to configure logging (`INFO` level is normally not visible). The import hook
also has extensive `DEBUG` level logging that generally would be more noise than useful. So by not propagating, `DEBUG`
messages from the import hook are not shown even if the root logger has `DEBUG` level visible.

If you prefer, `maturin_import_hook.reset_logger()` can be called to undo the default configuration and propagate
the messages as normal.

When debugging issues with the import hook, you should first call `reset_logger()` then configure the root logger
to show `DEBUG` messages. You can also run with the environment variable `RUST_LOG=maturin=debug` to get more
information from maturin.

```python
import logging
logging.basicConfig(format='%(name)s [%(levelname)s] %(message)s', level=logging.DEBUG)
import maturin_import_hook
maturin_import_hook.reset_logger()
maturin_import_hook.install()
```
