import os
import sqlite3
import sys

# Ensure psycopg is available
try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    print("Please install psycopg[binary]: pip install psycopg[binary]")
    sys.exit(1)

SQLITE_DB_PATH = os.environ.get("SQLITE_DB_PATH", "backend/data/trading-sim.db")
POSTGRES_URL = os.environ.get("DATABASE_URL", "postgresql://trading:trading@localhost:5432/trading")

def main():
    print(f"Connecting to SQLite: {SQLITE_DB_PATH}")
    if not os.path.exists(SQLITE_DB_PATH):
        print(f"SQLite DB not found at {SQLITE_DB_PATH}")
        sys.exit(1)

    sqlite_conn = sqlite3.connect(SQLITE_DB_PATH)
    sqlite_conn.row_factory = sqlite3.Row
    sqlite_cur = sqlite_conn.cursor()

    print(f"Connecting to Postgres: {POSTGRES_URL}")
    try:
        pg_conn = psycopg.connect(POSTGRES_URL, autocommit=False)
        pg_cur = pg_conn.cursor()
    except Exception as exc:
        print(f"Failed to connect to Postgres: {exc}")
        print("Make sure Docker containers are running.")
        sys.exit(1)

    # First, make sure postgres schema is initialized.
    print("Ensure postgres schema is initialized by running the app once, or importing the schema...")
    
    # Get all tables from SQLite
    sqlite_cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [row["name"] for row in sqlite_cur.fetchall()]
    
    # Filter out internal sqlite tables
    tables = [t for t in tables if not t.startswith("sqlite_")]

    for table in tables:
        sqlite_cur.execute(f"SELECT * FROM {table};")
        rows = sqlite_cur.fetchall()
        if not rows:
            print(f"Skipping empty table: {table}")
            continue
            
        print(f"Migrating table {table} ({len(rows)} rows)...")
        
        # Get column names
        cols = rows[0].keys()
        col_names = ", ".join(cols)
        placeholders = ", ".join(["%s"] * len(cols))
        
        insert_query = f"INSERT INTO {table} ({col_names}) VALUES ({placeholders}) ON CONFLICT DO NOTHING;"
        
        # Insert rows into postgres
        data = [tuple(row) for row in rows]
        
        try:
            pg_cur.executemany(insert_query, data)
            pg_conn.commit()
            print(f" -> Success: {table}")
        except Exception as exc:
            pg_conn.rollback()
            print(f" -> Error migrating {table}: {exc}")

    print("Migration complete!")
    sqlite_conn.close()
    pg_conn.close()

if __name__ == "__main__":
    main()
