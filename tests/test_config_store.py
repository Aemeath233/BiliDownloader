from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from bili_client.services.config_store import ConfigStore


class ConfigStoreTests(unittest.TestCase):
    def test_load_rewrites_config_with_current_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            old_appdata = os.environ.get("APPDATA")
            os.environ["APPDATA"] = temp_dir
            try:
                store = ConfigStore(app_name="BiliDownloaderTest")
                store.config_dir.mkdir(parents=True, exist_ok=True)
                store.config_file.write_text(
                    json.dumps({"sessdata": "old-sess", "save_path": str(Path(temp_dir) / "downloads")}),
                    encoding="utf-8",
                )

                config = store.load()
                saved = json.loads(store.config_file.read_text(encoding="utf-8"))
            finally:
                if old_appdata is None:
                    os.environ.pop("APPDATA", None)
                else:
                    os.environ["APPDATA"] = old_appdata

        self.assertEqual("old-sess", config.sessdata)
        self.assertIn("filename_template", saved)
        self.assertIn("cleanup_temp_files", saved)
        self.assertNotIn("speed_limit", saved)


if __name__ == "__main__":
    unittest.main()
