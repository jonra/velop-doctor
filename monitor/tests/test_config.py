import json
import os
import tempfile
import unittest

from velop_doctor import config
from velop_doctor.config import Config


class ConfigTests(unittest.TestCase):
    def setUp(self):
        os.environ["VELOP_DOCTOR_HOME"] = tempfile.mkdtemp()

    def tearDown(self):
        os.environ.pop("VELOP_DOCTOR_HOME", None)

    def test_save_writes_only_what_was_changed(self):
        cfg = Config(primary="192.168.1.1", weak_dbm=-75)
        config.save(cfg)
        with open(config.config_path()) as f:
            saved = json.load(f)
        self.assertEqual(saved, {"primary": "192.168.1.1", "username": "admin", "weak_dbm": -75})

    def test_load_applies_saved_values_over_defaults_and_ignores_unknown_keys(self):
        config.config_path().parent.mkdir(parents=True, exist_ok=True)
        with open(config.config_path(), "w") as f:
            json.dump({"primary": "10.0.0.1", "interval": 30, "nonsense": 1}, f)
        cfg = config.load()
        self.assertEqual((cfg.primary, cfg.interval, cfg.weak_dbm), ("10.0.0.1", 30, -80))

    def test_a_default_that_changes_later_reaches_existing_users(self):
        config.save(Config(primary="192.168.1.1"))
        self.assertEqual(config.load().stale_client_seconds, Config().stale_client_seconds)

    def test_missing_or_corrupt_file_gives_defaults(self):
        self.assertEqual(config.load().interval, 60)
        config.config_path().parent.mkdir(parents=True, exist_ok=True)
        config.config_path().write_text("{not json")
        self.assertEqual(config.load().interval, 60)


if __name__ == "__main__":
    unittest.main()
