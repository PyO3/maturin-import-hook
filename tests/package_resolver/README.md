# Package Resolver

This is a small utility to use the internal maturin package resolving code to compare with the python implementation
in the import hook.

from this directory, run

```shell
cargo run -- ../maturin ../resolved.json
cargo clean
```

to update `resolved.json`
