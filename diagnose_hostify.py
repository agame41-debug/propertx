#!/usr/bin/env python
"""Diagnose Hostify sync issues"""
import sys
import os
import logging

# Add project root to path
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

# Enable debug logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

from report.db import get_connection
from report.config import load_runtime_config, get_all_properties
from report.hostify_sync import HostifySyncTask, compute_sync_months

try:
    db_path = os.path.join(project_root, "cache", "rentero.db")
    config_path = os.path.join(project_root, "config", "properties.json")
    
    print("=" * 60)
    print("HOSTIFY SYNC DIAGNOSTICS")
    print("=" * 60)
    
    # Test 1: Check database
    print("\n1. Testing database connection...")
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM report_objects")
    count = cursor.fetchone()[0]
    print(f"   ✓ Database has {count} report objects")
    
    # Test 2: Load config
    print("\n2. Loading runtime config...")
    config = load_runtime_config(config_path, db_conn=conn)
    props = get_all_properties(config)
    print(f"   ✓ Found {len(props)} properties in config")
    for prop in props:
        print(f"     - {prop['slug']}: active={prop.get('active', True)}, listing_id={prop.get('listing_id')}")
    
    # Test 3: Compute sync months
    print("\n3. Computing sync months...")
    months = compute_sync_months()
    print(f"   ✓ Will sync for {len(months)} months:")
    for year, month in months:
        print(f"     - {year}/{month:02d}")
    
    # Test 4: Check Hostify API key
    print("\n4. Checking Hostify API key...")
    from hostify_api import hostify_api_key
    key = hostify_api_key()
    if key:
        print(f"   ✓ API key is set (length: {len(key)})")
    else:
        print("   ✗ WARNING: Hostify API key is not set!")
        print("            Set HOSTIFY_API_KEY environment variable")
    
    # Test 5: Try running sync once
    print("\n5. Running sync once...")
    task = HostifySyncTask(db_path=db_path, config=config, config_path=config_path)
    task._sync_once(conn=conn)
    print("   ✓ Sync completed without exceptions")
    
    # Test 6: Check generated records
    print("\n6. Checking database after sync...")
    cursor.execute("SELECT COUNT(*) FROM hostify_reservations")
    res_count = cursor.fetchone()[0]
    print(f"   ✓ Database now has {res_count} Hostify reservations")
    
    conn.close()
    print("\n" + "=" * 60)
    print("✓ All diagnostics completed successfully!")
    print("=" * 60)
    
except Exception as e:
    print(f"\n✗ Error: {e}", file=sys.stderr)
    import traceback
    traceback.print_exc(file=sys.stderr)
    sys.exit(1)
