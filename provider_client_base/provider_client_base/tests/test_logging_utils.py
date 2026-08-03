"""``configure_logger`` — this lib's logger factory.

The text-helper tests that used to share this file moved to
``utils_core_lib/tests/test_text_utils.py`` along with the helpers themselves.
"""

import logging
import unittest

from provider_client_base.provider_client_base.helpers.logging_utils import configure_logger


class ConfigureLoggerTests(unittest.TestCase):
    def test_returns_logger(self):
        logger = configure_logger('test_logger')
        self.assertIsInstance(logger, logging.Logger)

    def test_logger_has_correct_name(self):
        logger = configure_logger('my_service')
        self.assertEqual(logger.name, 'my_service')

    def test_same_name_returns_same_instance(self):
        logger1 = configure_logger('shared')
        logger2 = configure_logger('shared')
        self.assertIs(logger1, logger2)

    def test_different_names_return_different_loggers(self):
        logger1 = configure_logger('service_a')
        logger2 = configure_logger('service_b')
        self.assertIsNot(logger1, logger2)
        self.assertNotEqual(logger1.name, logger2.name)
