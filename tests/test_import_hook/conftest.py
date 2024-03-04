import logging
import shutil
import sys
from pathlib import Path
from typing import Iterator

import pytest
from maturin_import_hook import reset_logger
from maturin_import_hook._building import get_default_build_dir

from .common import CLEAR_WORKSPACE

reset_logger()  # so that logs can be captured for testing
logging.basicConfig(format="[%(name)s] [%(levelname)s] %(message)s", level=logging.DEBUG)

log = logging.getLogger(__name__)


log.info("running tests with %s", sys.executable)


@pytest.fixture()
def workspace(tmp_path: Path) -> Iterator[Path]:
    try:
        yield tmp_path
    finally:
        if CLEAR_WORKSPACE:
            log.info("clearing workspace %s", tmp_path)
            shutil.rmtree(tmp_path, ignore_errors=True)


@pytest.fixture(autouse=True)
def _clean_build_cache() -> None:
    build_dir = get_default_build_dir()
    if build_dir.exists():
        log.info("clearing build cache at %s", build_dir)
        shutil.rmtree(build_dir)
