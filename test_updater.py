#!/usr/bin/env python3
"""Test script to debug auto-updater issues"""

import requests
import json
from datetime import datetime

def test_auto_updater():
    print("NGBrowser Auto-Updater Debug Test")
    print("=" * 50)
    
    # Test GitHub API endpoint
    github_url = "https://api.github.com/repos/had3s-dev/NGBrowser/releases/latest"
    
    print(f"Testing GitHub API: {github_url}")
    
    try:
        headers = {
            'User-Agent': 'NGBrowser/1.0.0',
            'Accept': 'application/vnd.github.v3+json'
        }
        
        response = requests.get(github_url, headers=headers, timeout=10)
        print(f"   Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"   OK API Response received")
            print(f"   Latest Release: {data.get('tag_name', 'N/A')}")
            print(f"   Published: {data.get('published_at', 'N/A')}")
            
            # Check assets
            assets = data.get('assets', [])
            print(f"   Assets ({len(assets)}):")
            for asset in assets:
                print(f"      - {asset['name']} ({asset['size']} bytes)")
                
            # Check for executable
            exe_assets = [a for a in assets if a['name'].endswith('.exe') or a['name'].endswith('.zip')]
            if exe_assets:
                print(f"   OK Found {len(exe_assets)} executable asset(s)")
            else:
                print(f"   ERROR No executable assets found")
                
        elif response.status_code == 404:
            print(f"   ERROR Repository not found or no releases")
        else:
            print(f"   ERROR HTTP Error: {response.status_code}")
            print(f"   Response: {response.text}")
            
    except requests.RequestException as e:
        print(f"   ERROR Network Error: {str(e)}")
    except Exception as e:
        print(f"   ERROR Error: {str(e)}")
    
    # Test version comparison
    print(f"\nVersion Comparison Test")
    current_version = "1.0.0"
    test_versions = ["1.0.1", "1.1.0", "2.0.0", "0.9.0"]
    
    def version_tuple(v):
        return tuple(map(int, v.split('.')))
    
    for test_ver in test_versions:
        is_newer = version_tuple(test_ver) > version_tuple(current_version)
        print(f"   {current_version} vs {test_ver}: {'Newer' if is_newer else 'Same/Older'}")
    
    # Check last update check timestamp
    print(f"\nUpdate Check Timestamp Analysis")
    timestamp = 1752878407.5206954
    try:
        check_date = datetime.fromtimestamp(timestamp)
        now = datetime.now()
        print(f"   Last Check: {check_date}")
        print(f"   Current Time: {now}")
        print(f"   Time Difference: {now - check_date}")
        
        if check_date > now:
            print(f"   ⚠️  WARNING: Last check time is in the future!")
        
    except Exception as e:
        print(f"   ❌ Timestamp Error: {str(e)}")

if __name__ == "__main__":
    test_auto_updater()
