import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading.db")

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    
    # Enable foreign keys
    cursor.execute("PRAGMA foreign_keys = ON;")
    
    # Create accounts table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            asset TEXT PRIMARY KEY,
            balance REAL NOT NULL DEFAULT 0.0,
            locked REAL NOT NULL DEFAULT 0.0
        )
    """)
    
    # Create orders table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            type TEXT NOT NULL,       -- LIMIT, MARKET
            side TEXT NOT NULL,       -- BUY, SELL
            price REAL,
            quantity REAL NOT NULL,
            status TEXT NOT NULL,     -- PENDING, FILLED, CANCELED, REJECTED
            filled_quantity REAL DEFAULT 0.0,
            average_fill_price REAL DEFAULT 0.0,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Create positions table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            symbol TEXT PRIMARY KEY,
            size REAL NOT NULL DEFAULT 0.0,
            avg_price REAL NOT NULL DEFAULT 0.0
        )
    """)
    
    conn.commit()
    
    # Seed/Migrate accounts individually to preserve existing data while adding new assets
    assets_to_seed = [
        ('USD', 100000.0),
        ('USDT', 100000.0),
        ('BTC', 0.0),
        ('ETH', 0.0),
        ('AAPL', 0.0),
        ('TSLA', 0.0),
        ('MSFT', 0.0)
    ]
    for asset, initial_balance in assets_to_seed:
        cursor.execute("SELECT COUNT(*) FROM accounts WHERE asset = ?", (asset,))
        if cursor.fetchone()[0] == 0:
            cursor.execute("INSERT INTO accounts (asset, balance, locked) VALUES (?, ?, 0.0)", (asset, initial_balance))
    conn.commit()
        
    conn.close()

if __name__ == "__main__":
    init_db()
    print("Database initialized successfully at:", DB_PATH)
