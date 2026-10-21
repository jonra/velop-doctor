"""SQLite history: snapshots, latency samples, events and tracked conditions."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (ts INTEGER PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS samples (ts INTEGER NOT NULL, target TEXT NOT NULL,
    loss REAL, rtt REAL);
CREATE INDEX IF NOT EXISTS samples_idx ON samples (target, ts);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL,
    severity TEXT NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL, subject TEXT);
CREATE TABLE IF NOT EXISTS conditions (key TEXT PRIMARY KEY, first_seen INTEGER NOT NULL,
    last_seen INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 0,
    severity TEXT, title TEXT, detail TEXT, min_seconds INTEGER NOT NULL DEFAULT 0);
"""


class Store:
    def __init__(self, path):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)
            try:  # databases created before events had a subject
                self._db.execute("ALTER TABLE events ADD COLUMN subject TEXT")
            except sqlite3.OperationalError:
                pass

    def _run(self, sql, args=()):
        with self._lock:
            cur = self._db.execute(sql, args)
            self._db.commit()
            return cur.fetchall()

    # --- snapshots and samples -------------------------------------------
    def add_snapshot(self, snap):
        self._run("INSERT OR REPLACE INTO snapshots VALUES (?, ?)", (snap["ts"], json.dumps(snap)))
        rows = []
        for n in snap.get("nodes", []):
            if n.get("loss") is not None:
                rows.append((snap["ts"], f"node:{n['name']}", n["loss"], n["rtt_ms"]))
        for i in snap.get("internet", []):
            rows.append((snap["ts"], f"internet:{i['target']}", i["loss"], i["rtt_ms"]))
        rp = (snap.get("probes") or {}).get("router_ping")
        if rp:
            rows.append((snap["ts"], f"router:{rp['target']}", rp["loss"], rp["rtt_ms"]))
        if snap.get("upstream"):
            u = snap["upstream"]
            rows.append((snap["ts"], f"upstream:{u['target']}", u["loss"], u["rtt_ms"]))
        with self._lock:
            self._db.executemany("INSERT INTO samples VALUES (?, ?, ?, ?)", rows)
            self._db.commit()

    def latest_snapshot(self, offset=0):
        rows = self._run("SELECT data FROM snapshots ORDER BY ts DESC LIMIT 1 OFFSET ?", (offset,))
        return json.loads(rows[0]["data"]) if rows else None

    def history(self, since_ts):
        out = {}
        for r in self._run("SELECT ts, target, loss, rtt FROM samples WHERE ts >= ? ORDER BY ts",
                           (since_ts,)):
            out.setdefault(r["target"], []).append([r["ts"], r["rtt"], r["loss"]])
        return out

    # --- events ---------------------------------------------------------
    def add_event(self, ts, severity, kind, message, subject=None):
        self._run("INSERT INTO events (ts, severity, kind, message, subject) VALUES (?, ?, ?, ?, ?)",
                  (ts, severity, kind, message, subject))

    def count_events(self, kind, subject, since_ts):
        return self._run("SELECT COUNT(*) AS n FROM events WHERE kind = ? AND subject = ? AND ts >= ?",
                         (kind, subject, since_ts))[0]["n"]

    def availability(self, since_ts):
        """Per target: share of samples that got an answer, plus latency summary."""
        out = {}
        for r in self._run(
                "SELECT target, COUNT(*) AS n, SUM(CASE WHEN loss < 100 THEN 1 ELSE 0 END) AS up, "
                "AVG(CASE WHEN loss < 100 THEN rtt END) AS avg_rtt, MAX(rtt) AS max_rtt, "
                "AVG(loss) AS avg_loss FROM samples WHERE ts >= ? GROUP BY target", (since_ts,)):
            out[r["target"]] = {"samples": r["n"], "up_pct": round(100.0 * r["up"] / r["n"], 2),
                                "avg_rtt": r["avg_rtt"], "max_rtt": r["max_rtt"],
                                "avg_loss": round(r["avg_loss"] or 0, 1)}
        return out

    def events(self, limit=100):
        return [dict(r) for r in self._run(
            "SELECT ts, severity, kind, message FROM events ORDER BY ts DESC, id DESC LIMIT ?", (limit,))]

    # --- conditions -----------------------------------------------------
    def update_conditions(self, now, findings, gap_seconds=300):
        """Track findings over time; emit events when they open and clear.

        A finding becomes active once it has been present for `min_seconds`. If
        monitoring paused for longer than `gap_seconds`, the clock restarts, so a
        sleeping laptop never counts as "the problem lasted an hour".
        """
        existing = {r["key"]: dict(r) for r in self._run("SELECT * FROM conditions")}
        seen = set()
        for f in findings:
            seen.add(f["key"])
            row = existing.get(f["key"])
            if row is None or now - row["last_seen"] > gap_seconds:
                first, was_active = now, 0
                if row and row["active"]:
                    self.add_event(now, "info", "gap",
                                   f"Monitoring paused; restarted tracking of: {row['title']}")
            else:
                first, was_active = row["first_seen"], row["active"]
            active = 1 if now - first >= f["min_seconds"] else 0
            if active and not was_active:
                self.add_event(now, f["severity"], "problem", f["title"])
            self._run(
                "INSERT OR REPLACE INTO conditions VALUES (?,?,?,?,?,?,?,?)",
                (f["key"], first, now, active, f["severity"], f["title"], f["detail"],
                 f["min_seconds"]))
        for key, row in existing.items():
            if key not in seen:
                if row["active"]:
                    self.add_event(now, "info", "cleared", f"Resolved: {row['title']}")
                self._run("DELETE FROM conditions WHERE key = ?", (key,))

    def active_findings(self):
        return [dict(r) for r in self._run(
            "SELECT key, severity, title, detail, first_seen AS since FROM conditions "
            "WHERE active = 1 ORDER BY CASE severity WHEN 'critical' THEN 0 "
            "WHEN 'warning' THEN 1 ELSE 2 END, first_seen")]

    def prune(self, now, retention_days):
        cutoff = now - retention_days * 86400
        self._run("DELETE FROM snapshots WHERE ts < ?", (cutoff,))
        self._run("DELETE FROM samples WHERE ts < ?", (cutoff,))
