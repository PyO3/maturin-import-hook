# Testing

These tests ensure that the import hook behaves correctly when installing a wide variety of different crates.

The recommended way to run the tests is to run:

```bash
git submodule update --init ./maturin
python runner.py
```

This script will create an isolated environment for the tests to run in
and produce a html report from the test results (see `runner.py --help` for options).

The tests will use maturin from pypi with the version specified in the requirements.txt file.

## Alternatives

The tests can be run from any python environment with the necessary requirements but may leave temporary files
on your system and other unwanted side-effects:

- the tests install and uninstall packages from the current python virtual environment
- the maturin import hook build cache for the current virtual environment will be cleared and used by the test
    - you can set the `MATURIN_BUILD_DIR` environment variable to control this
- the tests build crates and therefore create a `target/` directory
    - you can set the `CARGO_TARGET_DIR` variable to control this
- if `test_import_hook.common.CLEAR_WORKSPACE = False` then the temporary files used during the test are not deleted

To ensure even more isolation than the runner script, you can use [act](https://github.com/nektos/act) to run the CI
of this repository locally.

## Maintenance

To update maturin:

- update the submodule to the maturin commit you want to update to
- re-run the `package_resolver` to update `resolved.json` (see `package_resolver/README.md` for instructions)
- update `requirements.txt` to match the packages and versions installed by the maturin ci
  (see `pip` and `uv` commands in `maturin/.github/workflows/test.yml`)
    - check the `uniffi` package version listed in the `Cargo.toml` of any of the `uniffi-*`
      test crates and update `uniffi-bindgen` in `requirements.txt` to match.
- check that no crates have been added to `test-crates` that should be excluded from the import hook tests.
  If so, add them to `IGNORED_TEST_CRATES` in `common.py`
- upgrade the test packages in `test_import_hook/*_helpers`
    - check what version of `pyo3` is used by the command: `maturin new --bindings pyo3 test_project`
- update the version check in the import hook to ensure it allows using the new version
- run the tests to ensure everything still works

Released versions of the import hook should use a tagged version of maturin, but during development, in order
to use a specific maturin commit:

- use `maturin @ git+https://github.com/PyO3/maturin@xxxxxxx` in `requirements.txt`
    - where `xxxxxxx` is a git commit hash
- `export MATURIN_SETUP_ARGS="--features=scaffolding"` before running `runner.py`
    - the `setup.py` of `maturin` uses this environment variable when building from source. the `scaffolding` feature
      is required for `maturin new`.
    - Make sure a cached built version of this commit is not available because `uv` doesn't know about the
      environment variable. run `uv cache clean maturin` to remove any cached versions.

## Notes

### Debugging

The tests can be tricky to debug because they require spawning one or more python instances. One option is to use
remote debugging with [debugpy](https://pypi.org/project/debugpy/).

First, install debugpy into the test virtualenv:

```shell
test_workspace/venv/bin/python -m pip install debugpy
```

Add the following line in a helper script that you want to debug, or inside `maturin_import_hook` itself

```python
import debugpy; debugpy.listen(5678); debugpy.wait_for_client(); debugpy.breakpoint()
```

Run the test you are interested in, either directly or using the `test_runner` script

Connect to the debugger, eg [using vscode](https://code.visualstudio.com/docs/python/debugging#_local-script-debugging)

Note: set `CLEAR_WORKSPACE = False` in `common.py` if you want to prevent the temporary files generated during the test
from being cleared.

### Benchmarking

The `create_benchmark_data.py` script creates a directory with many python packages to represent a worst case scenario.
Run the script then run `venv/bin/python run.py` from the created directory.

One way of obtaining profiling information is to run:

```sh
venv/bin/python -m cProfile -o profile.prof run.py
pyprof2calltree -i profile.prof -o profile.log
kcachegrind profile.log
```

### Caching

sccache is a tool for caching build artifacts to speed up compilation. Unfortunately, it is currently useless for these
tests as it [cannot cache cdylib crates](https://github.com/mozilla/sccache/issues/1715)

To run with sccache anyway (to check if the situation has improved):

```bash
sccache --stop-server  # so the tests use a separate empty sccache
# sccache cannot cache incremental compilation, so disable it: https://github.com/mozilla/sccache/issues/236
RUSTC_WRAPPER=sccache SCCACHE_DIR=/tmp/sccache CARGO_INCREMENTAL=0 python test_runner/test_runner.py <args>
sccache --show-stats
```

### Faster Linking

You can use `lld` for linking using the `--lld` argument to the test runner. This usually provides a speed increase
but not a huge one as linking is not a huge bottleneck in the testing.

### Profiling

you can run the test runner with the `--profile <path>` argument to run the tests with `cProfile`. The majority of the
total time is spent waiting for subprocesses and isn't very interesting.

To collect profiling stats for a particular invocation of `run_python()` inside a test, use the `profile=<path>`
argument to that function.

You can view cprofile results using [pyprof2calltree](https://pypi.org/project/pyprof2calltree/) or
[flameprof](https://pypi.org/project/flameprof/).
