import sqlite3
import sys

try:
    conn = sqlite3.connect('rentero.db')
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table'")
    print("Tables in database:")
    for row in c.fetchall():
        print(f"  - {row[0]}")
    
    # Try to get generation jobs
    try:
        c.execute("SELECT id, slug, year, month, status FROM generation_jobs ORDER BY id DESC LIMIT 10")
        print("\nRecent generation jobs:")
        for row in c.fetchall():
            print(f"  {row}")
    except Exception as e:
        print(f"\nNo generation_jobs table: {e}")
    
    conn.close()
except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)
