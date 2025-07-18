import os
import shutil
import PyInstaller.__main__
import sys
import glob
import requests
import zipfile
import tempfile
import platform
from pathlib import Path
from urllib.parse import urlparse

def download_file(url, local_path, description):
    """Download a file from URL with progress indication"""
    try:
        print(f"   🔄 Downloading {description}...")
        response = requests.get(url, stream=True)
        response.raise_for_status()
        
        total_size = int(response.headers.get('content-length', 0))
        downloaded = 0
        
        with open(local_path, 'wb') as file:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    file.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        progress = (downloaded / total_size) * 100
                        print(f"\r     Progress: {progress:.1f}%", end='', flush=True)
        
        print(f"\n   ✅ Downloaded {description} to {local_path}")
        return True
        
    except Exception as e:
        print(f"\n   ❌ Failed to download {description}: {str(e)}")
        return False

def download_and_extract_zip(url, extract_to, description, files_to_extract=None):
    """Download and extract specific files from a ZIP archive"""
    try:
        print(f"   🔄 Downloading {description}...")
        
        with tempfile.TemporaryDirectory() as temp_dir:
            zip_path = os.path.join(temp_dir, "download.zip")
            
            # Download ZIP file
            response = requests.get(url, stream=True)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            
            with open(zip_path, 'wb') as file:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        file.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0:
                            progress = (downloaded / total_size) * 100
                            print(f"\r     Download Progress: {progress:.1f}%", end='', flush=True)
            
            print(f"\n   📦 Extracting {description}...")
            
            # Extract ZIP file
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                if files_to_extract:
                    # Extract specific files
                    for file_info in zip_ref.filelist:
                        for target_file in files_to_extract:
                            if target_file in file_info.filename:
                                # Extract to specific location
                                file_info.filename = os.path.basename(file_info.filename)
                                zip_ref.extract(file_info, extract_to)
                                print(f"     ✅ Extracted {file_info.filename}")
                else:
                    # Extract all files
                    zip_ref.extractall(extract_to)
                    print(f"     ✅ Extracted all files to {extract_to}")
        
        print(f"   ✅ {description} extraction completed")
        return True
        
    except Exception as e:
        print(f"\n   ❌ Failed to download/extract {description}: {str(e)}")
        return False

def download_rclone():
    """Download rclone.exe for Windows"""
    print("🔄 Downloading rclone.exe...")
    
    # rclone download URL for Windows
    rclone_url = "https://downloads.rclone.org/rclone-current-windows-amd64.zip"
    
    return download_and_extract_zip(
        rclone_url, 
        ".", 
        "rclone for Windows",
        ["rclone.exe"]
    )

def download_quest_adb_tools():
    """Download minimal ADB tools specifically for Quest sideloading"""
    print("🔄 Downloading minimal ADB tools for Quest sideloading...")
    
    # Android SDK Platform Tools download URL (but we'll extract only what we need)
    adb_url = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
    
    # Create quest_adb directory for minimal tools
    quest_adb_dir = "quest_adb"
    os.makedirs(quest_adb_dir, exist_ok=True)
    
    # Only download essential files for Quest sideloading
    # fastboot.exe is not needed for Quest sideloading
    quest_essential_files = ["adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll"]
    
    success = download_and_extract_zip(
        adb_url,
        quest_adb_dir,
        "Quest ADB Tools (minimal)",
        quest_essential_files
    )
    
    if success:
        # Copy all Quest ADB files to main directory for easy access and PyInstaller packaging
        quest_files_to_copy = ["adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll"]
        
        for file_name in quest_files_to_copy:
            # Try direct path first
            source_path = os.path.join(quest_adb_dir, file_name)
            if os.path.exists(source_path):
                shutil.copy2(source_path, file_name)
                print(f"   ✅ Copied {file_name} to main directory")
            else:
                # Look for file in subdirectories
                found = False
                for root, dirs, files in os.walk(quest_adb_dir):
                    if file_name in files:
                        source_path = os.path.join(root, file_name)
                        shutil.copy2(source_path, file_name)
                        print(f"   ✅ Copied {file_name} from {source_path} to main directory")
                        found = True
                        break
                
                if not found:
                    print(f"   ⚠️ Warning: {file_name} not found in Quest ADB download")
    
    return success

def download_dependencies():
    """Download all required dependencies"""
    print("\n📦 Downloading required dependencies...")
    
    success = True
    
    # Download rclone.exe if missing
    if not os.path.exists("rclone.exe"):
        if not download_rclone():
            success = False
    else:
        print("   ✅ rclone.exe already exists")
    
    # Download Quest ADB tools if missing (check all required files)
    quest_adb_files = ["adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll"]
    missing_adb_files = [f for f in quest_adb_files if not os.path.exists(f)]
    
    if missing_adb_files:
        print(f"   🔄 Missing Quest ADB files: {', '.join(missing_adb_files)}")
        if not download_quest_adb_tools():
            success = False
    else:
        print("   ✅ All Quest ADB tools already exist")
    
    if success:
        print("   🎉 All dependencies downloaded successfully!")
    else:
        print("   ❌ Some dependencies failed to download")
    
    return success

def build():
    print("="*60)
    print("🚀 NGBrowser Build Script with Auto-Updater & Quest Support")
    print("="*60)
    
    # Download dependencies first
    if not download_dependencies():
        print("\n❌ Failed to download required dependencies!")
        return False
    
    # Clean up previous build artifacts
    print("\n📁 Cleaning up previous build artifacts...")
    for item in ['build', 'dist', 'NGBrowser.spec']:
        if os.path.exists(item):
            try:
                if os.path.isdir(item):
                    shutil.rmtree(item)
                else:
                    os.remove(item)
                print(f"   ✅ Removed {item}")
            except PermissionError:
                print(f"   ⚠️ Warning: Could not remove {item} (permission denied). Continuing...")
            except Exception as e:
                print(f"   ⚠️ Warning: Could not remove {item}: {e}. Continuing...")
    
    # Check for required files
    print("\n🔍 Verifying required files...")
    required_files = {
        'rclone_gui.py': '📄 Main application file',
        'auto_updater.py': '🔄 Auto-updater module',
        'rclone.exe': '⚙️ rclone executable (auto-downloaded)',
        'adb.exe': '🔧 ADB for Quest sideloading (auto-downloaded)',
        'AdbWinApi.dll': '🔧 ADB Windows API DLL (auto-downloaded)',
        'AdbWinUsbApi.dll': '🔧 ADB Windows USB API DLL (auto-downloaded)'
    }
    
    missing_files = []
    for file, description in required_files.items():
        if os.path.exists(file):
            print(f"   ✅ {description}: {file}")
        else:
            print(f"   ❌ {description}: {file} - NOT FOUND")
            missing_files.append(file)
    
    if missing_files:
        print(f"\n❌ Build verification failed! Missing files: {', '.join(missing_files)}")
        print("   💡 This shouldn't happen if auto-download worked correctly.")
        return False
    
    # Check for optional files
    print("\n📋 Checking optional files...")
    optional_files = {
        'rclone.conf': '🔧 rclone configuration',
        'requirements.txt': '📦 Python dependencies',
        'README.md': '📖 Documentation'
    }
    
    for file, description in optional_files.items():
        if os.path.exists(file):
            print(f"   ✅ {description}: {file}")
        else:
            print(f"   ⚠️ {description}: {file} - Not found (optional)")
    
    # Find icon file
    print("\n🎨 Looking for icon file...")
    icon_file = None
    icon_patterns = ['*.ico', '*.png', '*.jpg', '*.jpeg']
    
    for pattern in icon_patterns:
        icons = glob.glob(pattern)
        if icons:
            # Prefer rclone.ico, then NGBrowser.ico, then any icon
            if 'rclone.ico' in icons:
                icon_file = 'rclone.ico'
            elif 'NGBrowser.ico' in icons:
                icon_file = 'NGBrowser.ico'
            else:
                icon_file = icons[0]
            break
    
    if icon_file:
        print(f"   ✅ Found icon: {icon_file}")
    else:
        print(f"   ⚠️ No icon file found. Executable will use default icon.")
    
    # Prepare PyInstaller command
    print("\n🔨 Preparing PyInstaller command...")
    cmd = [
        '--name=NGBrowser',
        '--onefile',
        '--windowed',
        '--clean',  # Clean build cache
        '--noconfirm',  # Overwrite output directory
        # Include auto-updater module
        '--hidden-import=auto_updater',
        '--hidden-import=requests',
        '--hidden-import=requests.adapters',
        '--hidden-import=requests.auth',
        '--hidden-import=requests.cookies',
        '--hidden-import=requests.sessions',
        '--hidden-import=urllib3',
        # Add rclone.exe as data
        '--add-data=rclone.exe;.',
        # Add auto-updater module
        '--add-data=auto_updater.py;.',
        # Add ADB tools for Quest sideloading
        '--add-data=adb.exe;.',
    ]
    
    # Add individual Quest ADB DLL files to root directory (required for adb.exe to work)
    quest_adb_files = ['AdbWinApi.dll', 'AdbWinUsbApi.dll']
    for adb_file in quest_adb_files:
        if os.path.exists(adb_file):
            cmd.append(f'--add-data={adb_file};.')
            print(f"   ✅ Including {adb_file} in root directory")
        elif os.path.exists('quest_adb'):
            # Look for the file in quest_adb directory
            for root, dirs, files in os.walk('quest_adb'):
                if adb_file in files:
                    source_path = os.path.join(root, adb_file)
                    cmd.append(f'--add-data={source_path};.')
                    print(f"   ✅ Including {adb_file} from {source_path} in root directory")
                    break
    
    # Add Quest ADB tools directory if it exists (for completeness)
    if os.path.exists('quest_adb'):
        cmd.append('--add-data=quest_adb;quest_adb')
        print("   ✅ Including Quest ADB tools directory")
    
    # Add rclone.conf if it exists
    if os.path.exists('rclone.conf'):
        cmd.append('--add-data=rclone.conf;.')
        print("   ✅ Including rclone.conf")
    
    # Add icon if found
    if icon_file:
        cmd.extend(['--icon', icon_file])
        print(f"   ✅ Using icon: {icon_file}")
    
    # Add the main script
    cmd.append('rclone_gui.py')
    
    print(f"\n🔧 PyInstaller command: {' '.join(cmd)}")
    
    # Run PyInstaller with the prepared command
    print("\n🚀 Building executable with PyInstaller...")
    print("   This may take a few minutes...")
    
    try:
        PyInstaller.__main__.run(cmd)
        
        # Check if build was successful
        exe_path = os.path.join('dist', 'NGBrowser.exe')
        if os.path.exists(exe_path):
            file_size = os.path.getsize(exe_path) / (1024 * 1024)  # MB
            print(f"\n✅ Build successful!")
            print(f"   📁 Location: {exe_path}")
            print(f"   📊 Size: {file_size:.1f} MB")
            
            # Post-build checks
            print("\n🔍 Post-build verification...")
            
            # Check if rclone.conf should be copied
            if os.path.exists('rclone.conf'):
                dist_conf = os.path.join('dist', 'rclone.conf')
                if not os.path.exists(dist_conf):
                    shutil.copy2('rclone.conf', dist_conf)
                    print("   ✅ Copied rclone.conf to dist folder")
                else:
                    print("   ✅ rclone.conf already in dist folder")
            
            # Check if Quest ADB tools should be copied
            if os.path.exists('quest_adb'):
                dist_quest_adb_dir = os.path.join('dist', 'quest_adb')
                if not os.path.exists(dist_quest_adb_dir):
                    shutil.copytree('quest_adb', dist_quest_adb_dir)
                    print("   ✅ Copied Quest ADB tools to dist folder")
                else:
                    print("   ✅ Quest ADB tools already in dist folder")
            
            print("\n🎉 Build completed successfully!")
            print("═" * 50)
            print("📋 Next Steps:")
            print("   1. Test the executable: dist\\NGBrowser.exe")
            print("   2. Test Quest sideloading functionality")
            print("   3. Ensure rclone.conf is in the same directory")
            print("   4. Upload to GitHub releases for auto-updater")
            print("═" * 50)
            print("🎆 New Features Included:")
            print("   • Quest VR sideloading support")
            print("   • Auto-downloaded ADB tools")
            print("   • APK installation from cloud storage")
            print("   • Wireless ADB support")
            print("   • App management and file transfer")
            print("═" * 50)
            return True
            
        else:
            print("\n❌ Build failed! Executable not found.")
            return False
            
    except Exception as e:
        print(f"\n❌ Build failed with error: {str(e)}")
        return False


if __name__ == "__main__":
    print("NGBrowser Build Script")
    print("=====================")
    
    # Check if we're in the right directory
    if not os.path.exists('rclone_gui.py'):
        print("❌ Error: rclone_gui.py not found!")
        print("   Please run this script from the NGBrowser project directory.")
        sys.exit(1)
    
    # Check Python version
    if sys.version_info < (3, 8):
        print("❌ Error: Python 3.8 or higher is required.")
        sys.exit(1)
    
    # Check if PyInstaller is available
    try:
        import PyInstaller
        print(f"✅ PyInstaller version: {PyInstaller.__version__}")
    except ImportError:
        print("❌ Error: PyInstaller not found!")
        print("   Install it with: pip install pyinstaller")
        sys.exit(1)
    
    # Run the build
    success = build()
    
    if success:
        print("\n🎉 Build process completed successfully!")
        sys.exit(0)
    else:
        print("\n❌ Build process failed!")
        sys.exit(1)
