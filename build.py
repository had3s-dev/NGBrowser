import os
import shutil
import PyInstaller.__main__
import sys

def build():
    # Clean up previous build artifacts
    print("Cleaning up previous build...")
    for item in ['build', 'dist', 'NGBrowser.spec']:
        if os.path.exists(item):
            try:
                if os.path.isdir(item):
                    shutil.rmtree(item)
                else:
                    os.remove(item)
                print(f"Removed {item}")
            except PermissionError:
                print(f"Warning: Could not remove {item} (permission denied). Continuing...")
            except Exception as e:
                print(f"Warning: Could not remove {item}: {e}. Continuing...")
    
    # Ensure rclone.exe exists in the script directory
    if not os.path.exists('rclone.exe'):
        print("Error: rclone.exe not found in the current directory.")
        print("Please copy rclone.exe to this directory and try again.")
        return
    
    # Prepare PyInstaller command
    print("Building executable with PyInstaller...")
    cmd = [
        '--name=NGBrowser',
        '--onefile',
        '--windowed',
        '--add-data=rclone.exe;.'
    ]
    
    # Add rclone.conf if it exists
    if os.path.exists('rclone.conf'):
        cmd.append('--add-data=rclone.conf;.')
    
    # Add icon if it exists
    if os.path.exists('NGBrowser.ico'):
        cmd.extend(['--icon=NGBrowser.ico'])
    
    # Add the main script
    cmd.append('rclone_gui.py')
    
    # Run PyInstaller with the prepared command
    PyInstaller.__main__.run(cmd)
    
    print("\nBuild complete! The executable is in the 'dist' folder.")
    print("IMPORTANT: Ensure rclone.conf is in the same directory as the executable.")
    print("You can test it by running: dist\\NGBrowser.exe")

if __name__ == "__main__":
    build()
