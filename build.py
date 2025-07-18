import os
import shutil
import PyInstaller.__main__
import sys
import glob
from pathlib import Path

def build():
    print("="*60)
    print("🚀 NGBrowser Build Script with Auto-Updater Support")
    print("="*60)
    
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
    print("\n🔍 Checking required files...")
    required_files = {
        'rclone_gui.py': '📄 Main application file',
        'auto_updater.py': '🔄 Auto-updater module',
        'rclone.exe': '⚙️ rclone executable'
    }
    
    missing_files = []
    for file, description in required_files.items():
        if os.path.exists(file):
            print(f"   ✅ {description}: {file}")
        else:
            print(f"   ❌ {description}: {file} - NOT FOUND")
            missing_files.append(file)
    
    if missing_files:
        print(f"\n❌ Build failed! Missing required files: {', '.join(missing_files)}")
        if 'rclone.exe' in missing_files:
            print("   💡 Download rclone.exe from https://rclone.org/downloads/")
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
        '--add-data=auto_updater.py;.'
    ]
    
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
            
            print("\n🎉 Build completed successfully!")
            print("═" * 50)
            print("📋 Next Steps:")
            print("   1. Test the executable: dist\\NGBrowser.exe")
            print("   2. Ensure rclone.conf is in the same directory")
            print("   3. Upload to GitHub releases for auto-updater")
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
