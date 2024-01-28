# Maturin Import Hook

A python import hook to automatically rebuild [maturin](https://www.maturin.rs/) projects and import stand-alone rust files.

Using this import hook reduces friction when developing mixed python/rust codebases because changes made to rust
components take effect automatically like changes to python components do.


## Usage
After installing `maturin`, install into a python virtual environment with:
```
$ pip install maturin_import_hook
```

Then put the following at the top of each python script you want to activate the import hook for
```python
import maturin_import_hook
maturin_import_hook.install()
```

Or alternatively, put those lines in [sitecustomize.py](https://docs.python.org/3/library/site.html#module-sitecustomize)
to activate the import hook for every script run in the virtual environment.

Once the hook is active, any `import` statement that imports an editable-installed maturin project will be
automatically rebuilt if necessary before it is imported.


## License

Licensed under either of:

 * Apache License, Version 2.0, ([LICENSE-APACHE](https://github.com/PyO3/maturin-import-hook/blob/main/license-apache) or http://www.apache.org/licenses/LICENSE-2.0)
 * MIT license ([LICENSE-MIT](https://github.com/PyO3/maturin-import-hook/blob/main/license-mit) or http://opensource.org/licenses/MIT)

at your option.
