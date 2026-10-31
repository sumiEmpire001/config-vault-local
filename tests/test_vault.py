import concurrent.futures
import tempfile
import unittest
from pathlib import Path
from config_vault import Vault, Conflict
from config_vault.vault import validate_schema, validate_value
from demo import SCHEMA


class VaultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "config.db"
        self.vault = Vault(self.path)
        self.vault.register("app", SCHEMA)

    def publish(self, env="dev", value=None, generation=0):
        return self.vault.publish("app", env, value or {"timeout": 20, "enabled": True}, expected_generation=generation, actor="demo", reason="test revision")

    def test_invalid_schemas_fail_closed(self):
        for schema in ({}, {"type": "string", "pattern": "unsafe"}, {"type": "integer", "minimum": 10, "maximum": 1}, {"type": "object", "required": ["missing"]}, {"type": "array"}):
            with self.assertRaises(ValueError):
                validate_schema(schema)

    def test_invalid_configuration_does_not_publish(self):
        for value in ({"timeout": True, "enabled": True}, {"timeout": 0, "enabled": True}, {"timeout": 1}, {"timeout": 1, "enabled": True, "extra": 1}, {"timeout": float("nan"), "enabled": True}):
            with self.assertRaises(ValueError):
                self.publish(value=value)
        self.assertEqual(self.vault.history("app", "dev"), [])

    def test_promotion_preserves_exact_revision_and_hash(self):
        self.publish()
        before = self.vault.get("app", "dev")
        self.vault.promote("app", "dev", "prod", expected_source=1, expected_target=0, actor="demo", reason="promote")
        after = self.vault.get("app", "prod")
        self.assertEqual(before["hash"], after["hash"])
        self.assertEqual(before["number"], after["number"])
        self.assertEqual(before["data"], after["data"])

    def test_rollback_restores_value_but_advances_generation(self):
        self.publish()
        original = self.vault.get("app", "dev")
        self.publish(value={"timeout": 30, "enabled": False}, generation=1)
        self.vault.rollback("app", "dev", 1, expected_generation=2, actor="demo", reason="rollback")
        result = self.vault.get("app", "dev")
        self.assertEqual(result["data"], original["data"])
        self.assertEqual(result["generation"], 3)

    def test_concurrent_publish_has_one_generation_winner(self):
        def attempt(n):
            try:
                return self.publish(value={"timeout": n + 1, "enabled": True})
            except Conflict:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(attempt, range(10)))
        self.assertEqual(sum(r is not None for r in results), 1)
        self.assertEqual(len(self.vault.history("app", "dev")), 1)

    def test_changed_source_prevents_stale_promotion(self):
        self.publish()
        self.publish(generation=1)
        with self.assertRaises(Conflict):
            self.vault.promote("app", "dev", "prod", expected_source=1, expected_target=0, actor="demo", reason="stale")
        with self.assertRaises(ValueError):
            self.vault.get("app", "prod")

    def test_rollback_rejects_missing_revision_or_environment(self):
        self.publish()
        with self.assertRaises(ValueError):
            self.vault.rollback("app", "dev", 999, expected_generation=1, actor="demo", reason="invalid")
        with self.assertRaises(ValueError):
            self.vault.rollback("app", "missing", 1, expected_generation=0, actor="demo", reason="invalid")

    def test_schema_is_immutable_and_restart_preserves_history(self):
        self.publish()
        again = Vault(self.path)
        self.assertEqual(again.get("app", "dev")["data"]["timeout"], 20)
        with self.assertRaises(ValueError):
            again.register("app", {"type": "string"})

    def test_checksum_rejects_modified_stored_revision(self):
        self.publish()
        with self.vault.db(True) as db:
            db.execute("UPDATE revisions SET data='{}' WHERE app='app' AND number=1")
        with self.assertRaises(ValueError):
            self.vault.get("app", "dev")

    def test_app_namespaces_and_environment_histories_are_separate(self):
        self.publish()
        self.vault.register("other", SCHEMA)
        self.vault.publish("other", "dev", {"timeout": 3, "enabled": False}, expected_generation=0, actor="demo", reason="other app")
        self.assertEqual(self.vault.get("other", "dev")["data"]["timeout"], 3)
        self.assertEqual(self.vault.get("app", "dev")["data"]["timeout"], 20)
        self.assertEqual(len(self.vault.history("app", "dev")), 1)

    def test_nested_arrays_and_strict_booleans(self):
        schema = {"type": "object", "properties": {"flags": {"type": "array", "items": {"type": "boolean"}}}, "required": ["flags"]}
        validate_schema(schema)
        validate_value({"flags": [True, False]}, schema)
        with self.assertRaises(ValueError):
            validate_value({"flags": [1]}, schema)

    def test_actor_reason_generation_and_json_boundaries(self):
        with self.assertRaises(ValueError):
            self.vault.publish("app", "dev", {"timeout": 20, "enabled": True}, expected_generation=True, actor="demo", reason="bad")
        with self.assertRaises(ValueError):
            self.vault.publish("app", "dev", {"timeout": 20, "enabled": True}, expected_generation=0, actor="demo", reason="")
        with self.assertRaises(ValueError):
            self.vault.publish("app", "dev", {1: "invalid key"}, expected_generation=0, actor="demo", reason="bad")
        self.assertEqual(self.vault.history("app", "dev"), [])
