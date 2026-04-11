#!/usr/bin/env python
"""Test Hostify API connectivity"""
import sys
import os

# Add project root to path
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

# Load .env
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(project_root, '.env'))
except ImportError:
    pass

from hostify_api import hostify_api_key, hostify_request

print("=" * 60)
print("HOSTIFY API CONNECTION TEST")
print("=" * 60)

# Check API key
api_key = hostify_api_key()
print(f"\n1. API Key loaded: {bool(api_key)}")
if api_key:
    print(f"   Key: {api_key[:10]}...{api_key[-5:]}")
else:
    print("   ✗ ERROR: API key not found!")
    sys.exit(1)

# Try a simple request
print("\n2. Testing API connectivity...")
try:
    result = hostify_request("GET", "users/me", timeout=10)
    if result:
        print(f"   ✓ Connected successfully")
        print(f"   Response keys: {list(result.keys())[:5]}")
    else:
        print(f"   ✗ No response data")
except Exception as e:
    print(f"   ✗ API Error: {e}")

print("\n" + "=" * 60)
