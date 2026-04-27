#!/usr/bin/env python
"""Initialize database for Rentero"""
import sys
import os

# Add project root to path
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

from report.db import get_connection

try:
    print("Initializing database...")
    db_path = os.path.join(project_root, "cache", "rentero.db")
    conn = get_connection(db_path)
    print(f"✓ Database initialized at {db_path}")
    
    # Check tables
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row[0] for row in cursor.fetchall()]
    print(f"✓ Created {len(tables)} tables")
    if len(tables) > 0:
        print(f"  Tables: {', '.join(tables[:5])}{'...' if len(tables) > 5 else ''}")
    
    conn.close()
    print("✓ Done!")
except Exception as e:
    print(f"✗ Error: {e}", file=sys.stderr)
    import traceback
    traceback.print_exc(file=sys.stderr)
    sys.exit(1)
