#!/usr/bin/env python3
"""Force update check for NGBrowser"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from auto_updater import AutoUpdater

def force_update_check():
    print("NGBrowser Force Update Check")
    print("=" * 40)
    
    # Create auto-updater with current version
    current_version = "1.0.0"  # Same as in main app
    updater = AutoUpdater(current_version)
    
    print(f"Current Version: {current_version}")
    print("Checking for updates...")
    
    # Check for updates
    if updater.check_for_updates():
        print(f"UPDATE AVAILABLE!")
        print(f"New Version: {updater.new_version}")
        print(f"Download URL: {updater.download_url}")
        print(f"Changelog: {updater.changelog}")
    else:
        print("No updates available or error occurred")

if __name__ == "__main__":
    force_update_check()
