import sqlite3
import os
from pathlib import Path
import bcrypt
from contextlib import contextmanager

DB_PATH = Path(__file__).parent / "asclepius.db"

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

    -- Case messages between the EMT and the receiving hospital team.
    -- recording_id is the case; every message belongs to exactly one.
    CREATE TABLE IF NOT EXISTS messages (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      recording_id INTEGER NOT NULL,
      sender_id INTEGER NOT NULL,
      sender_role TEXT NOT NULL CHECK (sender_role IN ('emt', 'doctor')),
      body TEXT NOT NULL CHECK (length(trim(body)) > 0),
      client_id TEXT,
      created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
      FOREIGN KEY (recording_id) REFERENCES recordings (id) ON DELETE CASCADE,
      FOREIGN KEY (sender_id) REFERENCES users (id)
    );

    -- Create indexes for better performance
    CREATE INDEX IF NOT EXISTS idx_messages_recording ON messages(recording_id, id);
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
        conn.executescript(schema)
        print("✅ Database schema created successfully")

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
