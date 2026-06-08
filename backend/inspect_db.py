import os
import sqlite3

# Dynamically locate DB_PATH relative to the script location
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "trading.db")

def print_table(title, cursor, query):
    print(f"\n=== {title} ===")
    try:
        cursor.execute(query)
        rows = cursor.fetchall()
        if not rows:
            print("No records found.")
            return
        
        # Get column names
        col_names = [description[0] for description in cursor.description]
        # Calculate max width for each column
        widths = [len(col) for col in col_names]
        for row in rows:
            for idx, val in enumerate(row):
                widths[idx] = max(widths[idx], len(str(val if val is not None else "NULL")))
                
        # Print header
        header_row = " | ".join(f"{col_names[i]:<{widths[i]}}" for i in range(len(col_names)))
        print(header_row)
        print("-" * len(header_row))
        
        # Print rows
        for row in rows:
            print(" | ".join(f"{str(row[i] if row[i] is not None else 'NULL'):<{widths[i]}}" for i in range(len(col_names))))
    except Exception as e:
        print(f"Error querying table: {e}")

def main():
    if not os.path.exists(DB_PATH):
        print(f"Database file not found at {DB_PATH}. Run python main.py first to initialize the database.")
        return
        
    print(f"Connecting to database: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. Inspect Accounts
    print_table("ACCOUNTS", cursor, "SELECT asset, balance, locked FROM accounts WHERE balance > 0.0 OR locked > 0.0")
    
    # 2. Inspect Positions
    print_table("ACTIVE POSITIONS", cursor, "SELECT symbol, size, avg_price, stop_loss_price, take_profit_price FROM positions WHERE size != 0.0")
    
    # 3. Inspect Orders (Latest 10)
    print_table("ORDERS (Latest 10)", cursor, "SELECT id, symbol, type, side, price, quantity, status, timestamp FROM orders ORDER BY timestamp DESC LIMIT 10")
    
    conn.close()

if __name__ == "__main__":
    main()
