# Contributing
Thank you for your interest in contributing to maturin_import_hook. All are welcome! Please consider reading
the [Code of Conduct](https://github.com/PyO3/maturin-import-hook/blob/main/Code-of-Conduct.md) to keep our community
positive and inclusive.

## Getting Started Contributing
- we use the [github issue tracker](https://github.com/PyO3/maturin-import-hook/issues) and [discussions](https://github.com/PyO3/maturin-import-hook/discussions) to keep track of bugs and ideas

### Setting up a development environment

1. Install rust (eg using `rustup`) and maturin (eg `pipx install maturin`)
2. Clone the repository
    - if you are looking to submit a PR, create a fork on github and clone your fork
3. Install [pre-commit](https://pre-commit.com/) (eg `pipx install pre-commit`) then run `pre-commit install` in the repo root
4. See [tests/README.md](https://github.com/PyO3/maturin-import-hook/blob/main/tests/README.md) for instructions on how best to run the test suite and some other miscellaneous instructions for debugging and carrying out maintenance tasks.

Tips:
- [pyenv](https://github.com/pyenv/pyenv) may be useful for installing a specific python interpreter
- Virtual machines such as [VirtualBox](https://www.virtualbox.org/) and [OSX-KVM](https://github.com/kholia/OSX-KVM) are useful for running tests on specific platforms locally. Or you can use the test pipeline on github actions.

## Writing Pull Requests

### Continuous Integration
The maturin_import_hook repo uses [GitHub actions](https://github.com/PyO3/maturin-import-hook/actions). PRs are blocked from merging if the CI is not successful.

You can run the test pipeline on your fork from the 'Actions' tab of your fork. The pipeline takes several arguments when run manually that you can use to narrow down what is run so you can iterate faster when trying to solve a particular issue. The pipeline uploads the test results as html reports that can be downloaded and examined. This is generally easier than sifting through the raw logs.

Linting and type-checking is enforced in the repo using [pre-commit](https://pre-commit.com/). See `.pre-commit-config.yaml` for the checks that are performed and `pyproject.toml` for the configuration of those linters. The configuration starts with all `ruff` lints enabled with a list of specifically disabled lints. If you are writing new code that is triggering a lint that you think ought to be disabled, you can suggest this in a PR, but generally stick to conforming to the suggested linter rules.
