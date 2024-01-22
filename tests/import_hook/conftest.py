import logging

from maturin_import_hook import reset_logger

reset_logger()  # so that logs can be captured for testing
logging.basicConfig(format="[%(name)s] [%(levelname)s] %(message)s", level=logging.DEBUG)
