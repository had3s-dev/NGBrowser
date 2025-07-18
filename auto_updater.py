import os
import sys
import json
import tempfile
import shutil
import hashlib
import subprocess
import requests
from pathlib import Path
from PyQt6.QtCore import QThread, pyqtSignal, QTimer
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QProgressBar, QMessageBox, QCheckBox
from PyQt6.QtGui import QPixmap, QIcon
import zipfile


class AutoUpdater(QThread):
    """Auto-updater class for NGBrowser with secure download and installation"""
    
    # Signals
    update_available = pyqtSignal(str, str)  # version, changelog
    update_progress = pyqtSignal(int)  # progress percentage
    update_status = pyqtSignal(str)  # status message
    update_complete = pyqtSignal(bool)  # success flag
    error_occurred = pyqtSignal(str)  # error message
    
    def __init__(self, current_version="1.0.0", update_server="https://api.github.com/repos/had3s-dev/NGBrowser/releases/latest"):
        super().__init__()
        self.current_version = current_version
        self.update_server = update_server
        self.download_url = None
        self.new_version = None
        self.changelog = None
        self.temp_dir = None
        self.app_path = os.path.dirname(os.path.abspath(sys.argv[0]))
        self.executable_name = "NGBrowser.exe"
        
    def check_for_updates(self):
        """Check for available updates"""
        try:
            self.update_status.emit("Checking for updates...")
            
            # Make request to update server
            headers = {
                'User-Agent': f'NGBrowser/{self.current_version}',
                'Accept': 'application/vnd.github.v3+json'
            }
            
            response = requests.get(self.update_server, headers=headers, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            latest_version = data.get('tag_name', '').lstrip('v')
            
            if self._is_newer_version(latest_version, self.current_version):
                self.new_version = latest_version
                self.changelog = data.get('body', 'No changelog available')
                
                # Find the download URL for the executable
                for asset in data.get('assets', []):
                    if asset['name'].endswith('.exe') or asset['name'].endswith('.zip'):
                        self.download_url = asset['browser_download_url']
                        break
                
                if self.download_url:
                    self.update_available.emit(self.new_version, self.changelog)
                    return True
                else:
                    self.error_occurred.emit("No downloadable executable found in latest release")
                    return False
            else:
                self.update_status.emit("No updates available")
                return False
                
        except requests.RequestException as e:
            self.error_occurred.emit(f"Network error checking for updates: {str(e)}")
            return False
        except Exception as e:
            self.error_occurred.emit(f"Error checking for updates: {str(e)}")
            return False
    
    def _is_newer_version(self, remote_version, local_version):
        """Compare version strings (semantic versioning)"""
        try:
            def version_tuple(v):
                return tuple(map(int, v.split('.')))
            
            return version_tuple(remote_version) > version_tuple(local_version)
        except:
            return False
    
    def download_and_install(self):
        """Download and install the update"""
        if not self.download_url:
            self.error_occurred.emit("No download URL available")
            return
            
        try:
            # Create temporary directory
            self.temp_dir = tempfile.mkdtemp(prefix="ngbrowser_update_")
            
            # Download the update
            self.update_status.emit("Downloading update...")
            self._download_file(self.download_url, self.temp_dir)
            
            # Install the update
            self.update_status.emit("Installing update...")
            self._install_update()
            
            self.update_complete.emit(True)
            
        except Exception as e:
            self.error_occurred.emit(f"Error during update: {str(e)}")
            self.update_complete.emit(False)
        finally:
            # Clean up temporary files
            if self.temp_dir and os.path.exists(self.temp_dir):
                try:
                    shutil.rmtree(self.temp_dir)
                except:
                    pass
    
    def _download_file(self, url, destination_dir):
        """Download file with progress tracking"""
        try:
            response = requests.get(url, stream=True, timeout=30)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            
            filename = os.path.basename(url.split('?')[0])
            if not filename.endswith(('.exe', '.zip')):
                filename = self.executable_name
                
            filepath = os.path.join(destination_dir, filename)
            
            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        
                        if total_size > 0:
                            progress = int((downloaded / total_size) * 100)
                            self.update_progress.emit(progress)
            
            self.downloaded_file = filepath
            return filepath
            
        except Exception as e:
            raise Exception(f"Download failed: {str(e)}")
    
    def _install_update(self):
        """Install the downloaded update"""
        try:
            if not hasattr(self, 'downloaded_file') or not os.path.exists(self.downloaded_file):
                raise Exception("Downloaded file not found")
            
            # Handle ZIP files
            if self.downloaded_file.endswith('.zip'):
                self._extract_zip_update()
            else:
                # Direct executable replacement
                self._replace_executable()
                
        except Exception as e:
            raise Exception(f"Installation failed: {str(e)}")
    
    def _extract_zip_update(self):
        """Extract ZIP file and replace executable"""
        try:
            extract_dir = os.path.join(self.temp_dir, "extracted")
            os.makedirs(extract_dir, exist_ok=True)
            
            with zipfile.ZipFile(self.downloaded_file, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)
            
            # Find the executable in the extracted files
            exe_path = None
            for root, dirs, files in os.walk(extract_dir):
                for file in files:
                    if file.endswith('.exe'):
                        exe_path = os.path.join(root, file)
                        break
                if exe_path:
                    break
            
            if not exe_path:
                raise Exception("No executable found in ZIP file")
            
            # Replace the current executable
            current_exe = os.path.join(self.app_path, self.executable_name)
            backup_exe = current_exe + ".backup"
            
            # Create backup
            if os.path.exists(current_exe):
                shutil.copy2(current_exe, backup_exe)
            
            # Replace with new version
            shutil.copy2(exe_path, current_exe)
            
            # Remove backup if successful
            if os.path.exists(backup_exe):
                os.remove(backup_exe)
                
        except Exception as e:
            # Restore backup if something went wrong
            backup_exe = os.path.join(self.app_path, self.executable_name + ".backup")
            if os.path.exists(backup_exe):
                current_exe = os.path.join(self.app_path, self.executable_name)
                shutil.copy2(backup_exe, current_exe)
                os.remove(backup_exe)
            raise e
    
    def _replace_executable(self):
        """Replace the current executable with the new one"""
        try:
            current_exe = os.path.join(self.app_path, self.executable_name)
            backup_exe = current_exe + ".backup"
            
            # Create backup
            if os.path.exists(current_exe):
                shutil.copy2(current_exe, backup_exe)
            
            # Replace with new version
            shutil.copy2(self.downloaded_file, current_exe)
            
            # Remove backup if successful
            if os.path.exists(backup_exe):
                os.remove(backup_exe)
                
        except Exception as e:
            # Restore backup if something went wrong
            backup_exe = os.path.join(self.app_path, self.executable_name + ".backup")
            if os.path.exists(backup_exe):
                current_exe = os.path.join(self.app_path, self.executable_name)
                shutil.copy2(backup_exe, current_exe)
                os.remove(backup_exe)
            raise e
    
    def run(self):
        """Thread run method for background update checking"""
        self.check_for_updates()


class UpdateDialog(QDialog):
    """Dialog for displaying update information and progress"""
    
    def __init__(self, parent=None, version="", changelog=""):
        super().__init__(parent)
        self.setWindowTitle("NGBrowser Update Available")
        self.setModal(True)
        self.setFixedSize(500, 400)
        
        self.version = version
        self.changelog = changelog
        self.user_choice = None
        
        self._setup_ui()
        
    def _setup_ui(self):
        """Setup the update dialog UI"""
        layout = QVBoxLayout(self)
        
        # Header
        header_layout = QHBoxLayout()
        
        # Update icon (you can add an icon here)
        icon_label = QLabel("🔄")
        icon_label.setStyleSheet("font-size: 32px;")
        header_layout.addWidget(icon_label)
        
        # Version info
        version_layout = QVBoxLayout()
        title_label = QLabel(f"NGBrowser {self.version} is available!")
        title_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #2196F3;")
        version_layout.addWidget(title_label)
        
        subtitle_label = QLabel("A new version is ready to download and install.")
        subtitle_label.setStyleSheet("color: #666;")
        version_layout.addWidget(subtitle_label)
        
        header_layout.addLayout(version_layout)
        header_layout.addStretch()
        
        layout.addLayout(header_layout)
        
        # Changelog
        changelog_label = QLabel("What's New:")
        changelog_label.setStyleSheet("font-weight: bold; margin-top: 10px;")
        layout.addWidget(changelog_label)
        
        from PyQt6.QtWidgets import QTextEdit
        changelog_text = QTextEdit()
        changelog_text.setPlainText(self.changelog)
        changelog_text.setReadOnly(True)
        changelog_text.setMaximumHeight(200)
        layout.addWidget(changelog_text)
        
        # Auto-update checkbox
        self.auto_update_check = QCheckBox("Automatically check for updates on startup")
        self.auto_update_check.setChecked(True)
        layout.addWidget(self.auto_update_check)
        
        # Progress bar (initially hidden)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)
        
        # Status label
        self.status_label = QLabel("")
        self.status_label.setVisible(False)
        layout.addWidget(self.status_label)
        
        # Buttons
        button_layout = QHBoxLayout()
        
        self.later_button = QPushButton("Later")
        self.later_button.clicked.connect(self.reject)
        button_layout.addWidget(self.later_button)
        
        self.skip_button = QPushButton("Skip This Version")
        self.skip_button.clicked.connect(self._skip_version)
        button_layout.addWidget(self.skip_button)
        
        self.update_button = QPushButton("Update Now")
        self.update_button.clicked.connect(self._start_update)
        self.update_button.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
        """)
        button_layout.addWidget(self.update_button)
        
        layout.addLayout(button_layout)
        
    def _skip_version(self):
        """Skip this version"""
        self.user_choice = "skip"
        self.reject()
        
    def _start_update(self):
        """Start the update process"""
        self.user_choice = "update"
        self.accept()
        
    def show_progress(self, visible=True):
        """Show/hide progress elements"""
        self.progress_bar.setVisible(visible)
        self.status_label.setVisible(visible)
        
        if visible:
            self.later_button.setEnabled(False)
            self.skip_button.setEnabled(False)
            self.update_button.setEnabled(False)
            
    def update_progress(self, value):
        """Update progress bar"""
        self.progress_bar.setValue(value)
        
    def update_status(self, message):
        """Update status message"""
        self.status_label.setText(message)
        
    def get_auto_update_preference(self):
        """Get auto-update preference"""
        return self.auto_update_check.isChecked()


class UpdateSettings:
    """Handle update settings and preferences"""
    
    def __init__(self, settings_file="update_settings.json"):
        self.settings_file = settings_file
        self.settings = self._load_settings()
        
    def _load_settings(self):
        """Load settings from file"""
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, 'r') as f:
                    return json.load(f)
        except:
            pass
        
        # Default settings
        return {
            "auto_check": True,
            "check_interval": 24,  # hours
            "skipped_versions": [],
            "last_check": 0
        }
    
    def save_settings(self):
        """Save settings to file"""
        try:
            with open(self.settings_file, 'w') as f:
                json.dump(self.settings, f, indent=2)
        except Exception as e:
            print(f"Error saving update settings: {e}")
    
    def get(self, key, default=None):
        """Get setting value"""
        return self.settings.get(key, default)
    
    def set(self, key, value):
        """Set setting value"""
        self.settings[key] = value
        self.save_settings()
    
    def should_check_for_updates(self):
        """Check if we should check for updates"""
        if not self.get("auto_check", True):
            return False
            
        import time
        last_check = self.get("last_check", 0)
        check_interval = self.get("check_interval", 24) * 3600  # Convert to seconds
        
        return time.time() - last_check > check_interval
    
    def mark_update_check(self):
        """Mark that we've checked for updates"""
        import time
        self.set("last_check", time.time())
    
    def is_version_skipped(self, version):
        """Check if a version is skipped"""
        return version in self.get("skipped_versions", [])
    
    def skip_version(self, version):
        """Skip a version"""
        skipped = self.get("skipped_versions", [])
        if version not in skipped:
            skipped.append(version)
            self.set("skipped_versions", skipped)
