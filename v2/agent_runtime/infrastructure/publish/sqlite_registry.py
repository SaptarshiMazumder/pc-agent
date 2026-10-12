"""Single-host publishing metadata. Accounts and payments remain in Neon.

Creator/root private keys are always Fernet-wrapped. Import existing keys before
cutover; never generate a replacement platform root or silently change trust.
"""

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from cryptography.fernet import Fernet

from agent_runtime.infrastructure import signing
from agent_runtime.infrastructure.publish.creator_directory import (
    DynamoCreatorDirectory,
    creator_id_for,
)
from agent_runtime.infrastructure.publish.root_vault import ROOT_ROW_ID


class RegistryDatabase:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS creators (account TEXT PRIMARY KEY, id TEXT UNIQUE NOT NULL,
                                                     document TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS owners (bundle TEXT PRIMARY KEY, creator TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS uploads (key TEXT PRIMARY KEY, owner TEXT NOT NULL,
                    max_bytes INTEGER NOT NULL, expires INTEGER NOT NULL, claimed INTEGER NOT NULL DEFAULT 0);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()


class SqliteCreatorDirectory:
    def __init__(self, database, cipher):
        self.database, self.cipher = database, cipher

    def row(self, creator):
        with self.database.connect() as db:
            row = db.execute("SELECT document FROM creators WHERE id=?", (creator,)).fetchone()
        return json.loads(row[0]) if row else None

    def for_account(self, account_id, display_name=""):
        account_id = account_id.strip()
        if not account_id or account_id == ROOT_ROW_ID:
            raise ValueError("valid account required")
        creator = creator_id_for(account_id)
        existing = self.row(creator)
        if existing:
            return DynamoCreatorDirectory._to_creator(existing)
        private, public = signing.generate_keypair()
        row = dict(
            account_id=account_id,
            creator_id=creator,
            name=display_name or creator,
            state="pending",
            public_key=public,
            private_key=self.cipher.encrypt(private.encode()).decode(),
            created=DynamoCreatorDirectory._default_now(),
        )
        # Do not replace the winner's identity under concurrent first submissions.
        with self.database.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO creators VALUES (?,?,?)",
                (account_id, creator, json.dumps(row)),
            )
        return DynamoCreatorDirectory._to_creator(self.row(creator))

    def private_key(self, creator_id):
        return (self.row(creator_id) or {}).get("private_key", "")

    def public_key(self, creator_id):
        return (self.row(creator_id) or {}).get("public_key", "")

    def all(self):
        with self.database.connect() as db:
            rows = [
                json.loads(row[0])
                for row in db.execute("SELECT document FROM creators WHERE id!=?", (ROOT_ROW_ID,))
            ]
        return sorted(
            [
                {
                    **{k: v for k, v in row.items() if k != "private_key"},
                    "wrapped": bool(row.get("private_key")),
                }
                for row in rows
            ],
            key=lambda row: (row["state"], row["name"].lower()),
        )

    def pending(self):
        return [
            DynamoCreatorDirectory._to_creator(row)
            for row in self.all()
            if row["state"] == "pending"
        ]

    def _state(self, creator_id, state, date):
        if creator_id == ROOT_ROW_ID:
            raise ValueError("cannot change platform root state")
        with self.database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            raw = db.execute("SELECT document FROM creators WHERE id=?", (creator_id,)).fetchone()
            if not raw:
                raise KeyError(creator_id)
            row = json.loads(raw[0])
            row.update(state=state, **{date: DynamoCreatorDirectory._default_now()})
            db.execute("UPDATE creators SET document=? WHERE id=?", (json.dumps(row), creator_id))

    def admit(self, creator_id):
        self._state(creator_id, "listed", "admitted")

    def revoke(self, creator_id):
        self._state(creator_id, "revoked", "revoked")

    def claim(self, bundle_id, creator_id):
        with self.database.connect() as db:
            db.execute("INSERT OR IGNORE INTO owners VALUES (?,?)", (bundle_id, creator_id))
            return db.execute("SELECT creator FROM owners WHERE bundle=?", (bundle_id,)).fetchone()[
                0
            ]

    def owner_of(self, bundle_id):
        with self.database.connect() as db:
            row = db.execute("SELECT creator FROM owners WHERE bundle=?", (bundle_id,)).fetchone()
        return row[0] if row else ""


class EncryptedDirectorySigner:
    def __init__(self, directory, cipher):
        self.directory, self.cipher = directory, cipher

    def sign(self, creator_id, message):
        key = self.cipher.decrypt(self.directory.private_key(creator_id).encode()).decode()
        return signing.sign(key, message)

    def public_key(self, creator_id):
        return self.directory.public_key(creator_id)


class SqliteRootKeyVault:
    def __init__(self, directory, cipher):
        self.directory, self.cipher = directory, cipher

    def public_key(self):
        return self.directory.public_key(ROOT_ROW_ID)

    def private_key(self):
        wrapped = self.directory.private_key(ROOT_ROW_ID)
        if not wrapped:
            raise RuntimeError("import the existing platform root key before roster changes")
        return self.cipher.decrypt(wrapped.encode()).decode()

    def store(self, private_b64, public_b64):
        challenge = b"agentd-root-import"
        if not signing.verify(public_b64, challenge, signing.sign(private_b64, challenge)):
            raise ValueError("root key pair mismatch")
        with self.directory.database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            raw = db.execute("SELECT document FROM creators WHERE id=?", (ROOT_ROW_ID,)).fetchone()
            if raw and json.loads(raw[0])["public_key"] != public_b64:
                raise ValueError("refusing platform root rotation during migration")
            row = dict(
                account_id=ROOT_ROW_ID,
                creator_id=ROOT_ROW_ID,
                name="platform root key",
                state="root",
                public_key=public_b64,
                private_key=self.cipher.encrypt(private_b64.encode()).decode(),
            )
            db.execute(
                "INSERT OR REPLACE INTO creators VALUES (?,?,?)",
                (ROOT_ROW_ID, ROOT_ROW_ID, json.dumps(row)),
            )


class FileIndexLock:
    """Host kernel lock: crash release, no lease expiry allowing two active publishers.

    Separate file descriptors and thread-local state are required for concurrent
    requests sharing one composition root. No network filesystem is supported.
    """

    def __init__(self, path, timeout=20):
        self.path, self.timeout, self.local = str(path), timeout, threading.local()

    def __enter__(self):
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"0")
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.local.fd = fd
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise TimeoutError("another publish is in progress; retry shortly") from None
                time.sleep(0.1)

    def __exit__(self, *exc):
        fd = self.local.fd
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
            del self.local.fd


def adapters(path, kek):
    cipher = Fernet(kek.encode())  # Missing/invalid encryption configuration refuses startup.
    database = RegistryDatabase(path)
    directory = SqliteCreatorDirectory(database, cipher)
    return (
        database,
        directory,
        EncryptedDirectorySigner(directory, cipher),
        SqliteRootKeyVault(directory, cipher),
        FileIndexLock(str(path) + ".lock"),
    )
