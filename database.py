import sqlite3
import os
from datetime import datetime, timezone
from pathlib import Path
import bcrypt
from contextlib import contextmanager

DB_PATH = Path(__file__).parent / "asclepius.db"


def to_iso(moment: datetime) -> str:
    """Timestamp format of the case tables: fixed-width UTC, so string order is time order."""
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def utc_now_iso() -> str:
    return to_iso(datetime.now(timezone.utc))

def get_db_connection():
    """Create and return a database connection."""
    # timeout makes writers wait on a held lock instead of failing with "database is locked".
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Safe with WAL: a crash can lose the last commit but never corrupts the database.
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn

@contextmanager
def get_db():
    """Context manager for database connections."""
    conn = get_db_connection()
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def query(sql: str, params: tuple = ()):
    """Execute a SELECT query and return all results."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

def run(sql: str, params: tuple = ()):
    """Execute an INSERT/UPDATE/DELETE query and return lastrowid and changes."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        return {"id": cursor.lastrowid, "changes": cursor.rowcount}

# Messages between the EMT and the receiving hospital team. Each message belongs to exactly
# one thread: a case (case_id) or, for the older single-recording flow, a recording (recording_id).
MESSAGES_TABLE = """
    CREATE TABLE IF NOT EXISTS {name} (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      recording_id INTEGER,
      case_id INTEGER,
      sender_id INTEGER NOT NULL,
      sender_role TEXT NOT NULL CHECK (sender_role IN ('emt', 'doctor')),
      body TEXT NOT NULL CHECK (length(trim(body)) > 0),
      client_id TEXT,
      created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
      CHECK ((recording_id IS NULL) != (case_id IS NULL)),
      FOREIGN KEY (recording_id) REFERENCES recordings (id) ON DELETE CASCADE,
      FOREIGN KEY (case_id) REFERENCES cases (id) ON DELETE CASCADE,
      FOREIGN KEY (sender_id) REFERENCES users (id)
    )"""

def init_database():
    """Create database tables if they do not exist. Does not seed users."""
    print("✅ SQLite database connected successfully")
    print(f"📁 Database file: {DB_PATH}")
    
    schema = """
    -- Users table (EMTs and Doctors)
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      username TEXT UNIQUE NOT NULL,
      email TEXT UNIQUE NOT NULL,
      password_hash TEXT NOT NULL,
      role TEXT NOT NULL CHECK (role IN ('emt', 'doctor')),
      first_name TEXT NOT NULL,
      last_name TEXT NOT NULL,
      phone TEXT,
      specialty TEXT,
      is_available BOOLEAN DEFAULT 1,
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );

    -- Recordings table
    CREATE TABLE IF NOT EXISTS recordings (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      emt_id INTEGER,
      patient_info TEXT,
      audio_file_path TEXT NOT NULL,
      transcription TEXT,
      llm_summary TEXT,
      urgency_level TEXT DEFAULT 'medium' CHECK (urgency_level IN ('low', 'medium', 'high', 'critical')),
      risk_score INTEGER DEFAULT 5 CHECK (risk_score >= 0 AND risk_score <= 10),
      priority_level INTEGER DEFAULT 3 CHECK (priority_level >= 1 AND priority_level <= 5),
      chief_complaint TEXT,
      vital_signs TEXT,
      symptoms TEXT,
      recommended_actions TEXT,
      critical_info TEXT,
      status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'completed', 'notified', 'error')),
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY (emt_id) REFERENCES users (id)
    );

    -- Notifications table
    CREATE TABLE IF NOT EXISTS notifications (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      recording_id INTEGER,
      doctor_id INTEGER,
      notification_type TEXT NOT NULL CHECK (notification_type IN ('sms', 'email', 'both')),
      sent_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      delivered BOOLEAN DEFAULT 0,
      read_at DATETIME,
      response TEXT,
      FOREIGN KEY (recording_id) REFERENCES recordings (id),
      FOREIGN KEY (doctor_id) REFERENCES users (id)
    );

    -- Receiving hospitals an EMT can route a case to.
    CREATE TABLE IF NOT EXISTS hospitals (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      code TEXT UNIQUE NOT NULL,
      name TEXT NOT NULL
    );

    -- EMS cases: one per patient transport. Transcript segments, typed updates, vitals,
    -- risk assessments, messages and acknowledgments all belong to the case.
    -- status is whether the EMT still has the case open; arrived_at marks hand-over.
    -- info_version goes up by one whenever new patient information arrives, so assessments
    -- and acknowledgments can record exactly which information they covered.
    -- Older databases get the columns after updated_at from migrate_existing_tables().
    CREATE TABLE IF NOT EXISTS cases (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      emt_id INTEGER NOT NULL,
      patient_info TEXT,
      status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'closed')),
      started_at TEXT NOT NULL,
      closed_at TEXT,
      updated_at TEXT NOT NULL,
      destination_hospital_id INTEGER REFERENCES hospitals (id),
      ems_unit TEXT,
      eta_at TEXT,
      info_version INTEGER NOT NULL DEFAULT 0,
      last_update_at TEXT,
      arrived_at TEXT,
      FOREIGN KEY (emt_id) REFERENCES users (id)
    );

    -- Patient information the EMT adds after creating the case. Append-only: a correction
    -- is a new row, so earlier information is never overwritten. info_version is the
    -- case's version once this update was applied.
    CREATE TABLE IF NOT EXISTS case_updates (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      case_id INTEGER NOT NULL,
      info_version INTEGER NOT NULL,
      kind TEXT NOT NULL CHECK (kind IN ('note', 'vitals', 'correction', 'eta')),
      body TEXT,
      eta_at TEXT,
      author_id INTEGER NOT NULL,
      client_id TEXT,
      created_at TEXT NOT NULL,
      FOREIGN KEY (case_id) REFERENCES cases (id),
      FOREIGN KEY (author_id) REFERENCES users (id)
    );

    -- One row per vital sign reading, so a trend like SpO2 96 -> 91 -> 86 is kept.
    -- Each name has a fixed unit (VITAL_SIGNS in case_assessment.py).
    CREATE TABLE IF NOT EXISTS vital_readings (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      case_id INTEGER NOT NULL,
      update_id INTEGER NOT NULL,
      name TEXT NOT NULL,
      value REAL NOT NULL,
      measured_at TEXT NOT NULL,
      FOREIGN KEY (case_id) REFERENCES cases (id),
      FOREIGN KEY (update_id) REFERENCES case_updates (id)
    );

    -- Every risk/priority assessment attempt for a case, kept as history.
    -- based_on_version is the case info_version the assessment read. The current assessment
    -- is the usable one with the highest based_on_version, so a slow result for older
    -- information can never replace a newer one.
    -- risk_score and priority_level stay NULL unless the scorer returned valid values:
    -- an unknown score is never stored as a normal-looking default.
    CREATE TABLE IF NOT EXISTS risk_assessments (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      case_id INTEGER NOT NULL,
      based_on_version INTEGER NOT NULL,
      status TEXT NOT NULL CHECK (status IN ('processing', 'completed', 'failed', 'needs_review')),
      risk_score INTEGER CHECK (risk_score IS NULL OR (risk_score >= 0 AND risk_score <= 10)),
      priority_level INTEGER CHECK (priority_level IS NULL OR (priority_level >= 1 AND priority_level <= 5)),
      chief_complaint TEXT,
      summary TEXT,
      critical_info TEXT,
      review_reason TEXT,
      error TEXT,
      scorer_version TEXT NOT NULL,
      started_at TEXT NOT NULL,
      completed_at TEXT,
      updated_at TEXT NOT NULL,
      FOREIGN KEY (case_id) REFERENCES cases (id)
    );

    -- A hospital user confirming they have seen the case's information up to info_version.
    CREATE TABLE IF NOT EXISTS case_acknowledgments (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      case_id INTEGER NOT NULL,
      user_id INTEGER NOT NULL,
      info_version INTEGER NOT NULL,
      acknowledged_at TEXT NOT NULL,
      FOREIGN KEY (case_id) REFERENCES cases (id),
      FOREIGN KEY (user_id) REFERENCES users (id)
    );

    -- Transcript segments: short audio chunks transcribed independently.
    -- seq is assigned by the EMT client and defines chronological order within a case;
    -- client_id identifies one recorded clip so a re-upload is told apart from a seq collision.
    -- updated_at changes on every state change so clients can keep the newest version of a row.
    CREATE TABLE IF NOT EXISTS transcript_segments (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      case_id INTEGER NOT NULL,
      seq INTEGER NOT NULL,
      client_id TEXT NOT NULL,
      recorded_at TEXT NOT NULL,
      duration_ms INTEGER,
      audio_file_path TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'completed', 'failed')),
      text TEXT,
      error TEXT,
      attempts INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL,
      transcribed_at TEXT,
      updated_at TEXT NOT NULL,
      UNIQUE (case_id, seq),
      FOREIGN KEY (case_id) REFERENCES cases (id)
    );

    -- AI-extracted findings, each traceable to the transcript segment it came from.
    -- Not populated yet; reserved for the findings-extraction feature.
    CREATE TABLE IF NOT EXISTS case_findings (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      case_id INTEGER NOT NULL,
      segment_id INTEGER,
      finding_type TEXT NOT NULL,
      value TEXT NOT NULL,
      source_text TEXT,
      created_at TEXT NOT NULL,
      FOREIGN KEY (case_id) REFERENCES cases (id),
      FOREIGN KEY (segment_id) REFERENCES transcript_segments (id)
    );

    """ + MESSAGES_TABLE.format(name="messages") + """;

    -- Create indexes for better performance
    CREATE INDEX IF NOT EXISTS idx_cases_status ON cases(status);
    CREATE UNIQUE INDEX IF NOT EXISTS idx_cases_one_active_per_emt ON cases(emt_id) WHERE status = 'active';
    CREATE INDEX IF NOT EXISTS idx_cases_emt_id ON cases(emt_id);
    CREATE INDEX IF NOT EXISTS idx_cases_destination ON cases(destination_hospital_id, status);
    CREATE INDEX IF NOT EXISTS idx_case_findings_segment_id ON case_findings(segment_id);
    CREATE INDEX IF NOT EXISTS idx_case_updates_case ON case_updates(case_id, id);
    -- A retried update with the same client_id returns the stored row instead of a duplicate.
    CREATE UNIQUE INDEX IF NOT EXISTS idx_case_updates_client_id ON case_updates(case_id, client_id) WHERE client_id IS NOT NULL;
    CREATE INDEX IF NOT EXISTS idx_vital_readings_case ON vital_readings(case_id, name, id);
    CREATE INDEX IF NOT EXISTS idx_risk_assessments_case ON risk_assessments(case_id, based_on_version);
    CREATE INDEX IF NOT EXISTS idx_case_acknowledgments_case ON case_acknowledgments(case_id, info_version);
    CREATE INDEX IF NOT EXISTS idx_messages_recording ON messages(recording_id, id);
    CREATE INDEX IF NOT EXISTS idx_messages_case ON messages(case_id, id);
    -- A retried send with the same client_id returns the original message instead of a duplicate.
    CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_client_id ON messages(sender_id, client_id) WHERE client_id IS NOT NULL;
    CREATE INDEX IF NOT EXISTS idx_recordings_emt_id ON recordings(emt_id);
    CREATE INDEX IF NOT EXISTS idx_recordings_status ON recordings(status);
    CREATE INDEX IF NOT EXISTS idx_notifications_doctor_id ON notifications(doctor_id);
    CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
    CREATE INDEX IF NOT EXISTS idx_users_available ON users(is_available);
    """
    
    with get_db() as conn:
        # WAL lets the SSE readers poll while a message is being written; the mode persists in the file.
        journal_mode = conn.execute("PRAGMA journal_mode = WAL").fetchone()[0]
        print(f"✅ SQLite journal mode: {journal_mode}")
        migrate_existing_tables(conn)
        conn.executescript(schema)
        if conn.execute("SELECT COUNT(*) FROM hospitals").fetchone()[0] == 0:
            conn.executemany("INSERT INTO hospitals (code, name) VALUES (?, ?)", DEMO_HOSPITALS)
        print("✅ Database schema created successfully")


# Destinations offered to EMTs until real hospitals are configured. Only inserted into an empty table.
DEMO_HOSPITALS = [
    ("GEN", "General Hospital (demo)"),
    ("NORTH", "Northside Medical Center (demo)"),
    ("CHILD", "Children's Hospital (demo)"),
]

# Columns added to `cases` after it first shipped; ALTER TABLE adds them to older databases.
ADDED_CASE_COLUMNS = [
    ("destination_hospital_id", "INTEGER REFERENCES hospitals (id)"),
    ("ems_unit", "TEXT"),
    ("eta_at", "TEXT"),
    ("info_version", "INTEGER NOT NULL DEFAULT 0"),
    ("last_update_at", "TEXT"),
    ("arrived_at", "TEXT"),
]


def table_columns(conn, table: str) -> set:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def migrate_existing_tables(conn):
    """Bring tables created by an earlier version of the schema up to date. No-op on a new database."""
    existing = table_columns(conn, "cases")
    if existing:
        for name, definition in ADDED_CASE_COLUMNS:
            if name not in existing:
                conn.execute(f"ALTER TABLE cases ADD COLUMN {name} {definition}")
        if "info_version" not in existing:
            # Older cases already hold information; give them a version so acknowledgments can refer to it.
            conn.execute("UPDATE cases SET info_version = 1, last_update_at = updated_at")

    message_columns = table_columns(conn, "messages")
    if message_columns and "case_id" not in message_columns:
        # recording_id was NOT NULL; SQLite can only relax that by rebuilding the table.
        # Foreign keys are off while copying (SQLite's documented procedure), so rows written
        # before enforcement was on are carried over instead of aborting startup.
        conn.commit()
        conn.executescript(
            "PRAGMA foreign_keys = OFF; BEGIN;"
            + MESSAGES_TABLE.format(name="messages_new")
            + """;
            INSERT INTO messages_new (id, recording_id, sender_id, sender_role, body, client_id, created_at)
              SELECT id, recording_id, sender_id, sender_role, body, client_id, created_at FROM messages;
            DROP TABLE messages;
            ALTER TABLE messages_new RENAME TO messages;
            COMMIT; PRAGMA foreign_keys = ON;"""
        )

def insert_sample_data():
    """Insert sample data with proper password hashes."""
    try:
        # All demo accounts share the password "password123"
        password_hash = bcrypt.hashpw("password123".encode('utf-8'), bcrypt.gensalt(rounds=10)).decode('utf-8')
        
        sample_users = [
            ('dr.smith', 'dr.smith@hospital.com', password_hash, 'doctor', 'John', 'Smith', '+1234567890', 'Emergency Medicine'),
            ('dr.jones', 'dr.jones@hospital.com', password_hash, 'doctor', 'Sarah', 'Jones', '+1234567891', 'Cardiology'),
            ('emt.wilson', 'emt.wilson@ems.com', password_hash, 'emt', 'Mike', 'Wilson', '+1234567892', None),
            ('emt.garcia', 'emt.garcia@ems.com', password_hash, 'emt', 'Maria', 'Garcia', '+1234567893', None)
        ]
        
        with get_db() as conn:
            cursor = conn.cursor()
            for user in sample_users:
                cursor.execute(
                    'INSERT OR IGNORE INTO users (username, email, password_hash, role, first_name, last_name, phone, specialty) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                    user
                )
        
        print("✅ Sample data inserted successfully with proper passwords")
        print("🔑 Demo accounts all use password: password123")
        
    except Exception as error:
        print(f"❌ Error creating sample data: {error}")

if __name__ == "__main__":
    # `python3 database.py --seed` creates the schema and the demo users.
    import sys
    init_database()
    if "--seed" in sys.argv:
        insert_sample_data()
