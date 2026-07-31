from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .adapters import threads_display_name
from .util import slugify, utc_now
from .collection_status import SUCCESS_STATUSES


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS subjects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    slug TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    url TEXT NOT NULL,
    normalized_url TEXT NOT NULL,
    browser_name TEXT NOT NULL DEFAULT 'default',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_run_at TEXT,
    UNIQUE(subject_id, normalized_url)
);

CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    username TEXT,
    display_name TEXT,
    platform_user_id TEXT,
    avatar_url TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE(subject_id, platform, canonical_url)
);

CREATE TABLE IF NOT EXISTS contact_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    relation TEXT NOT NULL CHECK(relation IN ('followers','following','friends')),
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    times_seen INTEGER NOT NULL DEFAULT 1,
    UNIQUE(contact_id, profile_id, relation)
);

CREATE TABLE IF NOT EXISTS associated_people (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    normalized_name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    role TEXT,
    organization TEXT,
    source_url TEXT NOT NULL,
    evidence_text TEXT NOT NULL,
    extraction_source TEXT NOT NULL,
    canonical_profile_url TEXT,
    canonical_platform TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    times_seen INTEGER NOT NULL DEFAULT 1,
    UNIQUE(subject_id, profile_id, normalized_name)
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    run_stamp TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    run_dir TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collection_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    relation TEXT NOT NULL,
    reported_count INTEGER,
    collected_this_run INTEGER NOT NULL DEFAULT 0,
    new_contacts_added INTEGER NOT NULL DEFAULT 0,
    total_unique_saved INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    reason TEXT NOT NULL,
    diagnostics_path TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    UNIQUE(run_id, profile_id, relation)
);

CREATE TABLE IF NOT EXISTS website_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    subject_present INTEGER NOT NULL DEFAULT 0,
    people_detected INTEGER NOT NULL DEFAULT 0,
    new_associations INTEGER NOT NULL DEFAULT 0,
    new_contacts_added INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    reason TEXT NOT NULL,
    analysis_mode TEXT NOT NULL,
    author_name TEXT,
    author_entity_type TEXT,
    diagnostics_path TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    UNIQUE(run_id, profile_id)
);

CREATE INDEX IF NOT EXISTS idx_contacts_subject_platform ON contacts(subject_id, platform);
CREATE INDEX IF NOT EXISTS idx_edges_profile_relation ON contact_edges(profile_id, relation);
CREATE INDEX IF NOT EXISTS idx_results_run ON collection_results(run_id);
CREATE INDEX IF NOT EXISTS idx_associated_people_subject ON associated_people(subject_id, normalized_name);
CREATE INDEX IF NOT EXISTS idx_associated_people_profile ON associated_people(profile_id);
CREATE INDEX IF NOT EXISTS idx_website_results_run ON website_results(run_id);
"""


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.conn.commit()
        else:
            self.conn.rollback()
        self.close()

    def list_subjects(self) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT s.*,
                       COUNT(DISTINCT p.id) AS profile_count,
                       COUNT(DISTINCT c.id) AS contact_count,
                       COUNT(DISTINCT ap.normalized_name) AS associated_people_count,
                       MAX(r.completed_at) AS last_run_at
                FROM subjects s
                LEFT JOIN profiles p ON p.subject_id=s.id
                LEFT JOIN contacts c ON c.subject_id=s.id
                LEFT JOIN associated_people ap ON ap.subject_id=s.id
                LEFT JOIN runs r ON r.subject_id=s.id
                GROUP BY s.id
                ORDER BY s.created_at, s.id
                """
            )
        )

    def prune_subjects_missing_folders(
        self,
        subjects_root: Path,
        backup_dir: Path,
    ) -> dict[str, Any]:
        """Remove database subjects whose exported subject folders were deleted.

        The SQLite backup is created with SQLite's online-backup API before any
        rows are removed, so WAL-backed databases remain recoverable.
        """
        rows = list(self.conn.execute("SELECT id,name,slug FROM subjects ORDER BY id"))
        missing = [row for row in rows if not (subjects_root / str(row["slug"])).is_dir()]
        if not missing:
            return {"removed": [], "backup_path": None}

        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = (
            utc_now()
            .replace("-", "")
            .replace(":", "")
            .replace("+0000", "Z")
            .replace("+00:00", "Z")
            .replace("T", "-")
        )
        backup_path = backup_dir / f"contactanalyzer-before-subject-reconcile-{stamp}.sqlite3"
        index = 2
        while backup_path.exists():
            backup_path = backup_dir / f"contactanalyzer-before-subject-reconcile-{stamp}-{index}.sqlite3"
            index += 1
        backup = sqlite3.connect(backup_path)
        try:
            self.conn.backup(backup)
        finally:
            backup.close()

        subject_ids = [int(row["id"]) for row in missing]
        placeholders = ",".join("?" for _ in subject_ids)
        self.conn.execute(f"DELETE FROM subjects WHERE id IN ({placeholders})", subject_ids)
        self.conn.commit()
        return {
            "removed": [dict(row) for row in missing],
            "backup_path": str(backup_path),
        }

    def get_subject(self, selector: str | int) -> sqlite3.Row | None:
        if isinstance(selector, int) or str(selector).isdigit():
            row = self.conn.execute("SELECT * FROM subjects WHERE id=?", (int(selector),)).fetchone()
            if row:
                return row
        return self.conn.execute(
            "SELECT * FROM subjects WHERE lower(name)=lower(?) OR slug=?",
            (str(selector), slugify(str(selector))),
        ).fetchone()

    def create_or_get_subject(self, name: str) -> sqlite3.Row:
        existing = self.get_subject(name)
        if existing:
            return existing
        now = utc_now()
        base = slugify(name)
        slug = base
        index = 2
        while self.conn.execute("SELECT 1 FROM subjects WHERE slug=?", (slug,)).fetchone():
            slug = f"{base}-{index}"
            index += 1
        self.conn.execute(
            "INSERT INTO subjects(name,slug,created_at,updated_at) VALUES(?,?,?,?)",
            (name.strip(), slug, now, now),
        )
        self.conn.commit()
        return self.get_subject(name)  # type: ignore[return-value]

    def touch_subject(self, subject_id: int) -> None:
        self.conn.execute("UPDATE subjects SET updated_at=? WHERE id=?", (utc_now(), subject_id))

    def add_profile(self, subject_id: int, platform: str, url: str, browser_name: str = "default") -> tuple[sqlite3.Row, bool]:
        normalized = url.rstrip("/").casefold()
        existing = self.conn.execute(
            "SELECT * FROM profiles WHERE subject_id=? AND normalized_url=?",
            (subject_id, normalized),
        ).fetchone()
        if existing:
            self.conn.execute(
                "UPDATE profiles SET url=?, platform=?, browser_name=?, updated_at=? WHERE id=?",
                (url, platform, browser_name, utc_now(), existing["id"]),
            )
            self.conn.commit()
            return self.conn.execute("SELECT * FROM profiles WHERE id=?", (existing["id"],)).fetchone(), False  # type: ignore[return-value]
        now = utc_now()
        cursor = self.conn.execute(
            """
            INSERT INTO profiles(subject_id,platform,url,normalized_url,browser_name,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?)
            """,
            (subject_id, platform, url, normalized, browser_name, now, now),
        )
        self.touch_subject(subject_id)
        self.conn.commit()
        return self.conn.execute("SELECT * FROM profiles WHERE id=?", (cursor.lastrowid,)).fetchone(), True  # type: ignore[return-value]

    def profiles_for_subject(self, subject_id: int) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM profiles WHERE subject_id=? ORDER BY platform, lower(url)",
                (subject_id,),
            )
        )

    def start_run(self, subject_id: int, stamp: str, run_dir: str) -> int:
        cursor = self.conn.execute(
            "INSERT INTO runs(subject_id,run_stamp,started_at,status,run_dir) VALUES(?,?,?,?,?)",
            (subject_id, stamp, utc_now(), "running", run_dir),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def finish_run(self, run_id: int, status: str) -> None:
        self.conn.execute(
            "UPDATE runs SET completed_at=?, status=? WHERE id=?",
            (utc_now(), status, run_id),
        )
        self.conn.commit()

    def upsert_contacts(
        self,
        subject_id: int,
        profile_id: int,
        relation: str,
        records: Iterable[dict[str, Any]],
    ) -> tuple[int, int]:
        subject_profile_keys = {
            (
                str(row["platform"] or "").casefold(),
                str(row["normalized_url"] or "").casefold(),
            )
            for row in self.conn.execute(
                "SELECT platform,normalized_url FROM profiles WHERE subject_id=?",
                (subject_id,),
            )
        }
        seen_this_run: set[tuple[str, str]] = set()
        new_count = 0
        total_seen = 0
        saw_threads = False
        now = utc_now()
        for record in records:
            platform = str(record["platform"])
            if platform == "threads":
                saw_threads = True
                record = dict(record)
                record["display_name"] = threads_display_name(
                    str(record.get("username") or ""),
                    record.get("display_name"),
                )
            canonical = str(record["profile_url"]).rstrip("/")
            key = (platform, canonical.casefold())
            if (platform.casefold(), canonical.casefold()) in subject_profile_keys:
                continue
            if key in seen_this_run:
                continue
            seen_this_run.add(key)
            total_seen += 1
            existing = self.conn.execute(
                "SELECT * FROM contacts WHERE subject_id=? AND platform=? AND lower(canonical_url)=lower(?)",
                (subject_id, platform, canonical),
            ).fetchone()
            if existing:
                contact_id = int(existing["id"])
                self.conn.execute(
                    """
                    UPDATE contacts
                    SET username=COALESCE(NULLIF(?,''),username),
                        display_name=COALESCE(NULLIF(?,''),display_name),
                        platform_user_id=COALESCE(NULLIF(?,''),platform_user_id),
                        avatar_url=COALESCE(NULLIF(?,''),avatar_url),
                        last_seen_at=?
                    WHERE id=?
                    """,
                    (
                        record.get("username") or "",
                        record.get("display_name") or "",
                        record.get("platform_user_id") or "",
                        record.get("avatar_url") or "",
                        now,
                        contact_id,
                    ),
                )
            else:
                cursor = self.conn.execute(
                    """
                    INSERT INTO contacts(
                        subject_id,platform,canonical_url,username,display_name,platform_user_id,avatar_url,
                        first_seen_at,last_seen_at
                    ) VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        subject_id,
                        platform,
                        canonical,
                        record.get("username"),
                        record.get("display_name"),
                        record.get("platform_user_id"),
                        record.get("avatar_url"),
                        now,
                        now,
                    ),
                )
                contact_id = int(cursor.lastrowid)
                new_count += 1

            edge = self.conn.execute(
                "SELECT id FROM contact_edges WHERE contact_id=? AND profile_id=? AND relation=?",
                (contact_id, profile_id, relation),
            ).fetchone()
            if edge:
                self.conn.execute(
                    "UPDATE contact_edges SET last_seen_at=?, times_seen=times_seen+1 WHERE id=?",
                    (now, edge["id"]),
                )
            else:
                self.conn.execute(
                    """
                    INSERT INTO contact_edges(contact_id,profile_id,relation,first_seen_at,last_seen_at,times_seen)
                    VALUES(?,?,?,?,?,1)
                    """,
                    (contact_id, profile_id, relation, now, now),
                )
        if saw_threads:
            # Self-heal metadata from older passes without changing URL identity
            # or relationship membership.
            legacy_rows = self.conn.execute(
                "SELECT id,username,display_name FROM contacts WHERE subject_id=? AND platform='threads'",
                (subject_id,),
            ).fetchall()
            for legacy in legacy_rows:
                cleaned = threads_display_name(legacy["username"] or "", legacy["display_name"])
                if cleaned != legacy["display_name"]:
                    self.conn.execute(
                        "UPDATE contacts SET display_name=? WHERE id=?",
                        (cleaned, legacy["id"]),
                    )
        self.conn.execute("UPDATE profiles SET last_run_at=?, updated_at=? WHERE id=?", (now, now, profile_id))
        self.touch_subject(subject_id)
        self.conn.commit()
        return total_seen, new_count

    def total_for_profile_relation(self, profile_id: int, relation: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(DISTINCT contact_id) AS n FROM contact_edges WHERE profile_id=? AND relation=?",
            (profile_id, relation),
        ).fetchone()
        return int(row["n"] if row else 0)

    def upsert_associated_people(
        self,
        subject_id: int,
        profile_id: int,
        records: Iterable[dict[str, Any]],
    ) -> tuple[int, int, int]:
        """Save evidence-backed people without inventing relationship edges.

        Name-only records remain explicit unresolved associations. A record is
        promoted into ``contacts`` only when the browser/Codex validator supplied
        a real canonical person profile URL.
        """
        seen: set[str] = set()
        total_seen = 0
        new_associations = 0
        new_contacts = 0
        now = utc_now()
        for record in records:
            normalized_name = str(record.get("normalized_name") or "").strip().casefold()
            display_name = str(record.get("display_name") or "").strip()
            if not normalized_name or not display_name or normalized_name in seen:
                continue
            seen.add(normalized_name)
            total_seen += 1

            contact_id: int | None = None
            canonical = str(record.get("canonical_profile_url") or "").strip().rstrip("/")
            canonical_platform = str(record.get("canonical_platform") or "").strip()
            if canonical and canonical_platform:
                existing_contact = self.conn.execute(
                    "SELECT * FROM contacts WHERE subject_id=? AND platform=? AND lower(canonical_url)=lower(?)",
                    (subject_id, canonical_platform, canonical),
                ).fetchone()
                if existing_contact:
                    contact_id = int(existing_contact["id"])
                    self.conn.execute(
                        """
                        UPDATE contacts
                        SET username=COALESCE(NULLIF(?,''),username),
                            display_name=COALESCE(NULLIF(?,''),display_name),
                            last_seen_at=?
                        WHERE id=?
                        """,
                        (record.get("username") or "", display_name, now, contact_id),
                    )
                else:
                    cursor = self.conn.execute(
                        """
                        INSERT INTO contacts(
                            subject_id,platform,canonical_url,username,display_name,platform_user_id,
                            avatar_url,first_seen_at,last_seen_at
                        ) VALUES(?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            subject_id, canonical_platform, canonical, record.get("username"),
                            display_name, None, None, now, now,
                        ),
                    )
                    contact_id = int(cursor.lastrowid)
                    new_contacts += 1

            existing = self.conn.execute(
                """
                SELECT id,contact_id FROM associated_people
                WHERE subject_id=? AND profile_id=? AND normalized_name=?
                """,
                (subject_id, profile_id, normalized_name),
            ).fetchone()
            if existing:
                self.conn.execute(
                    """
                    UPDATE associated_people
                    SET contact_id=COALESCE(?,contact_id),display_name=?,role=?,organization=?,
                        source_url=?,evidence_text=?,extraction_source=?,
                        canonical_profile_url=COALESCE(NULLIF(?,''),canonical_profile_url),
                        canonical_platform=COALESCE(NULLIF(?,''),canonical_platform),
                        last_seen_at=?,times_seen=times_seen+1
                    WHERE id=?
                    """,
                    (
                        contact_id, display_name, record.get("role"), record.get("organization"),
                        record.get("source_url") or "", record.get("evidence_text") or "",
                        record.get("extraction_source") or "codex_rendered_page", canonical,
                        canonical_platform, now, existing["id"],
                    ),
                )
            else:
                self.conn.execute(
                    """
                    INSERT INTO associated_people(
                        subject_id,profile_id,contact_id,normalized_name,display_name,role,organization,
                        source_url,evidence_text,extraction_source,canonical_profile_url,canonical_platform,
                        first_seen_at,last_seen_at,times_seen
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
                    """,
                    (
                        subject_id, profile_id, contact_id, normalized_name, display_name,
                        record.get("role"), record.get("organization"), record.get("source_url") or "",
                        record.get("evidence_text") or "", record.get("extraction_source") or "codex_rendered_page",
                        canonical or None, canonical_platform or None, now, now,
                    ),
                )
                new_associations += 1
        self.conn.execute("UPDATE profiles SET last_run_at=?, updated_at=? WHERE id=?", (now, now, profile_id))
        self.touch_subject(subject_id)
        self.conn.commit()
        return total_seen, new_associations, new_contacts

    def subject_associated_people(self, subject_id: int, source_platform: str | None = None) -> list[sqlite3.Row]:
        sql = """
            SELECT ap.*,p.platform AS source_platform,p.url AS source_profile_url
            FROM associated_people ap
            JOIN profiles p ON p.id=ap.profile_id
            WHERE ap.subject_id=?
        """
        params: list[Any] = [subject_id]
        if source_platform:
            sql += " AND p.platform=?"
            params.append(source_platform)
        sql += " ORDER BY lower(ap.display_name),p.platform,p.url"
        return list(self.conn.execute(sql, params))

    def save_website_result(
        self,
        *,
        run_id: int,
        profile_id: int,
        subject_present: bool,
        people_detected: int,
        new_associations: int,
        new_contacts_added: int,
        status: str,
        reason: str,
        analysis_mode: str,
        author_name: str | None,
        author_entity_type: str | None,
        diagnostics_path: str | None,
        started_at: str,
        completed_at: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO website_results(
                run_id,profile_id,subject_present,people_detected,new_associations,new_contacts_added,
                status,reason,analysis_mode,author_name,author_entity_type,diagnostics_path,
                started_at,completed_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id, profile_id, int(subject_present), people_detected, new_associations,
                new_contacts_added, status, reason, analysis_mode, author_name, author_entity_type,
                diagnostics_path, started_at, completed_at,
            ),
        )
        self.conn.commit()

    def latest_website_results(self, subject_id: int) -> list[sqlite3.Row]:
        latest = self.latest_run(subject_id)
        if not latest:
            return []
        return list(self.conn.execute(
            """
            SELECT wr.*,p.platform,p.url AS source_profile_url
            FROM website_results wr
            JOIN profiles p ON p.id=wr.profile_id
            WHERE wr.run_id=?
            ORDER BY p.platform,p.url
            """,
            (latest["id"],),
        ))

    def save_collection_result(
        self,
        *,
        run_id: int,
        profile_id: int,
        relation: str,
        reported_count: int | None,
        collected_this_run: int,
        new_contacts_added: int,
        total_unique_saved: int,
        status: str,
        reason: str,
        diagnostics_path: str | None,
        started_at: str,
        completed_at: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO collection_results(
                run_id,profile_id,relation,reported_count,collected_this_run,new_contacts_added,
                total_unique_saved,status,reason,diagnostics_path,started_at,completed_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id,
                profile_id,
                relation,
                reported_count,
                collected_this_run,
                new_contacts_added,
                total_unique_saved,
                status,
                reason,
                diagnostics_path,
                started_at,
                completed_at,
            ),
        )
        self.conn.commit()

    def subject_contacts(self, subject_id: int, platform: str | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM contacts WHERE subject_id=?"
        params: list[Any] = [subject_id]
        if platform:
            sql += " AND platform=?"
            params.append(platform)
        sql += " ORDER BY platform, lower(COALESCE(username,canonical_url))"
        return list(self.conn.execute(sql, params))

    def contact_sources(self, contact_id: int) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT e.relation,e.first_seen_at,e.last_seen_at,e.times_seen,
                       p.id AS profile_id,p.platform AS source_platform,p.url AS source_profile_url,
                       'relationship' AS source_type,NULL AS evidence_text
                FROM contact_edges e
                JOIN profiles p ON p.id=e.profile_id
                WHERE e.contact_id=?
                UNION ALL
                SELECT NULL AS relation,ap.first_seen_at,ap.last_seen_at,ap.times_seen,
                       p.id AS profile_id,p.platform AS source_platform,p.url AS source_profile_url,
                       'website_association' AS source_type,ap.evidence_text
                FROM associated_people ap
                JOIN profiles p ON p.id=ap.profile_id
                WHERE ap.contact_id=?
                ORDER BY source_platform,source_profile_url,relation
                """,
                (contact_id, contact_id),
            )
        )

    def latest_results(self, subject_id: int) -> list[sqlite3.Row]:
        latest = self.conn.execute(
            "SELECT id FROM runs WHERE subject_id=? ORDER BY id DESC LIMIT 1",
            (subject_id,),
        ).fetchone()
        if not latest:
            return []
        return list(
            self.conn.execute(
                """
                SELECT cr.*,p.platform,p.url AS source_profile_url,r.run_stamp,r.started_at AS run_started_at,
                       r.completed_at AS run_completed_at
                FROM collection_results cr
                JOIN profiles p ON p.id=cr.profile_id
                JOIN runs r ON r.id=cr.run_id
                WHERE cr.run_id=?
                ORDER BY p.platform,p.url,cr.relation
                """,
                (latest["id"],),
            )
        )

    def latest_relationship_results(self, subject_id: int) -> list[sqlite3.Row]:
        """Return the latest known result for every profile/relationship pair.

        ``latest_results`` is intentionally scoped to one run.  Subject exports
        need a cumulative view instead: a later run may not attempt every saved
        profile, but its last known displayed count and collection status must
        still remain visible in the final data.
        """
        return list(
            self.conn.execute(
                """
                SELECT cr.*,p.platform,p.url AS source_profile_url,
                       r.run_stamp,r.started_at AS run_started_at,
                       r.completed_at AS run_completed_at
                FROM collection_results cr
                JOIN profiles p ON p.id=cr.profile_id
                JOIN runs r ON r.id=cr.run_id
                WHERE p.subject_id=?
                  AND cr.id=(
                      SELECT cr2.id
                      FROM collection_results cr2
                      WHERE cr2.profile_id=cr.profile_id
                        AND cr2.relation=cr.relation
                      ORDER BY cr2.run_id DESC,cr2.id DESC
                      LIMIT 1
                  )
                ORDER BY p.platform,p.url,cr.relation
                """,
                (subject_id,),
            )
        )

    def all_profile_totals(self, subject_id: int) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT p.id AS profile_id,p.platform,p.url AS source_profile_url,e.relation,
                       COUNT(DISTINCT e.contact_id) AS total_unique_saved
                FROM profiles p
                LEFT JOIN contact_edges e ON e.profile_id=p.id
                WHERE p.subject_id=?
                GROUP BY p.id,e.relation
                ORDER BY p.platform,p.url,e.relation
                """,
                (subject_id,),
            )
        )

    def latest_run(self, subject_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM runs WHERE subject_id=? ORDER BY id DESC LIMIT 1",
            (subject_id,),
        ).fetchone()


    def audit_subject(self, subject_id: int) -> dict[str, Any]:
        duplicate_contacts = list(
            self.conn.execute(
                """
                SELECT platform,lower(rtrim(canonical_url,'/')) AS canonical_key,COUNT(*) AS n
                FROM contacts
                WHERE subject_id=?
                GROUP BY platform,canonical_key
                HAVING COUNT(*)>1
                ORDER BY platform,canonical_key
                """,
                (subject_id,),
            )
        )
        duplicate_edges = list(
            self.conn.execute(
                """
                SELECT e.contact_id,e.profile_id,e.relation,COUNT(*) AS n
                FROM contact_edges e
                JOIN contacts c ON c.id=e.contact_id
                WHERE c.subject_id=?
                GROUP BY e.contact_id,e.profile_id,e.relation
                HAVING COUNT(*)>1
                ORDER BY e.contact_id,e.profile_id,e.relation
                """,
                (subject_id,),
            )
        )
        contact_count = self.conn.execute(
            "SELECT COUNT(*) AS n FROM contacts WHERE subject_id=?",
            (subject_id,),
        ).fetchone()["n"]
        edge_count = self.conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM contact_edges e JOIN contacts c ON c.id=e.contact_id
            WHERE c.subject_id=?
            """,
            (subject_id,),
        ).fetchone()["n"]
        associated_mentions = self.conn.execute(
            "SELECT COUNT(*) AS n FROM associated_people WHERE subject_id=?",
            (subject_id,),
        ).fetchone()["n"]
        associated_unique = self.conn.execute(
            "SELECT COUNT(DISTINCT normalized_name) AS n FROM associated_people WHERE subject_id=?",
            (subject_id,),
        ).fetchone()["n"]
        return {
            "contacts": int(contact_count),
            "edges": int(edge_count),
            "associated_people_unique": int(associated_unique),
            "associated_people_mentions": int(associated_mentions),
            "duplicate_contact_keys": [dict(row) for row in duplicate_contacts],
            "duplicate_edges": [dict(row) for row in duplicate_edges],
        }

    def purge_latest_untrusted_results(
        self,
        subject_id: int,
        statuses: tuple[str, ...] = ("review", "failed", "blocked"),
    ) -> dict[str, int]:
        """Remove edges first introduced by untrusted results in the latest run.

        Contacts that still have another trusted edge are preserved. Orphan contacts are
        deleted after the affected edges are removed.
        """
        latest = self.latest_run(subject_id)
        if not latest:
            return {"results": 0, "edges": 0, "contacts": 0}

        placeholders = ",".join("?" for _ in statuses)
        results = list(
            self.conn.execute(
                f"""
                SELECT cr.id,cr.profile_id,cr.relation,cr.status
                FROM collection_results cr
                WHERE cr.run_id=? AND cr.status IN ({placeholders})
                """,
                (latest["id"], *statuses),
            )
        )
        deleted_edges = 0
        for result in results:
            cursor = self.conn.execute(
                """
                DELETE FROM contact_edges
                WHERE profile_id=? AND relation=? AND first_seen_at>=?
                """,
                (result["profile_id"], result["relation"], latest["started_at"]),
            )
            deleted_edges += max(cursor.rowcount, 0)
            total = self.total_for_profile_relation(result["profile_id"], result["relation"])
            self.conn.execute(
                """
                UPDATE collection_results
                SET new_contacts_added=0,total_unique_saved=?,
                    reason=reason || '; untrusted records purged from database'
                WHERE id=?
                """,
                (total, result["id"]),
            )

        orphan_cursor = self.conn.execute(
            """
            DELETE FROM contacts
            WHERE subject_id=?
              AND NOT EXISTS (SELECT 1 FROM contact_edges e WHERE e.contact_id=contacts.id)
              AND NOT EXISTS (SELECT 1 FROM associated_people ap WHERE ap.contact_id=contacts.id)
            """,
            (subject_id,),
        )
        deleted_contacts = max(orphan_cursor.rowcount, 0)
        self.touch_subject(subject_id)
        self.conn.commit()
        return {"results": len(results), "edges": deleted_edges, "contacts": deleted_contacts}

    def incomplete_latest_results(self, subject_id: int) -> list[sqlite3.Row]:
        return [
            row for row in self.latest_results(subject_id)
            if row["status"] not in SUCCESS_STATUSES
        ]
