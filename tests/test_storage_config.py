from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from storage.config import load_database_settings


class DatabaseSettingsTests(unittest.TestCase):
    def test_loads_postgresql_url(self) -> None:
        value = "postgresql://user:password@localhost/database"
        with patch.dict(os.environ, {"JARVIS_DATABASE_URL": value}, clear=True):
            settings = load_database_settings()
        self.assertEqual(settings.database_url, value)

    def test_rejects_missing_url(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                load_database_settings()


if __name__ == "__main__":
    unittest.main()
