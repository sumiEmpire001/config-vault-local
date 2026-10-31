import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager


class Conflict(ValueError):
    pass


def encode(value):
    def check(item, depth=0):
        if depth > 24:
            raise ValueError("JSON nesting limit exceeded")
        if isinstance(item, dict):
            if not all(isinstance(key, str) for key in item):
                raise ValueError("JSON object keys must be strings")
            for child in item.values():
                check(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                check(child, depth + 1)
        elif type(item) not in (str, int, float, bool, type(None)):
            raise ValueError("Unsupported JSON value")
    check(value)
    return json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":"))


def label(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError("Identifiers, actors and reasons must be 1..256 characters")


def validate_schema(schema, depth=0):
    supported = {"type", "properties", "required", "additionalProperties", "items", "enum", "minimum", "maximum", "minLength", "maxLength"}
    if depth > 20 or not isinstance(schema, dict) or set(schema) - supported or schema.get("type") not in ("object", "array", "string", "integer", "number", "boolean"):
        raise ValueError("Invalid or unsupported schema")
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
        raise ValueError("enum must be a nonempty list")
    if schema["type"] == "object":
        properties, required = schema.get("properties", {}), schema.get("required", [])
        if not isinstance(properties, dict) or len(properties) > 256 or not isinstance(required, list) or not all(isinstance(x, str) for x in required) or len(set(required)) != len(required) or not set(required) <= set(properties) or type(schema.get("additionalProperties", False)) is not bool:
            raise ValueError("Invalid object schema")
        for name, rule in properties.items():
            label(name)
            validate_schema(rule, depth + 1)
    elif any(key in schema for key in ("properties", "required", "additionalProperties")):
        raise ValueError("Object keywords require object type")
    if schema["type"] == "array":
        validate_schema(schema.get("items"), depth + 1)
    elif "items" in schema:
        raise ValueError("items requires array type")
    for key in ("minimum", "maximum"):
        if key in schema and (schema["type"] not in ("integer", "number") or type(schema[key]) not in (int, float)):
            raise ValueError("Numeric bounds require numeric schema values")
    for key in ("minLength", "maxLength"):
        if key in schema and (schema["type"] != "string" or type(schema[key]) is not int or schema[key] < 0):
            raise ValueError("Length bounds require nonnegative integers and string type")
    if "minimum" in schema and "maximum" in schema and schema["minimum"] > schema["maximum"]:
        raise ValueError("Reversed numeric bounds")
    if "minLength" in schema and "maxLength" in schema and schema["minLength"] > schema["maxLength"]:
        raise ValueError("Reversed length bounds")
    encode(schema)


def validate_value(value, schema, path="$", depth=0):
    if depth > 20:
        raise ValueError("Configuration nesting limit exceeded")
    kind = schema["type"]
    expected = {"object": dict, "array": list, "string": str, "integer": int, "boolean": bool}
    if (kind == "number" and type(value) not in (int, float)) or (kind != "number" and type(value) is not expected[kind]):
        raise ValueError(path + ": wrong value type")
    if "enum" in schema and not any(encode(value) == encode(option) for option in schema["enum"]):
        raise ValueError(path + ": value outside enum")
    if kind == "object":
        rules = schema.get("properties", {})
        if not set(schema.get("required", [])) <= set(value) or (not schema.get("additionalProperties", False) and set(value) - set(rules)):
            raise ValueError(path + ": required fields missing or undeclared fields present")
        for key in value:
            if key in rules:
                validate_value(value[key], rules[key], path + "." + key, depth + 1)
    if kind == "array":
        for i, item in enumerate(value):
            validate_value(item, schema["items"], path + f"[{i}]", depth + 1)
    if kind in ("integer", "number") and (("minimum" in schema and value < schema["minimum"]) or ("maximum" in schema and value > schema["maximum"])):
        raise ValueError(path + ": numeric bound failed")
    if kind == "string" and (("minLength" in schema and len(value) < schema["minLength"]) or ("maxLength" in schema and len(value) > schema["maxLength"])):
        raise ValueError(path + ": string length bound failed")


class Vault:
    def __init__(self, path="config.db", clock=time.time):
        if str(path) == ":memory:":
            raise ValueError("Use a filesystem database")
        self.path, self.clock = str(path), clock
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS apps(id TEXT PRIMARY KEY,schema TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS revisions(app TEXT NOT NULL REFERENCES apps(id),number INTEGER NOT NULL,data TEXT NOT NULL,hash TEXT NOT NULL,actor TEXT NOT NULL,reason TEXT NOT NULL,at REAL NOT NULL,PRIMARY KEY(app,number));
                CREATE TABLE IF NOT EXISTS heads(app TEXT NOT NULL REFERENCES apps(id),environment TEXT NOT NULL,revision INTEGER NOT NULL,generation INTEGER NOT NULL,PRIMARY KEY(app,environment));
                CREATE TABLE IF NOT EXISTS history(seq INTEGER PRIMARY KEY,app TEXT NOT NULL,environment TEXT NOT NULL,generation INTEGER NOT NULL,revision INTEGER NOT NULL,event TEXT NOT NULL,actor TEXT NOT NULL,reason TEXT NOT NULL,at REAL NOT NULL);
            """)

    @contextmanager
    def db(self, write=False):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except BaseException:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def register(self, app, schema):
        label(app)
        validate_schema(schema)
        with self.db(True) as db:
            old = db.execute("SELECT schema FROM apps WHERE id=?", (app,)).fetchone()
            if old and old[0] != encode(schema):
                raise ValueError("Application schema is immutable in this version")
            db.execute("INSERT OR IGNORE INTO apps VALUES(?,?)", (app, encode(schema)))

    def schema(self, db, app):
        row = db.execute("SELECT schema FROM apps WHERE id=?", (app,)).fetchone()
        if not row:
            raise ValueError("Application not found")
        return json.loads(row[0])

    def head(self, db, app, env, expected):
        label(env)
        if type(expected) is not int or expected < 0:
            raise ValueError("Expected generation must be a nonnegative integer")
        row = db.execute("SELECT revision,generation FROM heads WHERE app=? AND environment=?", (app, env)).fetchone()
        generation = row["generation"] if row else 0
        if generation != expected:
            raise Conflict("Environment generation changed")
        return generation

    def revision(self, db, app, number):
        row = db.execute("SELECT * FROM revisions WHERE app=? AND number=?", (app, number)).fetchone()
        if not row or hashlib.sha256(row["data"].encode()).hexdigest() != row["hash"]:
            raise ValueError("Revision missing or checksum invalid")
        value = json.loads(row["data"])
        validate_value(value, self.schema(db, app))
        return dict(row) | {"data": value}

    def move(self, db, app, env, revision, generation, event, actor, reason):
        label(actor)
        label(reason)
        db.execute("INSERT INTO heads VALUES(?,?,?,?) ON CONFLICT(app,environment) DO UPDATE SET revision=excluded.revision,generation=excluded.generation", (app, env, revision, generation + 1))
        db.execute("INSERT INTO history(app,environment,generation,revision,event,actor,reason,at) VALUES(?,?,?,?,?,?,?,?)", (app, env, generation + 1, revision, event, actor, reason, self.clock()))
        return {"app": app, "environment": env, "revision": revision, "generation": generation + 1}

    def publish(self, app, env, value, *, expected_generation, actor, reason):
        packed = encode(value)
        if len(packed.encode()) > 65536:
            raise ValueError("Configuration exceeds 64 KiB")
        label(actor)
        label(reason)
        with self.db(True) as db:
            validate_value(value, self.schema(db, app))
            generation = self.head(db, app, env, expected_generation)
            revision = db.execute("SELECT COALESCE(MAX(number),0)+1 FROM revisions WHERE app=?", (app,)).fetchone()[0]
            db.execute("INSERT INTO revisions VALUES(?,?,?,?,?,?,?)", (app, revision, packed, hashlib.sha256(packed.encode()).hexdigest(), actor, reason, self.clock()))
            return self.move(db, app, env, revision, generation, "published", actor, reason)

    def promote(self, app, source, target, *, expected_source, expected_target, actor, reason):
        if source == target:
            raise ValueError("Source and target environments must differ")
        with self.db(True) as db:
            self.schema(db, app)
            self.head(db, app, source, expected_source)
            generation = self.head(db, app, target, expected_target)
            row = db.execute("SELECT revision FROM heads WHERE app=? AND environment=?", (app, source)).fetchone()
            if not row:
                raise ValueError("Source environment has no configuration")
            self.revision(db, app, row[0])
            return self.move(db, app, target, row[0], generation, "promoted:" + source, actor, reason)

    def rollback(self, app, env, revision, *, expected_generation, actor, reason):
        if type(revision) is not int or revision < 1:
            raise ValueError("Revision must be a positive integer")
        with self.db(True) as db:
            self.revision(db, app, revision)
            if not db.execute("SELECT revision FROM heads WHERE app=? AND environment=?", (app, env)).fetchone():
                raise ValueError("Rollback requires an existing environment")
            generation = self.head(db, app, env, expected_generation)
            return self.move(db, app, env, revision, generation, "rolled_back", actor, reason)

    def get(self, app, env):
        with self.db() as db:
            db.execute("BEGIN")
            row = db.execute("SELECT revision,generation FROM heads WHERE app=? AND environment=?", (app, env)).fetchone()
            if not row:
                raise ValueError("Environment not found")
            return self.revision(db, app, row["revision"]) | {"environment": env, "generation": row["generation"]}

    def history(self, app, env):
        with self.db() as db:
            return [dict(row) for row in db.execute("SELECT * FROM history WHERE app=? AND environment=? ORDER BY generation", (app, env))]
