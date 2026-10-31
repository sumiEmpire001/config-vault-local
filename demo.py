import json
import tempfile
from pathlib import Path
from config_vault import Vault

SCHEMA = {"type": "object", "properties": {"timeout": {"type": "integer", "minimum": 1, "maximum": 60}, "enabled": {"type": "boolean"}}, "required": ["timeout", "enabled"]}
if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        vault = Vault(Path(directory) / "demo.db")
        vault.register("synthetic-app", SCHEMA)
        vault.publish("synthetic-app", "dev", {"timeout": 20, "enabled": True}, expected_generation=0, actor="demo", reason="initial development configuration")
        vault.publish("synthetic-app", "prod", {"timeout": 10, "enabled": False}, expected_generation=0, actor="demo", reason="initial production configuration")
        vault.promote("synthetic-app", "dev", "prod", expected_source=1, expected_target=1, actor="demo", reason="synthetic promotion")
        vault.rollback("synthetic-app", "prod", 2, expected_generation=2, actor="demo", reason="synthetic rollback")
        print(json.dumps(vault.get("synthetic-app", "prod"), indent=2))
