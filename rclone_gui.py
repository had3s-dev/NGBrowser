import sys
import os
import subprocess
import json
import zipfile
import tempfile
import shutil
import psutil
import time
import re
from datetime import datetime
from pathlib import Path
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                             QTableWidget, QTableWidgetItem, QPushButton, QLineEdit, QFileDialog, QLabel, QStyle, 
                             QTabWidget, QProgressBar, QHeaderView, QMenu, QMessageBox, QTextEdit,
                             QComboBox, QCheckBox, QSpinBox, QGroupBox, QFormLayout, QSplitter,
                             QTreeWidget, QTreeWidgetItem, QDialog, QDialogButtonBox, QScrollArea,
                             QSlider, QStatusBar, QToolBar, QListWidget, QListWidgetItem, QPlainTextEdit)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QSize, QUrl
from PyQt6.QtGui import QColor, QIcon, QAction, QFont, QPixmap, QPainter, QBrush

# Auto-updater imports
from auto_updater import AutoUpdater, UpdateDialog, UpdateSettings

class TransferWorker(QThread):
    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    error = pyqtSignal(str)
    finished = pyqtSignal(bool)  # Success/failure flag
    debug_log = pyqtSignal(str)  # Debug logging signal

    def __init__(self, command, timeout=3600):
        super().__init__()
        self.command = command
        self.timeout = timeout
        self.process = None
        self._is_cancelled = False

    def run(self):
        stderr_lines = []
        stdout_lines = []
        
        try:
            self.status.emit("Initializing transfer...")
            
            # Validate command before execution
            if not self.command or len(self.command) < 3:
                self.error.emit("Invalid command parameters")
                self.finished.emit(False)
                return
            
            # Validate command components
            if not self._validate_command():
                self.error.emit("Command validation failed")
                self.finished.emit(False)
                return
            
            # Start the rclone process with proper buffering for real-time output
            # Force unbuffered output by making rclone think it's running in a terminal
            import os
            env = os.environ.copy()
            env['PYTHONUNBUFFERED'] = '1'
            env['TERM'] = 'xterm'  # Make rclone think it's in a terminal
            
            self.process = subprocess.Popen(
                self.command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                bufsize=0,  # Unbuffered for real-time output
                universal_newlines=True,
                env=env
            )
            
            self.status.emit("Transfer in progress...")
            
            # Read output with timeout using threading for non-blocking reads
            import time
            import threading
            from queue import Queue, Empty
            
            # Initialize tracking variables
            start_time = time.time()
            transfer_start_time = start_time
            last_activity_time = start_time
            last_progress = -1
            startup_timeout = 60  # seconds
            no_progress_timeout = self.timeout  # Use the timeout passed to constructor
            
            # Create queues for non-blocking reads
            stdout_queue = Queue()
            stderr_queue = Queue()
            
            # Thread functions for reading stdout and stderr
            def read_stdout():
                try:
                    while True:
                        line = self.process.stdout.readline()
                        if not line:
                            break
                        stdout_queue.put(line.strip())
                except Exception:
                    pass
            
            def read_stderr():
                try:
                    while True:
                        line = self.process.stderr.readline()
                        if not line:
                            break
                        stderr_queue.put(line.strip())
                except Exception:
                    pass
            
            # Start reader threads
            stdout_thread = threading.Thread(target=read_stdout, daemon=True)
            stderr_thread = threading.Thread(target=read_stderr, daemon=True)
            stdout_thread.start()
            stderr_thread.start()
            
            # Main transfer loop
            while self.process.poll() is None:
                if self._is_cancelled:
                    self.process.terminate()
                    self.status.emit("Transfer cancelled")
                    self.finished.emit(False)
                    return
                
                current_time = time.time()
                
                # Check for startup timeout (transfer should start showing progress within 1 minute)
                if last_progress == -1 and current_time - transfer_start_time > startup_timeout:
                    self.debug_log.emit(f"DEBUG: Transfer startup timeout - no progress shown for {startup_timeout} seconds")
                    self.debug_log.emit(f"DEBUG: This might indicate a connection problem or file access issue")
                    # Don't kill the process yet, just warn
                    self.status.emit("⚠️ Transfer startup slow - checking connection...")
                
                # Check for timeout if no progress for too long
                if current_time - last_activity_time > no_progress_timeout:
                    self.debug_log.emit(f"DEBUG: Transfer timeout - no progress for {no_progress_timeout} seconds")
                    self.process.terminate()
                    self.error.emit(f"Transfer timed out after {no_progress_timeout} seconds of inactivity")
                    self.finished.emit(False)
                    return
                
                # Check stdout queue (non-blocking)
                try:
                    while True:
                        line = stdout_queue.get_nowait()
                        if line:
                            # Handle both carriage return and newline terminated lines
                            line = line.replace('\r', '').strip()
                            if line:
                                stdout_lines.append(line)
                                last_activity_time = current_time  # Reset activity timer
                                
                                # Output rclone progress INFO lines directly to logs for user visibility
                                if "INFO" in line and ("B/s" in line or "ETA" in line or "GiB" in line or "MiB" in line or "KiB" in line):
                                    # This is a progress line - show it directly in logs
                                    self.debug_log.emit(f"📊 Transfer Progress: {line}")
                                
                                # DEBUG: Log raw rclone output from stdout
                                if line and not line.startswith('DEBUG'):
                                    print(f"DEBUG STDOUT: {repr(line)}")
                                    self.debug_log.emit(f"DEBUG STDOUT: {repr(line)}")
                                
                                # Parse progress from stdout
                                progress = self._parse_progress(line)
                                if progress is not None:
                                    print(f"DEBUG: Parsed progress {progress}% from stdout: {repr(line)}")
                                    self.debug_log.emit(f"DEBUG: Parsed progress {progress}% from stdout: {repr(line)}")
                                    if progress != last_progress:
                                        last_progress = progress
                                        self.progress.emit(progress)
                                        # Reset activity timer on progress change
                                        if progress > 0:
                                            last_activity_time = current_time
                                
                                # Parse status info (ETA, speed, etc.)
                                status_info = self._parse_status_info(line)
                                if status_info:
                                    self.debug_log.emit(f"DEBUG STATUS: Emitting status: {status_info}")
                                    self.status.emit(status_info)
                                elif any(keyword in line.lower() for keyword in ['eta', 'speed', 'transferred', 'copying']):
                                    # Fallback to old method if new parser didn't catch it
                                    self.debug_log.emit(f"DEBUG: Fallback status parsing for: {line}")
                                    self.status.emit(line)
                except Empty:
                    pass  # No stdout data available right now
                
                # Check stderr queue (non-blocking)
                try:
                    while True:
                        line = stderr_queue.get_nowait()
                        if line:
                            # Handle both carriage return and newline terminated lines
                            line = line.replace('\r', '').strip()
                            if line:
                                stderr_lines.append(line)
                                last_activity_time = current_time  # Reset activity timer
                                
                                # Output rclone progress INFO lines directly to logs for user visibility
                                if "INFO" in line and ("B/s" in line or "ETA" in line or "GiB" in line or "MiB" in line or "KiB" in line):
                                    # This is a progress line - show it directly in logs
                                    self.debug_log.emit(f"📊 Transfer Progress: {line}")
                                
                                # DEBUG: Log raw rclone output from stderr
                                if line and not line.startswith('DEBUG'):
                                    print(f"DEBUG STDERR: {repr(line)}")
                                    self.debug_log.emit(f"DEBUG STDERR: {repr(line)}")
                                
                                # Parse progress from stderr too
                                progress = self._parse_progress(line)
                                if progress is not None:
                                    print(f"DEBUG: Parsed progress {progress}% from stderr: {repr(line)}")
                                    self.debug_log.emit(f"DEBUG: Parsed progress {progress}% from stderr: {repr(line)}")
                                    if progress != last_progress:
                                        last_progress = progress
                                        self.progress.emit(progress)
                                        # Reset activity timer on progress change
                                        if progress > 0:
                                            last_activity_time = current_time
                                
                                # Parse status info from stderr too
                                status_info = self._parse_status_info(line)
                                if status_info:
                                    self.debug_log.emit(f"DEBUG STATUS: Emitting status from stderr: {status_info}")
                                    self.status.emit(status_info)
                except Empty:
                    pass  # No stderr data available right now
                
                # Small delay to prevent busy waiting
                time.sleep(0.1)
            
            # Process completed, check result
            return_code = self.process.returncode
            
            # Read any remaining stderr
            try:
                remaining_stderr = self.process.stderr.read()
                if remaining_stderr:
                    stderr_lines.extend(remaining_stderr.splitlines())
            except:
                pass
            
            if return_code == 0:
                self.progress.emit(100)
                self.status.emit("Transfer completed successfully")
                self.finished.emit(True)
            else:
                # Combine all stderr for comprehensive error analysis
                stderr_output = '\n'.join(stderr_lines)
                
                # Use comprehensive error parsing for user-friendly messages
                user_friendly_error = self._parse_error_message(return_code, stderr_output)
                
                self.error.emit(user_friendly_error)
                self.status.emit(f"Failed (Code: {return_code})")
                self.finished.emit(False)
                
        except Exception as e:
            self.error.emit(f"Transfer worker error: {str(e)}")
            self.status.emit("Transfer failed")
            self.finished.emit(False)
        finally:
            if self.process:
                try:
                    # Try graceful termination first
                    self.process.terminate()
                    # Wait a bit for graceful shutdown
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        # Force kill if still running
                        self.process.kill()
                        self.process.wait()
                except Exception as e:
                    # Force kill as last resort
                    try:
                        self.process.kill()
                    except:
                        pass
    
    def _parse_progress(self, line):
        """Parse progress percentage from rclone output"""
        try:
            import re
            
            # Debug: Log every line we're trying to parse
            self.debug_log.emit(f"DEBUG PARSE: Analyzing line: {repr(line)}")
            
            # Skip rclone system messages but NOT INFO lines that contain progress data
            if re.search(r'\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2} (NOTICE|DEBUG|ERROR):', line):
                self.debug_log.emit(f"DEBUG PARSE: Skipping system message: {repr(line)}")
                return None
        
            # Check if this is an INFO line with progress data - if so, extract the progress part
            info_match = re.search(r'\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2} INFO\s*:\s*(.+)', line)
            if info_match:
                # Extract the progress data part after "INFO  :"
                progress_data = info_match.group(1).strip()
                self.debug_log.emit(f"DEBUG PARSE: Found INFO line with progress data: {repr(progress_data)}")
                line = progress_data  # Use only the progress data part for parsing
            
            # Clean the line of ANSI escape codes and control characters
            clean_line = re.sub(r'\x1b\[[0-9;]*[mK]', '', line)
            clean_line = re.sub(r'\r', '', clean_line)
            clean_line = clean_line.strip()
            
            # Enhanced patterns to match actual rclone output formats
            patterns = [
                # Primary format from rclone INFO output: "703.250 MiB / 6.429 GiB, 11%, 9.303 MiB/s, ETA 10m32s"
                r'[0-9.]+\s*[KMGT]?i?B\s*/\s*[0-9.]+\s*[KMGT]?i?B,\s*(\d{1,3})%',
                # Standard rclone progress format: "Transferred: 10.5 MB / 100 MB, 10%, 1.2 MB/s, ETA 1m30s"
                r'Transferred:\s*[0-9.]+\s*[KMGT]?i?B\s*/\s*[0-9.]+\s*[KMGT]?i?B,\s*(\d{1,3})%',
                # Alternative format: "10% 10.5 MB/s"
                r'^\s*(\d{1,3})%\s*[0-9.]+\s*[KMGT]?i?B/s',
                # Progress bar format: "████████████████████████████████████████ 100%"
                r'█+\s*(\d{1,3})%',
                # Percentage with data: "50% (25.5 MB/s)"
                r'(\d{1,3})%\s*\([0-9.]+\s*[KMGT]?i?B/s\)',
                # Simple percentage: "50%"
                r'\b(\d{1,3})%\b',
                # Copying progress: "Copying 'file.txt' 50%"
                r'Copying\s+.*?\s+(\d{1,3})%',
                # Transfer progress: "Transfer: 50%"
                r'Transfer:\s*(\d{1,3})%',
                # Percentage in parentheses: "(50%)"
                r'\((\d{1,3})%\)',
                # Progress with slash: "50% / 100%"
                r'(\d{1,3})%\s*/\s*100%',
            ]
            
            for pattern in patterns:
                match = re.search(pattern, clean_line)
                if match:
                    percentage = int(match.group(1))
                    if 0 <= percentage <= 100:
                        self.debug_log.emit(f"DEBUG PARSE: Found percentage {percentage}% with pattern '{pattern}': {repr(line)}")
                        return percentage
            
            # Check for any percentage in the line as fallback
            all_percentages = re.findall(r'\b(\d{1,3})%\b', clean_line)
            if all_percentages:
                for perc_str in all_percentages:
                    percentage = int(perc_str)
                    if 0 <= percentage <= 100:
                        self.debug_log.emit(f"DEBUG PARSE: Found fallback percentage {percentage}% in: {repr(line)}")
                        return percentage
            
            # No percentage found
            self.debug_log.emit(f"DEBUG PARSE: No percentage found in: {repr(line)}")
                    
        except Exception as e:
            self.debug_log.emit(f"DEBUG PARSE: Exception parsing line {repr(line)}: {str(e)}")
            pass
        
        return None
    
    def _parse_status_info(self, line):
        """Parse status information including ETA, speed, and transferred data from rclone output"""
        try:
            import re
            
            # Skip system messages but NOT rclone INFO lines with progress data
            if re.search(r'\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2} (NOTICE|DEBUG|ERROR):', line):
                self.debug_log.emit(f"DEBUG PARSE: Skipping system message: {repr(line)}")
                return None
            
            # Handle rclone INFO lines - these often contain progress information
            if re.search(r'\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2} INFO\s*:', line):
                self.debug_log.emit(f"DEBUG PARSE: Analyzing INFO line: {repr(line)}")
                # Extract the content after the INFO timestamp
                info_match = re.search(r'\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2} INFO\s*:\s*(.+)', line)
                if info_match:
                    line = info_match.group(1)  # Use the content after INFO:
                    self.debug_log.emit(f"DEBUG PARSE: Extracted INFO content: {repr(line)}")
                else:
                    self.debug_log.emit(f"DEBUG PARSE: Could not extract INFO content from: {repr(line)}")
                    return None
            
            # Clean the line first
            clean_line = re.sub(r'\x1b\[[0-9;]*m', '', line)  # Remove ANSI codes
            
            # Look for rclone progress lines with ETA, speed, or transferred info
            # Based on actual rclone output format:
            # "       640 KiB / 6.429 GiB, 0%, 0 B/s, ETA -"
            # "    15.250 MiB / 6.429 GiB, 0%, 2.550 MiB/s, ETA 42m55s"
            # "Transferred:   123.45 MiB / 1.23 GiB, 45%, 12.34 MiB/s, ETA 1m23s"
            
            status_info = {}
            
            # Extract ETA (format: "ETA 42m55s" or "ETA 1h33m56s" or "ETA -")
            eta_match = re.search(r'ETA\s+([0-9]+[smhd]?(?:[0-9]+[smhd]?)*|[-]+)', clean_line)
            if eta_match:
                eta_value = eta_match.group(1)
                if eta_value != '-':
                    status_info['eta'] = eta_value
            
            # Extract speed (format: "2.550 MiB/s" or "0 B/s")
            speed_match = re.search(r'([0-9.]+)\s*([KMGT]?i?B)/s', clean_line)
            if speed_match:
                speed_value = speed_match.group(1)
                speed_unit = speed_match.group(2)
                if float(speed_value) > 0:  # Only show if speed > 0
                    status_info['speed'] = f"{speed_value} {speed_unit}/s"
            
            # Extract transferred amount (format: "15.250 MiB / 6.429 GiB")
            # Try multiple patterns to catch different formats
            transferred_patterns = [
                r'([0-9.]+\s*[KMGT]?i?B)\s*/\s*([0-9.]+\s*[KMGT]?i?B)',  # Standard format
                r'Transferred:\s*([0-9.]+\s*[KMGT]?i?B)\s*/\s*([0-9.]+\s*[KMGT]?i?B)',  # Transferred: prefix
            ]
            
            for pattern in transferred_patterns:
                transferred_match = re.search(pattern, clean_line)
                if transferred_match:
                    status_info['transferred'] = f"{transferred_match.group(1)} / {transferred_match.group(2)}"
                    break
            
            # Return formatted status if we found useful info
            if status_info:
                parts = []
                if 'transferred' in status_info:
                    parts.append(f"📁 {status_info['transferred']}")
                if 'speed' in status_info:
                    parts.append(f"⚡ {status_info['speed']}")
                if 'eta' in status_info:
                    parts.append(f"⏱️ ETA {status_info['eta']}")
                
                if parts:
                    return " | ".join(parts)
            
            return None
                    
        except Exception as e:
            self.debug_log.emit(f"DEBUG STATUS: Exception parsing status from line {repr(line)}: {str(e)}")
            return None
    
    def _clean_status_line(self, line):
        """Clean and format status line for display"""
        try:
            # Remove ANSI escape codes
            import re
            clean_line = re.sub(r'\x1b\[[0-9;]*m', '', line)
            
            # Extract useful information
            if 'ETA' in clean_line:
                return clean_line.split(',')[-1].strip()
            elif 'speed' in clean_line.lower():
                return clean_line.strip()
            elif 'transferred' in clean_line.lower():
                return clean_line.strip()
                
            return clean_line.strip()[:50]  # Limit length
        except Exception:
            return ""
    
    def _parse_error_message(self, exit_code, stderr_output):
        """Parse rclone error and provide user-friendly explanation"""
        error_msg = f"Transfer failed (Exit code: {exit_code})"
        user_friendly_msg = ""
        suggestions = []
        
        # Convert stderr to lowercase for pattern matching
        stderr_lower = stderr_output.lower() if stderr_output else ""
        
        # Map common error patterns to user-friendly messages
        if exit_code == 1:
            # Generic error - try to parse stderr for specifics
            if "no space left" in stderr_lower or "disk full" in stderr_lower:
                user_friendly_msg = "❌ Insufficient disk space on destination"
                suggestions = [
                    "Free up space on your local drive",
                    "Choose a different download location",
                    "Delete unnecessary files"
                ]
            elif "network" in stderr_lower or "connection" in stderr_lower or "timeout" in stderr_lower:
                user_friendly_msg = "🌐 Network connection problem"
                suggestions = [
                    "Check your internet connection",
                    "Try again in a few moments",
                    "Consider using bandwidth limiting"
                ]
            elif "permission denied" in stderr_lower or "access denied" in stderr_lower:
                user_friendly_msg = "🔒 Permission denied"
                suggestions = [
                    "Run as administrator if needed",
                    "Check file/folder permissions",
                    "Choose a different destination"
                ]
            elif "not found" in stderr_lower or "no such file" in stderr_lower:
                user_friendly_msg = "📁 File or folder not found"
                suggestions = [
                    "Refresh the file list",
                    "Check if the file still exists",
                    "Verify remote connection"
                ]
            elif "unauthorized" in stderr_lower or "forbidden" in stderr_lower or "authentication" in stderr_lower:
                user_friendly_msg = "🔐 Authentication or authorization failed"
                suggestions = [
                    "Check your remote configuration",
                    "Re-authenticate with the remote service",
                    "Verify access permissions"
                ]
            elif "rate limit" in stderr_lower or "too many requests" in stderr_lower:
                user_friendly_msg = "⏱️ Rate limited by remote service"
                suggestions = [
                    "Wait a few minutes before retrying",
                    "Use bandwidth limiting",
                    "Check service limits"
                ]
            elif "quota" in stderr_lower or "storage full" in stderr_lower:
                user_friendly_msg = "💾 Remote storage quota exceeded"
                suggestions = [
                    "Free up space on remote storage",
                    "Upgrade your storage plan",
                    "Choose different files to transfer"
                ]
            else:
                user_friendly_msg = "❌ Transfer failed with unknown error"
                suggestions = [
                    "Check the detailed error logs",
                    "Verify source and destination paths",
                    "Try a smaller file first"
                ]
        
        elif exit_code == 2:
            user_friendly_msg = "⚙️ Configuration or command error"
            suggestions = [
                "Check rclone configuration",
                "Verify remote settings",
                "Update rclone if needed"
            ]
        
        elif exit_code == 3:
            user_friendly_msg = "📂 Directory not found or access error"
            suggestions = [
                "Check if the directory exists",
                "Verify path permissions",
                "Refresh the remote connection"
            ]
        
        elif exit_code == 4:
            user_friendly_msg = "📄 File not found"
            suggestions = [
                "Refresh the file list",
                "Check if file was moved or deleted",
                "Verify the file path"
            ]
        
        elif exit_code == 5:
            user_friendly_msg = "⏰ Operation timed out"
            suggestions = [
                "Check your internet connection",
                "Try with a smaller file",
                "Increase timeout settings"
            ]
        
        elif exit_code == 6:
            user_friendly_msg = "🔄 Retry limit exceeded"
            suggestions = [
                "Check network stability",
                "Try again later",
                "Use lower transfer speeds"
            ]
        
        elif exit_code == 7:
            user_friendly_msg = "🚫 Interrupted by user or system"
            suggestions = [
                "Transfer was cancelled",
                "Try starting the transfer again"
            ]
        
        elif exit_code == 8:
            user_friendly_msg = "💥 Fatal error occurred"
            suggestions = [
                "Check system resources",
                "Restart the application",
                "Check rclone installation"
            ]
        
        else:
            user_friendly_msg = f"❓ Unknown error (Code: {exit_code})"
            suggestions = [
                "Check rclone documentation",
                "Try with different settings",
                "Report this error code"
            ]
        
        # Build comprehensive error message
        full_message = user_friendly_msg
        if suggestions:
            full_message += "\n\n💡 Suggestions:"
            for i, suggestion in enumerate(suggestions, 1):
                full_message += f"\n{i}. {suggestion}"
        
        if stderr_output:
            full_message += f"\n\n🔍 Technical details:\n{stderr_output.strip()}"
        
        return full_message
    
    def _check_disk_space(self, destination_path, estimated_size=None):
        """Check if there's enough disk space for the transfer"""
        try:
            import shutil
            
            # Get destination directory
            if os.path.isfile(destination_path):
                dest_dir = os.path.dirname(destination_path)
            else:
                dest_dir = destination_path
            
            # Get available disk space
            total, used, free = shutil.disk_usage(dest_dir)
            free_gb = free / (1024**3)
            
            # If we have an estimated size, check if there's enough space
            if estimated_size:
                estimated_gb = estimated_size / (1024**3)
                if free < estimated_size * 1.1:  # Add 10% buffer
                    return False, f"Insufficient disk space. Need ~{estimated_gb:.1f}GB, but only {free_gb:.1f}GB available."
            
            # General low space warning
            if free_gb < 1.0:
                return False, f"Very low disk space: only {free_gb:.1f}GB available. Consider freeing up space first."
            
            return True, f"Disk space OK: {free_gb:.1f}GB available"
            
        except Exception as e:
            return True, f"Could not check disk space: {str(e)}"
    

    def cancel(self):
        """Cancel the transfer"""
        self._is_cancelled = True
        if self.process:
            try:
                self.process.terminate()
            except:
                pass
    
    def _validate_command(self):
        """Validate the rclone command before execution"""
        try:
            if not self.command or not isinstance(self.command, list):
                return False
            
            # Check if we have at least 3 elements (rclone, command, args)
            if len(self.command) < 3:
                return False
            
            # Check if rclone executable exists
            rclone_path = self.command[0]
            if not os.path.exists(rclone_path):
                return False
            
            # Check if first element appears to be rclone executable
            if not self.command[0].lower().endswith(('rclone', 'rclone.exe')):
                return False
            
            # Check if second element is a valid rclone command
            valid_commands = ['copy', 'move', 'sync', 'copyto', 'moveto', 'lsf', 'lsjson', 'lsd', 'size']
            if self.command[1] not in valid_commands:
                return False
            
            # Check if source and destination are provided for transfer operations
            if self.command[1] in ['copy', 'copyto', 'move', 'moveto']:
                if len(self.command) < 4:
                    return False
                
                # Basic validation of paths
                source = self.command[2]
                dest = self.command[3]
                
                if not source or not dest:
                    return False
                
                # Check if source contains remote reference
                if ':' not in source and not os.path.exists(source):
                    # Local source should exist
                    return False
            
            return True
        except Exception:
            return False

    def _is_valid_rclone_path(self, path):
        """Check if path is a valid rclone format"""
        try:
            # Valid rclone paths have format: remote:path or just remote:
            if ':' not in path:
                return False
            
            parts = path.split(':', 1)
            if len(parts) != 2:
                return False
            
            remote, path_part = parts
            
            # Remote name should not be empty
            if not remote.strip():
                return False
            
            # Path part can be empty (root of remote)
            return True
            
        except Exception:
            return False


class RcloneGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NGBrowser - Advanced Cloud Storage Manager")
        self.setGeometry(100, 100, 1200, 800)
        self.current_remote = ""
        self.current_path = ""
        self.nav_history = []  # To track navigation history
        self.selected_files = []  # Multiple selection support
        self.sync_jobs = []  # Background sync jobs
        self.transfers = []  # Active transfer workers
        self.bandwidth_limit = 0  # KB/s, 0 = unlimited
        self.filters = {'include': [], 'exclude': []}  # File filters
        
        # Determine path to rclone.exe (same folder as script)
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.rclone_path = os.path.join(self.script_dir, "rclone.exe")
        
        # Check for local rclone.conf, fallback to default
        self.config_path = os.path.join(self.script_dir, "rclone.conf")
        if not os.path.exists(self.config_path):
            self.config_path = os.path.expanduser("~\\.config\\rclone\\rclone.conf")
        
        # Initialize system stats tracking
        self.start_time = datetime.now()
        self.total_transferred = 0
        self.active_transfers = 0
        
        # Auto-updater initialization
        self.app_version = "1.0.1"  # Current version
        self.update_settings = UpdateSettings()
        self.auto_updater = None
        self.update_dialog = None
        
        self.init_ui()
        self.load_remotes()
        self.setup_status_bar()
        self.setup_timers()
        
        # Initialize Quest functionality after UI is ready
        self.init_quest_functionality()
        
        # Check for updates on startup if enabled
        if self.update_settings.should_check_for_updates():
            QTimer.singleShot(3000, self.check_for_updates_background)  # Check after 3 seconds
    
    def check_for_updates_background(self):
        """Check for updates in background"""
        if not self.auto_updater:
            self.auto_updater = AutoUpdater(self.app_version)
            self.auto_updater.update_available.connect(self.on_update_available)
            self.auto_updater.error_occurred.connect(self.on_update_error)
            self.auto_updater.update_complete.connect(self.on_update_complete)
        
        self.auto_updater.start()
    
    def on_update_available(self, version, changelog):
        """Handle update available signal"""
        self.log_message(f"Update available: v{version}", "info")
        
        if not self.update_dialog:
            self.update_dialog = UpdateDialog(version, changelog, self)
            self.update_dialog.update_requested.connect(self.start_update_download)
            self.update_dialog.show()
    
    def on_update_error(self, error_message):
        """Handle update error signal"""
        self.log_message(f"Update error: {error_message}", "error")
    
    def on_update_complete(self, success):
        """Handle update completion"""
        if success:
            self.log_message("Update downloaded successfully. Preparing to restart...", "info")
            # Execute delayed replacement and restart
            if hasattr(self.auto_updater, 'execute_replacement'):
                if self.auto_updater.execute_replacement():
                    # Close the application to allow replacement
                    QTimer.singleShot(1000, self.close)
                else:
                    self.log_message("Failed to start update replacement", "error")
            else:
                self.log_message("Update completed but replacement not available", "warning")
        else:
            self.log_message("Update failed", "error")
    
    def start_update_download(self):
        """Start downloading the update"""
        if self.auto_updater:
            self.auto_updater.download_and_install()
    
    def add_update_menu(self):
        """Add update menu to the main window"""
        # This method is called from init_ui but may not be implemented yet
        pass
    
    def apply_custom_styling(self):
        """Apply beautiful Frutiger Aero-inspired styling with glassy effects and vibrant gradients"""
        # Frutiger Aero theme with glass effects, light gradients, and vibrant colors
        app_style = """
        QMainWindow {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1, 
                stop:0 #f0f8ff, stop:0.3 #e6f3ff, stop:0.7 #cce7ff, stop:1 #b3d9ff);
            color: #2c3e50;
            font-family: 'Segoe UI', 'Calibri', Arial, sans-serif;
        }
        
        /* Tab Widget Styling - Glassy Aero Effect */
        QTabWidget::pane {
            border: 1px solid rgba(135, 206, 235, 0.6);
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 rgba(255, 255, 255, 0.9), stop:1 rgba(240, 248, 255, 0.8));
            border-radius: 12px;
            margin-top: 20px;
        }
        
        QTabBar::tab {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 rgba(255, 255, 255, 0.9), stop:0.5 rgba(230, 243, 255, 0.8), stop:1 rgba(204, 231, 255, 0.7));
            border: 1px solid rgba(135, 206, 235, 0.5);
            border-bottom: none;
            padding: 12px 24px;
            margin-right: 3px;
            border-top-left-radius: 10px;
            border-top-right-radius: 10px;
            color: #2c3e50;
            font-weight: 600;
            font-size: 13px;
            min-width: 120px;
        }
        
        QTabBar::tab:selected {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 rgba(255, 255, 255, 1.0), stop:0.5 rgba(173, 216, 255, 0.9), stop:1 rgba(135, 206, 235, 0.8));
            border: 2px solid rgba(30, 144, 255, 0.7);
            border-bottom: none;
            color: #1e3a8a;
        }
        
        QTabBar::tab:hover:!selected {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 rgba(255, 255, 255, 0.95), stop:1 rgba(224, 240, 255, 0.85));
            border-color: rgba(30, 144, 255, 0.4);
        }
        
        /* Button Styling - Glassy Aero Buttons */
        QPushButton {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 rgba(255, 255, 255, 0.9), stop:0.5 rgba(173, 216, 255, 0.8), stop:1 rgba(135, 206, 235, 0.7));
            border: 1px solid rgba(30, 144, 255, 0.6);
            border-radius: 8px;
            padding: 10px 20px;
            color: #1e3a8a;
            font-weight: 600;
            font-size: 13px;
            min-height: 16px;
        }
        
        QPushButton:hover {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 rgba(255, 255, 255, 1.0), stop:0.5 rgba(173, 216, 255, 0.9), stop:1 rgba(100, 200, 255, 0.8));
            border: 2px solid rgba(30, 144, 255, 0.8);
        }
        
        QPushButton:pressed {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 rgba(173, 216, 255, 0.9), stop:1 rgba(135, 206, 235, 0.8));
            border: 2px solid rgba(30, 144, 255, 1.0);
        }
        
        QPushButton:disabled {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 rgba(240, 240, 240, 0.5), stop:1 rgba(220, 220, 220, 0.5));
            border: 1px solid rgba(180, 180, 180, 0.5);
            color: rgba(100, 100, 100, 0.7);
        }
        
        /* Secondary buttons */
        QPushButton[class="secondary"] {
            background-color: #3c3c3c;
            color: #ffffff;
        }
        
        QPushButton[class="secondary"]:hover {
            background-color: #484848;
        }
        
        /* Danger buttons */
        QPushButton[class="danger"] {
            background-color: #d13438;
        }
        
        QPushButton[class="danger"]:hover {
            background-color: #b92b2f;
        }
        
        /* Success buttons */
        QPushButton[class="success"] {
            background-color: #107c10;
        }
        
        QPushButton[class="success"]:hover {
            background-color: #0e6e0e;
        }
        
        /* Input Field Styling - Glassy Effect */
        QLineEdit {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 rgba(255, 255, 255, 0.9), stop:1 rgba(248, 252, 255, 0.8));
            border: 1px solid rgba(135, 206, 235, 0.6);
            border-radius: 8px;
            padding: 10px 14px;
            color: #2c3e50;
            font-size: 13px;
            selection-background-color: rgba(30, 144, 255, 0.3);
        }
        
        QLineEdit:focus {
            border: 2px solid rgba(30, 144, 255, 0.8);
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 rgba(255, 255, 255, 1.0), stop:1 rgba(240, 248, 255, 0.9));
        }
        
        QComboBox {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 rgba(255, 255, 255, 0.9), stop:1 rgba(248, 252, 255, 0.8));
            border: 1px solid rgba(135, 206, 235, 0.6);
            border-radius: 8px;
            padding: 8px 12px;
            color: #2c3e50;
            font-size: 13px;
        }
        
        QComboBox:hover {
            border: 2px solid rgba(30, 144, 255, 0.7);
        }
        
        QComboBox::drop-down {
            border: none;
            background: transparent;
        }
        
        QComboBox::down-arrow {
            image: url(data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMTIiIGhlaWdodD0iOCIgdmlld0JveD0iMCAwIDEyIDgiIGZpbGw9Im5vbmUiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyI+CjxwYXRoIGQ9Ik0xIDFMNiA2TDExIDEiIHN0cm9rZT0iI0ZGRkZGRiIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiLz4KPC9zdmc+);
        }
        
        /* Table Styling - Glassy Aero */
        QTableWidget {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 rgba(255, 255, 255, 0.95), stop:1 rgba(248, 252, 255, 0.85));
            alternate-background-color: rgba(230, 243, 255, 0.5);
            gridline-color: rgba(135, 206, 235, 0.4);
            border: 1px solid rgba(135, 206, 235, 0.6);
            border-radius: 10px;
            color: #2c3e50;
            selection-background-color: rgba(30, 144, 255, 0.3);
        }
        
        QTableWidget::item {
            padding: 10px;
            border: none;
        }
        
        QHeaderView::section {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 rgba(173, 216, 255, 0.9), stop:1 rgba(135, 206, 235, 0.8));
            color: #1e3a8a;
            padding: 12px;
            border: none;
            border-bottom: 1px solid rgba(30, 144, 255, 0.3);
            font-weight: 700;
            font-size: 13px;
        }
        
        /* Progress Bar Styling - Colorful Aero */
        QProgressBar {
            border: 1px solid rgba(135, 206, 235, 0.6);
            border-radius: 8px;
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 rgba(255, 255, 255, 0.9), stop:1 rgba(248, 252, 255, 0.8));
            text-align: center;
            color: #2c3e50;
            font-weight: 600;
            font-size: 12px;
        }
        
        QProgressBar::chunk {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #ff6b6b, stop:0.2 #ffa500, stop:0.4 #ffeb3b, 
                stop:0.6 #4caf50, stop:0.8 #2196f3, stop:1 #9c27b0);
            border-radius: 6px;
        }
        
        /* Group Box Styling - Glassy Containers */
        QGroupBox {
            font-weight: 600;
            border: 1px solid rgba(135, 206, 235, 0.6);
            border-radius: 12px;
            margin-top: 15px;
            padding-top: 15px;
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 rgba(255, 255, 255, 0.8), stop:1 rgba(240, 248, 255, 0.7));
            color: #2c3e50;
        }
        
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 15px;
            padding: 0 10px 0 10px;
            color: #1e3a8a;
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 rgba(255, 255, 255, 0.9), stop:1 rgba(240, 248, 255, 0.8));
            border-radius: 4px;
        }
        
        /* List Widget Styling */
        QListWidget {
            background-color: #2d2d2d;
            border: 1px solid #3c3c3c;
            border-radius: 6px;
            alternate-background-color: #333333;
        }
        
        QListWidget::item {
            padding: 8px;
            border-bottom: 1px solid #3c3c3c;
        }
        
        QListWidget::item:selected {
            background-color: #0078d4;
        }
        
        QListWidget::item:hover {
            background-color: #404040;
        }
        
        /* Text Edit Styling */
        QTextEdit, QPlainTextEdit {
            background-color: #2d2d2d;
            border: 1px solid #3c3c3c;
            border-radius: 6px;
            color: #ffffff;
            font-family: 'Consolas', 'Courier New', monospace;
            font-size: 13px;
            selection-background-color: #0078d4;
        }
        
        /* Status Bar Styling - Glassy Bottom */
        QStatusBar {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 rgba(173, 216, 255, 0.8), stop:1 rgba(135, 206, 235, 0.7));
            border-top: 1px solid rgba(30, 144, 255, 0.3);
            color: #1e3a8a;
            font-weight: 600;
        }
        
        QStatusBar QLabel {
            padding: 6px 12px;
            background: rgba(255, 255, 255, 0.3);
            border-radius: 4px;
            margin: 2px;
        }
        
        /* Scrollbar Styling - Glassy Scrollbars */
        QScrollBar:vertical {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 rgba(255, 255, 255, 0.7), stop:1 rgba(240, 248, 255, 0.6));
            width: 16px;
            border-radius: 8px;
            border: 1px solid rgba(135, 206, 235, 0.3);
        }
        
        QScrollBar::handle:vertical {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 rgba(173, 216, 255, 0.8), stop:1 rgba(135, 206, 235, 0.7));
            border-radius: 7px;
            min-height: 25px;
            border: 1px solid rgba(30, 144, 255, 0.4);
        }
        
        QScrollBar::handle:vertical:hover {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 rgba(100, 200, 255, 0.9), stop:1 rgba(30, 144, 255, 0.8));
        }
        
        QScrollBar:horizontal {
            background-color: #2d2d2d;
            height: 12px;
            border-radius: 6px;
        }
        
        QScrollBar::handle:horizontal {
            background-color: #484848;
            border-radius: 6px;
            min-width: 20px;
        }
        
        QScrollBar::handle:horizontal:hover {
            background-color: #0078d4;
        }
        
        QScrollBar::add-line, QScrollBar::sub-line {
            border: none;
            background: none;
        }
        
        /* Checkbox and Radio Button Styling */
        QCheckBox {
            color: #ffffff;
            spacing: 8px;
        }
        
        QCheckBox::indicator {
            width: 18px;
            height: 18px;
            border-radius: 3px;
            border: 1px solid #3c3c3c;
            background-color: #2d2d2d;
        }
        
        QCheckBox::indicator:checked {
            background-color: #0078d4;
            border-color: #0078d4;
        }
        
        /* Tooltip Styling - Glassy Aero Tooltips */
        QToolTip {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 rgba(255, 255, 255, 0.95), stop:1 rgba(240, 248, 255, 0.9));
            color: #2c3e50;
            border: 2px solid rgba(30, 144, 255, 0.6);
            border-radius: 8px;
            padding: 12px 16px;
            font-size: 13px;
            font-weight: 500;
            box-shadow: 0px 4px 8px rgba(0, 0, 0, 0.2);
        }
        """
        
        self.setStyleSheet(app_style)
        
        # Set window properties for better appearance
        self.setMinimumSize(1000, 700)
        self.resize(1200, 800)

    def init_ui(self):
        # Create central widget with tabs
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        
        # Initialize all tabs
        self.setup_dashboard_tab()
        self.setup_explorer_tab()
        self.setup_transfers_tab()
        self.setup_logs_tab()
        self.setup_quest_tab()
        
        # Apply comprehensive styling
        self.apply_custom_styling()
        
        # Add update menu
        self.add_update_menu()
    
    def setup_dashboard_tab(self):
        """Setup the enhanced dashboard with beautiful visual elements and system overview"""
        dashboard_widget = QWidget()
        self.tabs.addTab(dashboard_widget, "🏠 Dashboard")
        main_layout = QVBoxLayout(dashboard_widget)
        main_layout.setSpacing(20)
        main_layout.setContentsMargins(20, 20, 20, 20)
        
        # Welcome header with app info
        welcome_section = QGroupBox("🌟 Welcome to NGBrowser")
        welcome_layout = QVBoxLayout(welcome_section)
        
        app_title = QLabel("NGBrowser v1.0.1")
        app_title.setStyleSheet("""
            QLabel {
                font-size: 28px;
                font-weight: bold;
                color: #1e3a8a;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(255, 255, 255, 0.0), stop:0.5 rgba(173, 216, 255, 0.3), stop:1 rgba(255, 255, 255, 0.0));
                border-radius: 8px;
                padding: 10px;
                margin: 5px;
            }
        """)
        app_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        app_subtitle = QLabel("🥽 Quest VR Sideloading • ☁️ Cloud Storage • 🔄 Auto-Updates")
        app_subtitle.setStyleSheet("""
            QLabel {
                font-size: 16px;
                font-weight: 500;
                color: #2c3e50;
                text-align: center;
                margin: 5px;
            }
        """)
        app_subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        welcome_layout.addWidget(app_title)
        welcome_layout.addWidget(app_subtitle)
        main_layout.addWidget(welcome_section)
        
        # Create two-column layout for stats
        stats_container = QHBoxLayout()
        
        # System Stats Section (Enhanced)
        stats_group = QGroupBox("📊 System Overview")
        stats_layout = QVBoxLayout(stats_group)
        
        # Create grid layout for stats
        stats_grid = QVBoxLayout()
        
        # Uptime stat with icon
        uptime_container = QHBoxLayout()
        uptime_icon = QLabel("⏱️")
        uptime_icon.setStyleSheet("font-size: 20px; margin-right: 8px;")
        uptime_label_text = QLabel("Uptime:")
        uptime_label_text.setStyleSheet("font-weight: 600; color: #2c3e50;")
        self.uptime_label = QLabel("0 seconds")
        self.uptime_label.setStyleSheet("""
            QLabel {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(173, 216, 255, 0.3), stop:1 rgba(135, 206, 235, 0.2));
                border-radius: 6px;
                padding: 6px 12px;
                font-weight: bold;
                color: #1e3a8a;
            }
        """)
        uptime_container.addWidget(uptime_icon)
        uptime_container.addWidget(uptime_label_text)
        uptime_container.addStretch()
        uptime_container.addWidget(self.uptime_label)
        stats_grid.addLayout(uptime_container)
        
        # Total transfers stat
        transfer_container = QHBoxLayout()
        transfer_icon = QLabel("📊")
        transfer_icon.setStyleSheet("font-size: 20px; margin-right: 8px;")
        transfer_label_text = QLabel("Total Transferred:")
        transfer_label_text.setStyleSheet("font-weight: 600; color: #2c3e50;")
        self.total_transfers_label = QLabel("0 B")
        self.total_transfers_label.setStyleSheet("""
            QLabel {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(76, 175, 80, 0.3), stop:1 rgba(139, 195, 74, 0.2));
                border-radius: 6px;
                padding: 6px 12px;
                font-weight: bold;
                color: #2e7d32;
            }
        """)
        transfer_container.addWidget(transfer_icon)
        transfer_container.addWidget(transfer_label_text)
        transfer_container.addStretch()
        transfer_container.addWidget(self.total_transfers_label)
        stats_grid.addLayout(transfer_container)
        
        # Active transfers stat
        active_container = QHBoxLayout()
        active_icon = QLabel("🔄")
        active_icon.setStyleSheet("font-size: 20px; margin-right: 8px;")
        active_label_text = QLabel("Active Transfers:")
        active_label_text.setStyleSheet("font-weight: 600; color: #2c3e50;")
        self.active_transfers_label = QLabel("0")
        self.active_transfers_label.setStyleSheet("""
            QLabel {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(255, 152, 0, 0.3), stop:1 rgba(255, 193, 7, 0.2));
                border-radius: 6px;
                padding: 6px 12px;
                font-weight: bold;
                color: #f57c00;
            }
        """)
        active_container.addWidget(active_icon)
        active_container.addWidget(active_label_text)
        active_container.addStretch()
        active_container.addWidget(self.active_transfers_label)
        stats_grid.addLayout(active_container)
        
        # Remotes count stat
        remotes_container = QHBoxLayout()
        remotes_icon = QLabel("☁️")
        remotes_icon.setStyleSheet("font-size: 20px; margin-right: 8px;")
        remotes_label_text = QLabel("Cloud Remotes:")
        remotes_label_text.setStyleSheet("font-weight: 600; color: #2c3e50;")
        self.remotes_count_label = QLabel("0")
        self.remotes_count_label.setStyleSheet("""
            QLabel {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(156, 39, 176, 0.3), stop:1 rgba(171, 71, 188, 0.2));
                border-radius: 6px;
                padding: 6px 12px;
                font-weight: bold;
                color: #7b1fa2;
            }
        """)
        remotes_container.addWidget(remotes_icon)
        remotes_container.addWidget(remotes_label_text)
        remotes_container.addStretch()
        remotes_container.addWidget(self.remotes_count_label)
        stats_grid.addLayout(remotes_container)
        
        stats_layout.addLayout(stats_grid)
        stats_container.addWidget(stats_group)
        
        # Quest Status Section
        quest_status_group = QGroupBox("🥽 Quest Status")
        quest_status_layout = QVBoxLayout(quest_status_group)
        
        self.quest_status_label = QLabel("🔴 Not Connected")
        self.quest_status_label.setStyleSheet("""
            QLabel {
                font-size: 16px;
                font-weight: bold;
                padding: 12px;
                border-radius: 8px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(244, 67, 54, 0.3), stop:1 rgba(229, 115, 115, 0.2));
                color: #c62828;
            }
        """)
        self.quest_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        quest_info_label = QLabel("Connect your Quest headset to enable sideloading features")
        quest_info_label.setStyleSheet("""
            QLabel {
                color: #666;
                font-style: italic;
                text-align: center;
                margin: 8px;
            }
        """)
        quest_info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        quest_status_layout.addWidget(self.quest_status_label)
        quest_status_layout.addWidget(quest_info_label)
        stats_container.addWidget(quest_status_group)
        
        main_layout.addLayout(stats_container)
        
        # Recent Activity Section (Enhanced)
        activity_group = QGroupBox("📋 Recent Activity")
        activity_layout = QVBoxLayout(activity_group)
        
        # Add activity controls
        activity_controls = QHBoxLayout()
        clear_activity_btn = QPushButton("🗑️ Clear Activity")
        clear_activity_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(244, 67, 54, 0.8), stop:1 rgba(229, 115, 115, 0.7));
                color: white;
                font-weight: bold;
                border: 1px solid rgba(198, 40, 40, 0.8);
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(244, 67, 54, 0.9), stop:1 rgba(229, 115, 115, 0.8));
            }
        """)
        clear_activity_btn.clicked.connect(self.clear_activity_log)
        activity_controls.addStretch()
        activity_controls.addWidget(clear_activity_btn)
        activity_layout.addLayout(activity_controls)
        
        self.activity_list = QListWidget()
        self.activity_list.setStyleSheet("""
            QListWidget {
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 12px;
                min-height: 200px;
            }
        """)
        activity_layout.addWidget(self.activity_list)
        
        main_layout.addWidget(activity_group)
        
        # Add some initial activity messages
        self.add_activity_message("🚀 NGBrowser started successfully")
        self.add_activity_message("⚙️ Initializing cloud storage connections...")
        self.add_activity_message("🥽 Quest sideloading features ready")
    
    def add_activity_message(self, message):
        """Add a timestamped message to the activity log"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        formatted_message = f"[{timestamp}] {message}"
        
        # Create styled list item
        item = QListWidgetItem(formatted_message)
        
        # Color code different types of messages
        if "🚀" in message or "started" in message.lower():
            item.setBackground(QColor(76, 175, 80, 30))  # Green tint
        elif "⚙️" in message or "initializing" in message.lower():
            item.setBackground(QColor(255, 152, 0, 30))  # Orange tint
        elif "🥽" in message or "quest" in message.lower():
            item.setBackground(QColor(156, 39, 176, 30))  # Purple tint
        elif "❌" in message or "error" in message.lower():
            item.setBackground(QColor(244, 67, 54, 30))  # Red tint
        elif "✅" in message or "success" in message.lower():
            item.setBackground(QColor(76, 175, 80, 40))  # Stronger green
        
        if hasattr(self, 'activity_list'):
            self.activity_list.addItem(item)
            # Auto-scroll to bottom
            self.activity_list.scrollToBottom()
            
            # Limit activity log to 100 items
            if self.activity_list.count() > 100:
                self.activity_list.takeItem(0)
    
    def clear_activity_log(self):
        """Clear all activity log messages"""
        if hasattr(self, 'activity_list'):
            self.activity_list.clear()
            self.add_activity_message("🗑️ Activity log cleared")
    
    def update_quest_status(self, connected=False, device_name=""):
        """Update the Quest connection status on the dashboard"""
        if hasattr(self, 'quest_status_label'):
            if connected:
                self.quest_status_label.setText(f"🟢 Connected: {device_name}")
                self.quest_status_label.setStyleSheet("""
                    QLabel {
                        font-size: 16px;
                        font-weight: bold;
                        padding: 12px;
                        border-radius: 8px;
                        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                            stop:0 rgba(76, 175, 80, 0.3), stop:1 rgba(139, 195, 74, 0.2));
                        color: #2e7d32;
                    }
                """)
                self.add_activity_message(f"🟢 Quest device connected: {device_name}")
            else:
                self.quest_status_label.setText("🔴 Not Connected")
                self.quest_status_label.setStyleSheet("""
                    QLabel {
                        font-size: 16px;
                        font-weight: bold;
                        padding: 12px;
                        border-radius: 8px;
                        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                            stop:0 rgba(244, 67, 54, 0.3), stop:1 rgba(229, 115, 115, 0.2));
                        color: #c62828;
                    }
                """)
                if device_name:
                    self.add_activity_message(f"🔴 Quest device disconnected: {device_name}")
    
    def setup_explorer_tab(self):
        """Setup enhanced file explorer with advanced features"""
        explorer_widget = QWidget()
        self.tabs.addTab(explorer_widget, "📁 Explorer")
        layout = QVBoxLayout(explorer_widget)
        
        # Navigation and path display
        nav_layout = QHBoxLayout()
        
        # Navigation buttons
        self.back_btn = QPushButton("← Back")
        self.back_btn.setEnabled(False)
        self.back_btn.clicked.connect(self.go_back)
        nav_layout.addWidget(self.back_btn)
        
        self.up_btn = QPushButton("↑ Up")
        self.up_btn.clicked.connect(self.go_up)
        nav_layout.addWidget(self.up_btn)
        
        self.home_btn = QPushButton("🏠 Root")
        self.home_btn.clicked.connect(self.go_home)
        nav_layout.addWidget(self.home_btn)
        
        # Path display and search
        self.path_label = QLabel("Path: /")
        nav_layout.addWidget(self.path_label)
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search files...")
        self.search_input.returnPressed.connect(self.search_files)
        nav_layout.addWidget(self.search_input)
        
        search_btn = QPushButton("🔍 Search")
        search_btn.clicked.connect(self.search_files)
        nav_layout.addWidget(search_btn)
        
        layout.addLayout(nav_layout)
        
        # Action buttons
        actions_layout = QHBoxLayout()
        
        self.upload_btn = QPushButton("📤 Upload")
        self.upload_btn.clicked.connect(self.upload_file)
        actions_layout.addWidget(self.upload_btn)
        
        self.download_btn = QPushButton("📥 Download")
        self.download_btn.clicked.connect(self.download_selected)
        actions_layout.addWidget(self.download_btn)
        

        
        actions_layout.addStretch()
        layout.addLayout(actions_layout)
        
        # File table with enhanced features
        self.file_table = QTableWidget()
        self.file_table.setColumnCount(5)
        self.file_table.setHorizontalHeaderLabels(["Name", "Size", "Type", "Modified", "Permissions"])
        self.file_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.file_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.file_table.cellDoubleClicked.connect(self.on_cell_double_clicked)
        self.file_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.file_table.customContextMenuRequested.connect(self.show_context_menu)
        self.file_table.itemSelectionChanged.connect(self.update_selection)
        layout.addWidget(self.file_table)
        self.file_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    


    def setup_transfers_tab(self):
        """Setup transfer monitoring and management"""
        transfers_widget = QWidget()
        self.tabs.addTab(transfers_widget, "📊 Transfers")
        layout = QVBoxLayout(transfers_widget)
        
        # Transfer controls
        controls_layout = QHBoxLayout()
        
        self.pause_all_btn = QPushButton("⏸️ Pause All")
        self.pause_all_btn.clicked.connect(self.pause_all_transfers)
        controls_layout.addWidget(self.pause_all_btn)
        
        self.resume_all_btn = QPushButton("▶️ Resume All")
        self.resume_all_btn.clicked.connect(self.resume_all_transfers)
        controls_layout.addWidget(self.resume_all_btn)
        
        self.cancel_all_btn = QPushButton("⏹️ Cancel All")
        self.cancel_all_btn.clicked.connect(self.cancel_all_transfers)
        controls_layout.addWidget(self.cancel_all_btn)
        
        controls_layout.addStretch()
        
        # Bandwidth limit
        bw_label = QLabel("Bandwidth Limit:")
        controls_layout.addWidget(bw_label)
        
        self.bandwidth_slider = QSlider(Qt.Orientation.Horizontal)
        self.bandwidth_slider.setRange(0, 10000)  # 0-10MB/s in KB/s
        self.bandwidth_slider.setValue(self.bandwidth_limit)
        self.bandwidth_slider.valueChanged.connect(self.update_bandwidth_limit)
        controls_layout.addWidget(self.bandwidth_slider)
        
        self.bandwidth_label = QLabel("Unlimited")
        controls_layout.addWidget(self.bandwidth_label)
        
        layout.addLayout(controls_layout)
        
        # Transfer table
        self.transfers_table = QTableWidget()
        self.transfers_table.setColumnCount(7)
        self.transfers_table.setHorizontalHeaderLabels(["File", "Source", "Destination", "Progress", "Speed", "ETA", "Status"])
        self.transfers_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.transfers_table)
        
        self.transfers = []
    
    def setup_logs_tab(self):
        """Setup logs viewer with filtering"""
        logs_widget = QWidget()
        self.tabs.addTab(logs_widget, "📝 Logs")
        layout = QVBoxLayout(logs_widget)
        
        # Log controls
        log_controls = QHBoxLayout()
        
        self.log_level_combo = QComboBox()
        self.log_level_combo.addItems(["All", "Error", "Warning", "Info", "Debug"])
        self.log_level_combo.currentTextChanged.connect(self.filter_logs)
        log_controls.addWidget(QLabel("Level:"))
        log_controls.addWidget(self.log_level_combo)
        
        self.auto_scroll_check = QCheckBox("Auto-scroll")
        self.auto_scroll_check.setChecked(True)
        log_controls.addWidget(self.auto_scroll_check)
        
        clear_logs_btn = QPushButton("🗑️ Clear Logs")
        clear_logs_btn.clicked.connect(self.clear_logs)
        log_controls.addWidget(clear_logs_btn)
        
        export_logs_btn = QPushButton("📤 Export Logs")
        export_logs_btn.clicked.connect(self.export_logs)
        log_controls.addWidget(export_logs_btn)
        
        log_controls.addStretch()
        layout.addLayout(log_controls)
        
        # Log viewer
        self.log_viewer = QPlainTextEdit()
        self.log_viewer.setReadOnly(True)
        self.log_viewer.setFont(QFont("Consolas", 10))
        layout.addWidget(self.log_viewer)
    
    def setup_quest_tab(self):
        """Setup Quest VR sideloading tab"""
        quest_widget = QWidget()
        self.tabs.addTab(quest_widget, "🥽 Quest")
        main_layout = QVBoxLayout(quest_widget)
        
        # Quest Status Section
        status_group = QGroupBox("Quest Headset Status")
        status_layout = QVBoxLayout(status_group)
        
        # Connection status
        self.quest_status_label = QLabel("🔴 No Quest Connected")
        self.quest_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status_layout.addWidget(self.quest_status_label)
        
        # Connection buttons
        connection_layout = QHBoxLayout()
        self.refresh_quest_btn = QPushButton("🔄 Refresh Devices")
        self.refresh_quest_btn.clicked.connect(self.refresh_quest_devices)
        connection_layout.addWidget(self.refresh_quest_btn)
        
        self.enable_wireless_btn = QPushButton("📡 Enable Wireless ADB")
        self.enable_wireless_btn.clicked.connect(self.enable_wireless_adb)
        connection_layout.addWidget(self.enable_wireless_btn)
        
        self.pair_quest_btn = QPushButton("🔗 Pair Quest")
        self.pair_quest_btn.clicked.connect(self.pair_quest_device)
        connection_layout.addWidget(self.pair_quest_btn)
        
        status_layout.addLayout(connection_layout)
        main_layout.addWidget(status_group)
        
        # Device Info Section
        device_group = QGroupBox("Device Information")
        device_layout = QFormLayout(device_group)
        
        self.device_model_label = QLabel("N/A")
        self.device_battery_label = QLabel("N/A")
        self.device_storage_label = QLabel("N/A")
        self.device_version_label = QLabel("N/A")
        
        device_layout.addRow("Model:", self.device_model_label)
        device_layout.addRow("Battery:", self.device_battery_label)
        device_layout.addRow("Storage:", self.device_storage_label)
        device_layout.addRow("OS Version:", self.device_version_label)
        
        main_layout.addWidget(device_group)
        
        # APK Management Section
        apk_group = QGroupBox("APK Management")
        apk_layout = QVBoxLayout(apk_group)
        
        # APK installation
        install_layout = QHBoxLayout()
        self.install_apk_btn = QPushButton("📱 Install APK from PC")
        self.install_apk_btn.clicked.connect(self.install_apk_from_pc)
        install_layout.addWidget(self.install_apk_btn)
        
        self.install_cloud_btn = QPushButton("☁️ Install APK from Cloud")
        self.install_cloud_btn.clicked.connect(self.install_apk_from_cloud)
        install_layout.addWidget(self.install_cloud_btn)
        
        apk_layout.addLayout(install_layout)
        
        # Installed apps table
        self.installed_apps_table = QTableWidget()
        self.installed_apps_table.setColumnCount(5)
        self.installed_apps_table.setHorizontalHeaderLabels(["App Name", "Package", "Version", "Size", "Actions"])
        self.installed_apps_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        apk_layout.addWidget(self.installed_apps_table)
        
        # App management buttons
        app_controls = QHBoxLayout()
        self.refresh_apps_btn = QPushButton("🔄 Refresh Apps")
        self.refresh_apps_btn.clicked.connect(self.refresh_installed_apps)
        app_controls.addWidget(self.refresh_apps_btn)
        
        self.backup_apk_btn = QPushButton("💾 Backup APK")
        self.backup_apk_btn.clicked.connect(self.backup_selected_apk)
        app_controls.addWidget(self.backup_apk_btn)
        
        self.uninstall_app_btn = QPushButton("🗑️ Uninstall App")
        self.uninstall_app_btn.clicked.connect(self.uninstall_selected_app)
        app_controls.addWidget(self.uninstall_app_btn)
        
        app_controls.addStretch()
        apk_layout.addLayout(app_controls)
        
        main_layout.addWidget(apk_group)
        
        # File Transfer Section
        transfer_group = QGroupBox("File Transfer")
        transfer_layout = QVBoxLayout(transfer_group)
        
        # Transfer buttons
        transfer_buttons = QHBoxLayout()
        self.push_files_btn = QPushButton("📤 Push Files to Quest")
        self.push_files_btn.clicked.connect(self.push_files_to_quest)
        transfer_buttons.addWidget(self.push_files_btn)
        
        self.pull_files_btn = QPushButton("📥 Pull Files from Quest")
        self.pull_files_btn.clicked.connect(self.pull_files_from_quest)
        transfer_buttons.addWidget(self.pull_files_btn)
        
        transfer_layout.addLayout(transfer_buttons)
        
        main_layout.addWidget(transfer_group)
        
        # Initialize Quest functionality
        self.quest_connected = False
        self.connected_quest_device = None
        self.adb_path = None  # Will be initialized after UI setup

    def apply_custom_styling(self):
        """Apply comprehensive custom styling matching the original theme"""
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #E0F7FA, stop:1 #B2EBF2);
            }
            QTabWidget::pane { 
                border: 1px solid #80DEEA;
                border-top: 0px;
                background-color: rgba(255, 255, 255, 0.8);
            }
            QTabBar::tab {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #B2EBF2, stop:1 #80DEEA);
                border: 1px solid #4DD0E1;
                border-bottom: none;
                padding: 8px 20px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                color: #004D40;
                font-weight: bold;
            }
            QTabBar::tab:selected {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #FFFFFF, stop:1 #E0F7FA);
                color: #00796B;
            }
            QLabel {
                font-family: "Segoe UI", Arial, sans-serif;
                font-size: 14px;
                color: #004D40;
                background: transparent;
                font-weight: bold;
                padding: 5px;
            }
            QPushButton {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #FFFFFF, stop:1 #E0F7FA);
                color: #00796B;
                border: 1px solid #80DEEA;
                border-radius: 5px;
                padding: 8px;
                font-family: "Segoe UI", Arial, sans-serif;
                font-size: 14px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #E0F7FA;
                border-color: #26C6DA;
            }
            QTableWidget { 
                background-color: rgba(255, 255, 255, 0.9);
                color: #004D40;
                gridline-color: #B2EBF2;
                border: 1px solid #80DEEA;
                font-family: "Segoe UI", Arial, sans-serif;
                alternate-background-color: #E0F7FA;
            }
            QHeaderView::section { 
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #80DEEA, stop:1 #4DD0E1);
                color: #004D40;
                padding: 5px;
                border: 1px solid #4DD0E1;
                font-family: "Segoe UI", Arial, sans-serif;
                font-size: 14px;
                font-weight: bold;
            }
            QProgressBar {
                border: 1px solid #80DEEA;
                border-radius: 4px;
                background-color: #FFFFFF;
                text-align: center;
                color: #004D40;
            }
            QProgressBar::chunk {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                      stop:0 #FF5555, stop:0.2 #FF9900, stop:0.4 #FFFF00, 
                                      stop:0.6 #00CC00, stop:0.8 #0066FF, stop:1 #7A00CC);
                border-radius: 3px;
            }
        """)
    
    def setup_status_bar(self):
        """Setup status bar with system information"""
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        
        # Status labels
        self.connection_status = QLabel("🔴 Disconnected")
        self.transfer_status = QLabel("📊 Idle")
        self.memory_status = QLabel("💾 0 MB")
        
        self.status_bar.addPermanentWidget(self.connection_status)
        self.status_bar.addPermanentWidget(self.transfer_status)
        self.status_bar.addPermanentWidget(self.memory_status)
        
        self.status_bar.showMessage("NGBrowser ready")
    
    def setup_timers(self):
        """Setup update timers for real-time information"""
        # Dashboard update timer
        self.dashboard_timer = QTimer()
        self.dashboard_timer.timeout.connect(self.update_dashboard)
        self.dashboard_timer.start(5000)  # Update every 5 seconds
        
        # Status update timer
        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self.update_status)
        self.status_timer.start(2000)  # Update every 2 seconds
        
        # Fast event processing timer for real-time log updates during transfers
        self.event_processing_timer = QTimer()
        self.event_processing_timer.timeout.connect(self.process_events_during_transfers)
        self.event_processing_timer.start(100)  # Process events every 100ms for real-time updates
    
    def update_dashboard(self):
        """Update dashboard statistics"""
        try:
            # Update uptime
            uptime = datetime.now() - self.start_time
            days = uptime.days
            hours, remainder = divmod(uptime.seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            uptime_str = f"{days}d {hours}h {minutes}m {seconds}s"
            self.uptime_label.setText(uptime_str)
            
            # Update total transferred (format bytes)
            self.total_transfers_label.setText(self.format_bytes(self.total_transferred))
            
            # Update active transfers
            self.active_transfers_label.setText(str(self.active_transfers))
            
            # Update remotes count
            self.update_remotes_count()
            
        except Exception as e:
            self.log_message(f"Dashboard update error: {str(e)}", "error")
    
    def update_status(self):
        """Update status bar information"""
        try:
            # Update memory usage
            process = psutil.Process()
            memory_mb = process.memory_info().rss / 1024 / 1024
            self.memory_status.setText(f"💾 {memory_mb:.1f} MB")
            
            # Update transfer status
            if self.active_transfers > 0:
                self.transfer_status.setText(f"📊 {self.active_transfers} active")
                self.connection_status.setText("🟢 Connected")
            else:
                self.transfer_status.setText("📊 Idle")
                self.connection_status.setText("🔴 Idle")
                
        except Exception as e:
            self.log_message(f"Status update error: {str(e)}", "error")
    
    def process_events_during_transfers(self):
        """Force event processing during active transfers for real-time log updates"""
        try:
            # Check if any transfers are active
            has_active_transfers = any(worker.isRunning() for worker in self.transfers if worker)
            
            if has_active_transfers:
                # Force Qt event loop to process queued signals immediately
                QApplication.processEvents()
                
                # Force log viewer to update immediately
                if hasattr(self, 'log_viewer'):
                    self.log_viewer.update()
                    
                # Force auto-scroll if enabled
                if hasattr(self, 'auto_scroll_check') and self.auto_scroll_check.isChecked():
                    scrollbar = self.log_viewer.verticalScrollBar()
                    scrollbar.setValue(scrollbar.maximum())
                    
        except Exception as e:
            # Don't log this error to avoid recursion
            pass
    
    def format_bytes(self, bytes_value):
        """Format bytes into human readable format"""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if bytes_value < 1024.0:
                return f"{bytes_value:.1f} {unit}"
            bytes_value /= 1024.0
        return f"{bytes_value:.1f} PB"
    
    def log_message(self, message, level="info"):
        """Add message to log viewer"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        level_icon = {"error": "❌", "warning": "⚠️", "info": "ℹ️", "debug": "🔧"}
        icon = level_icon.get(level, "ℹ️")
        
        log_entry = f"[{timestamp}] {icon} {message}"
        self.log_viewer.appendPlainText(log_entry)
        
        # Add to recent activity
        self.add_activity(message)
        
        # Auto-scroll if enabled
        if hasattr(self, 'auto_scroll_check') and self.auto_scroll_check.isChecked():
            scrollbar = self.log_viewer.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
    
    def add_activity(self, activity):
        """Add activity to dashboard recent activity list"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        item = QListWidgetItem(f"[{timestamp}] {activity}")
        self.activity_list.insertItem(0, item)
        
        # Keep only last 50 activities
        while self.activity_list.count() > 50:
            self.activity_list.takeItem(self.activity_list.count() - 1)
    
    # Enhanced file operations
    def update_selection(self):
        """Update selected files list when selection changes"""
        self.selected_files = []
        for item in self.file_table.selectedItems():
            if item.column() == 0:  # Only get filename column
                self.selected_files.append(item.text())
    
    def go_up(self):
        """Navigate to parent directory"""
        if self.current_path and self.current_path != "/":
            parent_path = "/".join(self.current_path.rstrip("/").split("/")[:-1])
            if not parent_path:
                parent_path = "/"
            self.current_path = parent_path
            self.list_files()
    
    def go_home(self):
        """Navigate to root directory"""
        self.current_path = "/"
        self.list_files()
    
    def search_files(self):
        """Search files in current remote"""
        search_term = self.search_input.text().strip()
        if not search_term or not self.current_remote:
            return
        
        self.log_message(f"Searching for '{search_term}' in {self.current_remote}")
        # Implement search functionality here
        # For now, just show a message
        QMessageBox.information(self, "Search", f"Search feature coming soon!\nSearching for: {search_term}")
    
    def download_selected(self):
        """Download selected files"""
        if not self.selected_files:
            QMessageBox.warning(self, "No Selection", "Please select files to download.")
            return
        
        for filename in self.selected_files:
            row = self.find_file_row(filename)
            if row >= 0:
                self.download_file(row)
    

    
    def find_file_row(self, filename):
        """Find row index of file in table"""
        for row in range(self.file_table.rowCount()):
            if self.file_table.item(row, 0).text() == filename:
                return row
        return -1
    
    # Dialog methods
    def quick_sync_dialog(self):
        """Show quick sync dialog"""
        dialog = QDialog(self)
        dialog.setWindowTitle("Quick Sync")
        dialog.setModal(True)
        layout = QVBoxLayout(dialog)
        
        # Source and destination selection
        form_layout = QFormLayout()
        
        source_combo = QComboBox()
        dest_combo = QComboBox()
        
        # Populate with remotes and local folders
        remotes = self.get_remotes_list()
        source_combo.addItems(["Local"] + remotes)
        dest_combo.addItems(["Local"] + remotes)
        
        form_layout.addRow("Source:", source_combo)
        form_layout.addRow("Destination:", dest_combo)
        
        layout.addLayout(form_layout)
        
        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            source = source_combo.currentText()
            dest = dest_combo.currentText()
            self.log_message(f"Quick sync: {source} → {dest}")
            QMessageBox.information(self, "Sync Started", f"Started sync from {source} to {dest}")
    
    def add_remote_dialog(self):
        """Show add remote dialog"""
        dialog = QDialog(self)
        dialog.setWindowTitle("Add Remote")
        dialog.setModal(True)
        dialog.resize(400, 300)
        layout = QVBoxLayout(dialog)
        
        form_layout = QFormLayout()
        
        name_input = QLineEdit()
        type_combo = QComboBox()
        type_combo.addItems(["s3", "google cloud storage", "azure blob", "dropbox", "onedrive", "ftp", "sftp"])
        
        form_layout.addRow("Remote Name:", name_input)
        form_layout.addRow("Remote Type:", type_combo)
        
        layout.addLayout(form_layout)
        
        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name = name_input.text().strip()
            remote_type = type_combo.currentText()
            if name:
                self.log_message(f"Adding remote: {name} ({remote_type})")
                QMessageBox.information(self, "Remote Added", f"Remote '{name}' configuration started.\nPlease configure it manually using rclone config.")
    
    def bandwidth_settings_dialog(self):
        """Show bandwidth settings dialog"""
        dialog = QDialog(self)
        dialog.setWindowTitle("Bandwidth Settings")
        dialog.setModal(True)
        layout = QVBoxLayout(dialog)
        
        form_layout = QFormLayout()
        
        bandwidth_spin = QSpinBox()
        bandwidth_spin.setRange(0, 100000)
        bandwidth_spin.setSuffix(" KB/s")
        bandwidth_spin.setSpecialValueText("Unlimited")
        bandwidth_spin.setValue(self.bandwidth_limit)
        
        form_layout.addRow("Bandwidth Limit:", bandwidth_spin)
        layout.addLayout(form_layout)
        
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.bandwidth_limit = bandwidth_spin.value()
            self.update_bandwidth_limit(self.bandwidth_limit)
            self.log_message(f"Bandwidth limit set to {self.bandwidth_limit} KB/s")
    
    def update_bandwidth_limit(self, value):
        """Update bandwidth limit setting"""
        self.bandwidth_limit = value
        if hasattr(self, 'bandwidth_slider'):
            self.bandwidth_slider.setValue(value)
        if hasattr(self, 'bandwidth_label'):
            if value == 0:
                self.bandwidth_label.setText("Unlimited")
            else:
                self.bandwidth_label.setText(f"{value} KB/s")
    
    # Additional utility methods
    def get_remotes_list(self):
        """Get list of configured remotes"""
        try:
            result = subprocess.run([self.rclone_path, "listremotes", "--config", self.config_path], 
                                  capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode == 0:
                remotes = [line.rstrip(':') for line in result.stdout.strip().split('\n') if line.strip()]
                return remotes
        except Exception as e:
            self.log_message(f"Error getting remotes: {str(e)}", "error")
        return []
    
    def update_remotes_count(self):
        """Update the count of configured remotes"""
        remotes = self.get_remotes_list()
        if hasattr(self, 'remotes_count_label'):
            self.remotes_count_label.setText(str(len(remotes)))
    
    # Missing dialog and operation methods

    
    def add_transfer_to_table(self, description, status, progress):
        """Add transfer to transfers table"""
        if not hasattr(self, 'transfers_table'):
            return
            
        row = self.transfers_table.rowCount()
        self.transfers_table.insertRow(row)
        
        self.transfers_table.setItem(row, 0, QTableWidgetItem(description))
        self.transfers_table.setItem(row, 1, QTableWidgetItem(self.current_remote or "Local"))
        self.transfers_table.setItem(row, 2, QTableWidgetItem("Destination"))
        
        progress_bar = QProgressBar()
        progress_bar.setValue(progress)
        self.transfers_table.setCellWidget(row, 3, progress_bar)
        
        self.transfers_table.setItem(row, 4, QTableWidgetItem("0 KB/s"))
        self.transfers_table.setItem(row, 5, QTableWidgetItem(status))
    
    def update_transfer_in_table(self, description, status, progress):
        """Update transfer in table"""
        if not hasattr(self, 'transfers_table'):
            return
            
        for row in range(self.transfers_table.rowCount()):
            if self.transfers_table.item(row, 0).text() == description:
                self.transfers_table.item(row, 5).setText(status)
                progress_bar = self.transfers_table.cellWidget(row, 3)
                if progress_bar:
                    progress_bar.setValue(progress)
                break
    
    # Transfer control methods
    def pause_all_transfers(self):
        """Pause all active transfers"""
        self.log_message("Pausing all transfers")
        QMessageBox.information(self, "Transfers", "Pause functionality will be implemented in future version.")
    
    def resume_all_transfers(self):
        """Resume all paused transfers"""
        self.log_message("Resuming all transfers")
        QMessageBox.information(self, "Transfers", "Resume functionality will be implemented in future version.")
    
    def cancel_all_transfers(self):
        """Cancel all active transfers"""
        reply = QMessageBox.question(self, "Cancel Transfers", 
                                   "Are you sure you want to cancel all active transfers?",
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        
        if reply == QMessageBox.StandardButton.Yes:
            self.log_message("Cancelling all transfers")
            self.active_transfers = 0
            self.transfers_table.setRowCount(0)
    

    # Settings and configuration methods



    # Log management methods
    def filter_logs(self, level):
        """Filter logs by level"""
        # This would implement log filtering - for now just show message
        self.log_message(f"Log filter set to: {level}")
    
    def clear_logs(self):
        """Clear all logs"""
        if hasattr(self, 'log_viewer'):
            self.log_viewer.clear()
            self.log_message("Logs cleared")
    
    def export_logs(self):
        """Export logs to file"""
        if not hasattr(self, 'log_viewer'):
            return
            
        filename, _ = QFileDialog.getSaveFileName(self, "Export Logs", "ngbrowser_logs.txt", "Text Files (*.txt)")
        if filename:
            try:
                with open(filename, 'w', encoding='utf-8') as f:
                    f.write(self.log_viewer.toPlainText())
                self.log_message(f"Logs exported to: {filename}")
                QMessageBox.information(self, "Export Complete", f"Logs exported to:\n{filename}")
            except Exception as e:
                self.log_message(f"Error exporting logs: {str(e)}", "error")
                QMessageBox.critical(self, "Export Error", f"Error exporting logs: {str(e)}")
    
    # Settings methods
    def browse_rclone_path(self):
        """Browse for rclone executable"""
        filename, _ = QFileDialog.getOpenFileName(self, "Select Rclone Executable", 
                                                self.rclone_path, "Executable Files (*.exe)")
        if filename:
            self.rclone_path_input.setText(filename)
    
    def browse_config_path(self):
        """Browse for rclone config file"""
        filename, _ = QFileDialog.getOpenFileName(self, "Select Rclone Config File", 
                                                self.config_path, "Config Files (*.conf *.ini)")
        if filename:
            self.config_path_input.setText(filename)
    
    def save_settings(self):
        """Save application settings"""
        # Update paths
        self.rclone_path = self.rclone_path_input.text()
        self.config_path = self.config_path_input.text()
        
        # Update bandwidth
        self.bandwidth_limit = self.default_bandwidth_spin.value()
        
        # Update filters
        include_text = self.include_patterns.toPlainText().strip()
        exclude_text = self.exclude_patterns.toPlainText().strip()
        
        self.filters['include'] = [line.strip() for line in include_text.split('\n') if line.strip()]
        self.filters['exclude'] = [line.strip() for line in exclude_text.split('\n') if line.strip()]
        
        self.log_message("Settings saved successfully")
        QMessageBox.information(self, "Settings Saved", "All settings have been saved successfully!")

    def load_remotes(self):
        if not os.path.exists(self.rclone_path):
            self.file_table.setRowCount(1)
            self.file_table.setItem(0, 0, QTableWidgetItem("Error: rclone.exe not found in script directory"))
            return
        try:
            result = subprocess.run(
                [self.rclone_path, "listremotes", "--config", self.config_path], 
                capture_output=True, text=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW
            )
            remotes = result.stdout.strip().splitlines()
            if remotes:
                self.current_remote = remotes[0]
                self.list_files()
            else:
                self.current_remote = ""
        except subprocess.CalledProcessError:
            self.file_table.setRowCount(1)
            self.file_table.setItem(0, 0, QTableWidgetItem("Error: No remotes configured or invalid rclone.conf"))

    def list_files(self, is_back_navigation=False):
        if not self.current_remote:
            return
            
        # Only add to history if it's a new navigation (not going back)
        if not is_back_navigation and (not self.nav_history or self.nav_history[-1] != self.current_path):
            self.nav_history.append(self.current_path)
            
        # Update back button state
        self.back_btn.setEnabled(len(self.nav_history) > 1)
        
        path = f"{self.current_remote}{self.current_path}"
        try:
            result = subprocess.run(
                [self.rclone_path, "lsjson", path, "--config", self.config_path], 
                capture_output=True, text=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW
            )
            files = json.loads(result.stdout)
            self.file_table.setRowCount(len(files))
            for i, file in enumerate(files):
                self.file_table.setItem(i, 0, QTableWidgetItem(file["Name"]))
                size = "-" if file["IsDir"] else f"{file['Size']/1024:.2f} KB"
                self.file_table.setItem(i, 1, QTableWidgetItem(size))
                type_item = QTableWidgetItem("Directory" if file["IsDir"] else "File")
                type_item.setData(Qt.ItemDataRole.UserRole, file["IsDir"])
                self.file_table.setItem(i, 2, type_item)
            self.path_label.setText(f"Path: /{self.current_path}")
        except subprocess.CalledProcessError:
            self.file_table.setRowCount(1)
            self.file_table.setItem(0, 0, QTableWidgetItem("Error listing files"))



    def go_back(self):
        if len(self.nav_history) > 1:
            # Remove current path from history
            self.nav_history.pop()
            # Get previous path
            self.current_path = self.nav_history[-1]
            # Remove the path we're going to from history (it will be re-added in list_files)
            self.nav_history.pop()
            # List files with back navigation flag
            self.list_files(is_back_navigation=True)

    def on_cell_double_clicked(self, row, column):
        item = self.file_table.item(row, 2)
        if item and item.data(Qt.ItemDataRole.UserRole):  # Directory
            name = self.file_table.item(row, 0).text()
            self.current_path = f"{self.current_path}{name}/"
            self.list_files()
        else:  # File
            self.download_file(row)



    def start_transfer(self, source, destination, direction, is_folder=False):
        """Start a transfer with proper validation and error handling"""
        try:
            # Validate inputs
            if not source or not destination:
                error_msg = "Invalid source or destination path"
                self.log_message(error_msg, "error")
                QMessageBox.warning(self, "Transfer Error", error_msg)
                return
            
            # Validate rclone path exists
            if not os.path.exists(self.rclone_path):
                error_msg = f"rclone executable not found at: {self.rclone_path}"
                self.log_message(error_msg, "error")
                QMessageBox.critical(self, "rclone Not Found", error_msg)
                return
            
            # Validate config path exists
            if not os.path.exists(self.config_path):
                error_msg = f"rclone config not found at: {self.config_path}"
                self.log_message(error_msg, "error")
                QMessageBox.critical(self, "Config Not Found", error_msg)
                return
            
            # Choose appropriate rclone command based on transfer type
            if is_folder and direction == "Download":
                # For folder downloads, use 'copy' to preserve directory structure
                rclone_cmd = "copy"
            else:
                # For single files, use 'copyto' for exact destination control
                rclone_cmd = "copyto"
            
            # Build command with proper parameters
            # Try multiple flags for better progress capture
            command = [
                self.rclone_path,
                rclone_cmd,
                source,
                destination,
                "--config", self.config_path,
                "-v",  # Verbose output for better parsing
                "--stats", "500ms",  # Update stats every 500ms for more frequent updates
                "--stats-one-line",  # Force stats to one line
                "--transfers", "1",  # Use 1 transfer to see progress more clearly
                "--checkers", "8",  # Use 8 checkers
                "--stats-file-name-length", "0",  # Don't truncate file names
            ]
            
            # Add bandwidth limiting if set
            if hasattr(self, 'bandwidth_limit') and self.bandwidth_limit:
                command.extend(["--bwlimit", str(self.bandwidth_limit)])
            
            # Proactive checks to prevent common errors
            self.log_message(f"Running pre-transfer checks...")
            
            # Check network connectivity
            network_ok, network_msg = self._check_network_connectivity()
            self.log_message(f"Network check: {network_msg}")
            if not network_ok:
                QMessageBox.warning(self, "Network Issue", network_msg)
                return
            
            # Check remote file existence for downloads
            if direction == "Download" and ':' in source:
                try:
                    # For files, use 'lsf' which is designed for file listing
                    # For directories, we would use 'lsjson' but since we're checking existence,
                    # we'll use 'lsf' for both and check if it returns any results
                    check_cmd = [self.rclone_path, "lsf", source, "--config", self.config_path]
                    result = subprocess.run(check_cmd, capture_output=True, text=True, timeout=10)
                    
                    if result.returncode != 0:
                        # If lsf fails, try checking if it's a directory with lsjson
                        if "directory not found" in result.stderr.lower():
                            # Try parent directory to see if the path structure is correct
                            parent_path = source.rsplit('/', 1)[0] if '/' in source else source.split(':', 1)[0] + ':'
                            check_parent_cmd = [self.rclone_path, "lsjson", parent_path, "--config", self.config_path]
                            parent_result = subprocess.run(check_parent_cmd, capture_output=True, text=True, timeout=10)
                            
                            if parent_result.returncode != 0:
                                self.log_message(f"Remote file check failed: {result.stderr}", "error")
                                QMessageBox.warning(self, "File Not Found", 
                                                  f"Remote file or directory not found or inaccessible:\n{source}\n\nError: {result.stderr}")
                                return
                            else:
                                # Parent exists, so the specific file doesn't exist
                                self.log_message(f"Remote file check failed: File not found in parent directory", "error")
                                QMessageBox.warning(self, "File Not Found", 
                                                  f"Remote file not found:\n{source}")
                                return
                        else:
                            self.log_message(f"Remote file check failed: {result.stderr}", "error")
                            QMessageBox.warning(self, "File Not Found", 
                                              f"Remote file or directory not found or inaccessible:\n{source}\n\nError: {result.stderr}")
                            return
                    else:
                        self.log_message(f"Remote file check: File exists")
                        
                except subprocess.TimeoutExpired:
                    self.log_message("Remote file check timed out - proceeding anyway", "warning")
                except Exception as e:
                    self.log_message(f"Could not verify remote file existence: {str(e)}", "warning")
            
            # Check disk space for downloads
            if direction == "Download":
                space_ok, space_msg = self._check_disk_space(destination)
                self.log_message(f"Disk space check: {space_msg}")
                if not space_ok:
                    reply = QMessageBox.question(self, "Disk Space Warning", 
                                                f"{space_msg}\n\nDo you want to continue anyway?",
                                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                    if reply == QMessageBox.StandardButton.No:
                        return
            
            self.log_message(f"Starting {direction.lower()}: {source} -> {destination}")
            self.log_message(f"Full Command: {' '.join(command)}")
            
            # Create worker with timeout
            worker = TransferWorker(command, timeout=7200)  # 2 hour timeout
            
            # Add to transfers table with thread safety
            row = self.transfers_table.rowCount()
            self.transfers_table.insertRow(row)
            self.transfers.append(worker)
            
            # Extract file name for display
            if direction == "Download":
                file_name = os.path.basename(source.split(':')[-1]) if ':' in source else os.path.basename(source)
            else:
                file_name = os.path.basename(destination)
            
            # Set up table items - Column 0: File
            name_item = QTableWidgetItem(file_name)
            name_item.setToolTip(f"{source} -> {destination}")
            self.transfers_table.setItem(row, 0, name_item)
            
            # Column 1: Source
            source_item = QTableWidgetItem(source)
            self.transfers_table.setItem(row, 1, source_item)
            
            # Column 2: Destination
            destination_item = QTableWidgetItem(destination)
            self.transfers_table.setItem(row, 2, destination_item)
            
            # Column 3: Progress
            progress_bar = QProgressBar()
            progress_bar.setMinimum(0)
            progress_bar.setMaximum(100)
            progress_bar.setFormat("%p%")
            self.transfers_table.setCellWidget(row, 3, progress_bar)
            
            # Column 4: Speed
            speed_item = QTableWidgetItem("0 KB/s")
            self.transfers_table.setItem(row, 4, speed_item)
            
            # Column 5: ETA
            eta_item = QTableWidgetItem("--")
            self.transfers_table.setItem(row, 5, eta_item)
            
            # Column 6: Status with Cancel button
            status_widget = QWidget()
            status_layout = QHBoxLayout(status_widget)
            status_layout.setContentsMargins(2, 2, 2, 2)
            
            status_label = QLabel("Initializing...")
            status_layout.addWidget(status_label)
            
            cancel_button = QPushButton("Cancel")
            cancel_button.setMaximumWidth(60)
            cancel_button.clicked.connect(lambda: self.cancel_transfer(worker, row))
            status_layout.addWidget(cancel_button)
            
            self.transfers_table.setCellWidget(row, 6, status_widget)
            
            # Connect worker signals with debugging
            def debug_progress_update(value):
                self.log_message(f"DEBUG UI: Progress bar receiving value: {value}%", "debug")
                progress_bar.setValue(value)
                progress_bar.update()  # Force UI update
                self.transfers_table.update()  # Force table update
            
            def debug_status_update(text):
                self.log_message(f"DEBUG UI: Status receiving text: {text}", "debug")
                status_label.setText(text)
                
                # Update speed column if text contains speed info
                if "⚡" in text or "KB/s" in text or "MB/s" in text or "GB/s" in text:
                    # Extract speed from status text
                    speed_parts = [part.strip() for part in text.split("|") if "⚡" in part or "KB/s" in part or "MB/s" in part or "GB/s" in part]
                    if speed_parts:
                        speed_text = speed_parts[0].replace("⚡", "").strip()
                        speed_item.setText(speed_text)
                
                # Update ETA column if text contains ETA info
                if "⏱️" in text or "ETA" in text:
                    # Extract ETA from status text
                    eta_parts = [part.strip() for part in text.split("|") if "⏱️" in part or "ETA" in part]
                    if eta_parts:
                        eta_text = eta_parts[0].replace("⏱️", "").replace("ETA", "").strip()
                        eta_item.setText(eta_text)
                
                self.transfers_table.update()  # Force table update
            
            # Use QueuedConnection for cross-thread signal handling
            worker.progress.connect(debug_progress_update, Qt.ConnectionType.QueuedConnection)
            worker.status.connect(debug_status_update, Qt.ConnectionType.QueuedConnection)
            worker.error.connect(lambda msg: self.handle_transfer_error(msg, row), Qt.ConnectionType.QueuedConnection)
            worker.finished.connect(lambda success: self.handle_transfer_finished(success, row, worker), Qt.ConnectionType.QueuedConnection)
            worker.debug_log.connect(lambda msg: self.log_message(msg, "debug"), Qt.ConnectionType.QueuedConnection)
            
            # Start the worker
            worker.start()
            
            # Switch to transfers tab to show progress
            self.tabs.setCurrentWidget(self.tabs.widget(4))  # Transfers tab
            
            self.log_message(f"Transfer started: {file_name} ({direction})")
            
        except Exception as e:
            error_msg = f"Failed to start transfer: {str(e)}"
            self.log_message(error_msg, "error")
            QMessageBox.critical(self, "Transfer Error", error_msg)
    
    def cancel_transfer(self, worker, row):
        """Cancel a running transfer"""
        try:
            if worker and worker.isRunning():
                worker.cancel()
                self.log_message(f"Transfer cancelled: Row {row}")
                
                # Update progress bar (Column 3)
                progress_bar = self.transfers_table.cellWidget(row, 3)
                if progress_bar:
                    progress_bar.setFormat("Cancelling - %p%")
                    progress_bar.setStyleSheet("QProgressBar::chunk { background-color: #ffec8b; }")
                
                # Update speed column (Column 4)
                speed_item = self.transfers_table.item(row, 4)
                if speed_item:
                    speed_item.setText("Cancelling...")
                    speed_item.setBackground(QColor(255, 255, 200))  # Light yellow background
                
                # Update status widget (Column 5)
                status_widget = self.transfers_table.cellWidget(row, 5)
                if status_widget:
                    status_label = status_widget.findChild(QLabel)
                    cancel_button = status_widget.findChild(QPushButton)
                    
                    if status_label:
                        status_label.setText("Cancelling...")
                        status_label.setStyleSheet("QLabel { color: #ffec8b; font-weight: bold; }")
                    
                    if cancel_button:
                        cancel_button.setText("Cancelling...")
                        cancel_button.setEnabled(False)
                        cancel_button.setStyleSheet("QPushButton { background-color: #ffec8b; color: black; }")
                
                # Clean up transfer state to prevent UI blocking
                self._cleanup_transfer_state()
                    
        except Exception as e:
            self.log_message(f"Error cancelling transfer: {str(e)}", "error")
    
    def handle_transfer_error(self, error_msg, row):
        """Handle transfer errors with proper logging and user notification"""
        try:
            self.log_message(f"Transfer error (Row {row}): {error_msg}", "error")
            
            # Update progress bar to show failure (Column 3)
            progress_bar = self.transfers_table.cellWidget(row, 3)
            if progress_bar:
                progress_bar.setFormat("Failed - %p%")
                progress_bar.setStyleSheet("QProgressBar::chunk { background-color: #ff6b6b; }")
            
            # Update speed column (Column 4)
            speed_item = self.transfers_table.item(row, 4)
            if speed_item:
                speed_item.setText("Failed")
                speed_item.setBackground(QColor(255, 200, 200))  # Light red background
            
            # Update ETA column (Column 5)
            eta_item = self.transfers_table.item(row, 5)
            if eta_item:
                eta_item.setText("Failed")
                eta_item.setBackground(QColor(255, 200, 200))  # Light red background
            
            # Update status widget (Column 6)
            status_widget = self.transfers_table.cellWidget(row, 6)
            if status_widget:
                # Find the status label and cancel button within the widget
                status_label = status_widget.findChild(QLabel)
                cancel_button = status_widget.findChild(QPushButton)
                
                if status_label:
                    status_label.setText("Failed")
                    status_label.setStyleSheet("QLabel { color: #ff6b6b; font-weight: bold; }")
                
                if cancel_button:
                    cancel_button.setText("Failed")
                    cancel_button.setEnabled(False)
                    cancel_button.setStyleSheet("QPushButton { background-color: #ff6b6b; color: white; }")
            
            # Clean up any lingering transfer state that might block new downloads
            self._cleanup_transfer_state()
            
            # Show detailed error to user
            QMessageBox.critical(self, "Transfer Failed", 
                               f"Transfer failed:\n\n{error_msg}\n\nCheck the logs for more details.")
                               
        except Exception as e:
            self.log_message(f"Error handling transfer error: {str(e)}", "error")
    
    def _cleanup_transfer_state(self):
        """Clean up any lingering transfer state that might interfere with new downloads"""
        try:
            # Clean up any zombie processes or threads
            for worker in self.transfers[:]:
                if worker and not worker.isRunning():
                    self.transfers.remove(worker)
                    if worker:
                        worker.deleteLater()
            
            # Ensure UI is responsive for new actions
            self.repaint()
            QApplication.processEvents()
            
            self.log_message("Transfer state cleanup completed")
            
        except Exception as e:
            self.log_message(f"Error during transfer state cleanup: {str(e)}", "error")
    
    def handle_transfer_finished(self, success, row, worker):
        """Handle transfer completion with proper cleanup and notification"""
        try:
            # Remove worker from active transfers list
            if worker in self.transfers:
                self.transfers.remove(worker)
            
            # Update UI based on success/failure
            progress_bar = self.transfers_table.cellWidget(row, 3)  # Column 3: Progress
            speed_item = self.transfers_table.item(row, 4)  # Column 4: Speed
            eta_item = self.transfers_table.item(row, 5)  # Column 5: ETA
            status_widget = self.transfers_table.cellWidget(row, 6)  # Column 6: Status
            
            if success:
                # Update progress bar to show completion
                if progress_bar:
                    progress_bar.setValue(100)
                    progress_bar.setFormat("Completed - %p%")
                    progress_bar.setStyleSheet("QProgressBar::chunk { background-color: #51cf66; }")
                
                # Update speed column
                if speed_item:
                    speed_item.setText("Completed")
                    speed_item.setBackground(QColor(200, 255, 200))  # Light green background
                
                # Update ETA column
                if eta_item:
                    eta_item.setText("Completed")
                    eta_item.setBackground(QColor(200, 255, 200))  # Light green background
                
                # Update status widget
                if status_widget:
                    status_label = status_widget.findChild(QLabel)
                    cancel_button = status_widget.findChild(QPushButton)
                    
                    if status_label:
                        status_label.setText("Completed")
                        status_label.setStyleSheet("QLabel { color: #51cf66; font-weight: bold; }")
                    
                    if cancel_button:
                        cancel_button.setText("Done")
                        cancel_button.setEnabled(False)
                        cancel_button.setStyleSheet("QPushButton { background-color: #51cf66; color: white; }")
                
                self.log_message(f"Transfer completed successfully: Row {row}")
                
                # Refresh file list if we're in explorer tab
                if self.tabs.currentIndex() == 0:  # Explorer tab
                    self.list_files()
                    
            else:
                # Update progress bar to show failure
                if progress_bar:
                    progress_bar.setFormat("Failed - %p%")
                    progress_bar.setStyleSheet("QProgressBar::chunk { background-color: #ff6b6b; }")
                
                # Update speed column
                if speed_item:
                    speed_item.setText("Failed")
                    speed_item.setBackground(QColor(255, 200, 200))  # Light red background
                
                # Update ETA column
                if eta_item:
                    eta_item.setText("Failed")
                    eta_item.setBackground(QColor(255, 200, 200))  # Light red background
                
                # Update status widget
                if status_widget:
                    status_label = status_widget.findChild(QLabel)
                    cancel_button = status_widget.findChild(QPushButton)
                    
                    if status_label and status_label.text() != "Cancelling...":
                        status_label.setText("Failed")
                        status_label.setStyleSheet("QLabel { color: #ff6b6b; font-weight: bold; }")
                    
                    if cancel_button:
                        cancel_button.setText("Failed")
                        cancel_button.setEnabled(False)
                        cancel_button.setStyleSheet("QPushButton { background-color: #ff6b6b; color: white; }")
            
            # Clean up worker
            if worker:
                worker.deleteLater()
            
            # Ensure UI state is clean for new transfers
            self._cleanup_transfer_state()
                
        except Exception as e:
            self.log_message(f"Error handling transfer completion: {str(e)}", "error")

    def upload_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select File to Upload")
        if file_path:
            dest = f"{self.current_remote}{self.current_path}{os.path.basename(file_path)}"
            self.start_transfer(file_path, dest, "Upload")

    def download_file(self, row, zip_folder=False):
        """Download file or folder with proper validation and error handling"""
        try:
            # Validate row and table data
            if row < 0 or row >= self.file_table.rowCount():
                self.log_message("Invalid file selection for download", "error")
                return
            
            name_item = self.file_table.item(row, 0)
            type_item = self.file_table.item(row, 2)
            
            if not name_item or not type_item:
                self.log_message("Invalid file data for download", "error")
                return
            
            name = name_item.text().strip()
            is_dir = type_item.data(Qt.ItemDataRole.UserRole)
            
            # Validate current remote and path
            if not self.current_remote:
                QMessageBox.warning(self, "No Remote", "Please select a remote first.")
                return
            
            # Construct proper source path with validation
            current_path_clean = self.current_path.strip()
            if current_path_clean and not current_path_clean.endswith('/'):
                current_path_clean += '/'
            
            # Fix path construction - ensure proper colon placement
            remote_clean = self.current_remote.rstrip(':')
            if current_path_clean:
                src = f"{remote_clean}:{current_path_clean}{name}"
            else:
                src = f"{remote_clean}:{name}"
            
            self.log_message(f"Preparing to download: {src}")
            
            if is_dir and not zip_folder:
                # For folders, let the user select a directory
                save_dir = QFileDialog.getExistingDirectory(self, "Select Download Location")
                if not save_dir:
                    return
                
                # Validate and create save directory if needed
                try:
                    os.makedirs(save_dir, exist_ok=True)
                    if not os.access(save_dir, os.W_OK):
                        QMessageBox.warning(self, "Invalid Directory", 
                                          "Selected directory is not writable.")
                        return
                except OSError as e:
                    QMessageBox.warning(self, "Directory Error", 
                                      f"Cannot create or access directory: {str(e)}")
                    return
                
                save_path = os.path.join(save_dir, name)
                self.start_transfer(src, save_path, "Download", is_folder=True)
                
            else:
                # For files or zipped folders, use save dialog
                if is_dir and zip_folder:
                    name += ".zip"
                    
                # Default to Downloads folder if it exists
                default_dir = os.path.expanduser("~/Downloads")
                if not os.path.exists(default_dir):
                    default_dir = os.path.expanduser("~")
                
                default_path = os.path.join(default_dir, name)
                save_path, _ = QFileDialog.getSaveFileName(self, "Save As", default_path)
                if save_path:
                    # Validate and create save directory if needed
                    save_dir = os.path.dirname(save_path)
                    try:
                        os.makedirs(save_dir, exist_ok=True)
                        if not os.access(save_dir, os.W_OK):
                            QMessageBox.warning(self, "Invalid Location", 
                                              "Selected save location is not writable.")
                            return
                    except OSError as e:
                        QMessageBox.warning(self, "Directory Error", 
                                          f"Cannot create or access directory: {str(e)}")
                        return
                    
                    if is_dir and zip_folder:
                        self.download_folder_as_zip(row, save_path)
                    else:
                        self.start_transfer(src, save_path, "Download", is_folder=False)
                        
        except Exception as e:
            error_msg = f"Error preparing download: {str(e)}"
            self.log_message(error_msg, "error")
            QMessageBox.critical(self, "Download Error", error_msg)
    
    def download_folder_as_zip(self, row, zip_path):
        name = self.file_table.item(row, 0).text()
        # Fix path construction - ensure proper colon placement
        remote_clean = self.current_remote.rstrip(':')
        src = f"{remote_clean}:{self.current_path}{name}"
        
        # Create a temporary directory for downloading
        with tempfile.TemporaryDirectory() as temp_dir:
            # Download the folder to temp directory
            temp_dest = os.path.join(temp_dir, name)
            self.start_transfer_and_wait(src, temp_dest, "Downloading")
            
            # Create zip file
            self.status.emit("Creating zip file...")
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, _, files in os.walk(temp_dest):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, temp_dest)
                        zipf.write(file_path, arcname)
            
            self.status.emit("Download complete")
            QMessageBox.information(self, "Success", f"Folder has been zipped and saved to:\n{zip_path}")
    
    def start_transfer_and_wait(self, src, dest, direction):
        """Helper method to run a transfer and wait for it to complete"""
        self.transfer_worker = TransferWorker([self.rclone_path, "copy", src, dest, "--config", self.config_path])
        self.transfer_worker.finished.connect(lambda: self.transfer_worker.quit())
        self.transfer_worker.start()
        self.transfer_worker.wait()

    def show_context_menu(self, position):
        """Show context menu for file/folder operations"""
        row = self.file_table.rowAt(position.y())
        if row < 0:
            return
            
        is_dir = self.file_table.item(row, 2).data(Qt.ItemDataRole.UserRole)
        
        menu = QMenu()
        
        # Always show download option
        download_action = QAction("Download", self)
        download_action.triggered.connect(lambda: self.download_file(row, zip_folder=False))
        menu.addAction(download_action)
        
        # For directories, add zip download option
        if is_dir:
            download_zip_action = QAction("Download as ZIP", self)
            download_zip_action.triggered.connect(lambda: self.download_file(row, zip_folder=True))
            menu.addAction(download_zip_action)
        
        menu.exec(self.file_table.viewport().mapToGlobal(position))

    def _check_network_connectivity(self):
        """Basic network connectivity check"""
        try:
            import socket
            import time
            
            # Try to connect to a reliable server
            start_time = time.time()
            socket.create_connection(("8.8.8.8", 53), timeout=3)
            response_time = (time.time() - start_time) * 1000
            
            if response_time > 2000:  # > 2 seconds
                return True, f"⚠️ Slow network connection ({response_time:.0f}ms). Transfers may take longer."
            else:
                return True, f"✅ Network OK ({response_time:.0f}ms)"
                
        except socket.error:
            return False, "❌ Network connection failed. Check your internet connection."
        except Exception as e:
            return True, f"Could not check network: {str(e)}"

    def _check_disk_space(self, destination_path, estimated_size=None):
        """Check if there's enough disk space for the transfer"""
        try:
            import shutil
            
            # Get destination directory
            if os.path.isfile(destination_path):
                dest_dir = os.path.dirname(destination_path)
            else:
                dest_dir = destination_path
            
            # Get available disk space
            total, used, free = shutil.disk_usage(dest_dir)
            free_gb = free / (1024**3)
            
            # If we have an estimated size, check if there's enough space
            if estimated_size:
                estimated_gb = estimated_size / (1024**3)
                if free < estimated_size * 1.1:  # Add 10% buffer
                    return False, f"Insufficient disk space. Need ~{estimated_gb:.1f}GB, but only {free_gb:.1f}GB available."
            
            # General low space warning
            if free_gb < 1.0:
                return False, f"Very low disk space: only {free_gb:.1f}GB available. Consider freeing up space first."
            
            return True, f"Disk space OK: {free_gb:.1f}GB available"
            
        except Exception as e:
            return True, f"Could not check disk space: {str(e)}"
    
    # Auto-updater methods
    def check_for_updates_background(self):
        """Check for updates in the background"""
        try:
            self.auto_updater = AutoUpdater(self.app_version)
            self.auto_updater.update_available.connect(self.on_update_available)
            self.auto_updater.error_occurred.connect(self.on_update_error)
            self.auto_updater.start()
            
            # Mark that we've checked for updates
            self.update_settings.mark_update_check()
            
        except Exception as e:
            self.log_message(f"Error checking for updates: {str(e)}", "error")
    
    def check_for_updates_manual(self):
        """Manually check for updates (called from menu)"""
        try:
            self.log_message("Checking for updates...", "info")
            self.auto_updater = AutoUpdater(self.app_version)
            self.auto_updater.update_available.connect(self.on_update_available)
            self.auto_updater.error_occurred.connect(self.on_update_error)
            self.auto_updater.update_status.connect(self.on_update_status)
            
            # Show status while checking
            self.auto_updater.finished.connect(self.on_update_check_finished)
            self.auto_updater.start()
            
        except Exception as e:
            self.log_message(f"Error checking for updates: {str(e)}", "error")
            QMessageBox.critical(self, "Update Error", f"Error checking for updates: {str(e)}")
    
    def on_update_available(self, version, changelog):
        """Handle update available notification"""
        try:
            # Don't show if user has skipped this version
            if self.update_settings.is_version_skipped(version):
                self.log_message(f"Update {version} available but skipped by user", "info")
                return
            
            self.log_message(f"Update available: v{version}", "info")
            
            # Show update dialog
            self.update_dialog = UpdateDialog(self, version, changelog)
            self.update_dialog.finished.connect(self.on_update_dialog_finished)
            
            # Connect progress signals
            if self.auto_updater:
                self.auto_updater.update_progress.connect(self.update_dialog.update_progress)
                self.auto_updater.update_status.connect(self.update_dialog.update_status)
                self.auto_updater.update_complete.connect(self.on_update_complete)
                self.auto_updater.error_occurred.connect(self.on_update_error)
            
            result = self.update_dialog.exec()
            
            if result == QDialog.DialogCode.Accepted and self.update_dialog.user_choice == "update":
                # User chose to update
                self.update_dialog.show_progress(True)
                self.update_settings.set("auto_check", self.update_dialog.get_auto_update_preference())
                
                # Start download and installation
                if self.auto_updater:
                    self.auto_updater.download_and_install()
                    
            elif self.update_dialog.user_choice == "skip":
                # User chose to skip this version
                self.update_settings.skip_version(version)
                self.log_message(f"Skipped version {version}", "info")
                
        except Exception as e:
            self.log_message(f"Error handling update notification: {str(e)}", "error")
    
    def on_update_dialog_finished(self, result):
        """Handle update dialog finished"""
        if self.update_dialog:
            # Save auto-update preference
            self.update_settings.set("auto_check", self.update_dialog.get_auto_update_preference())
    
    def on_update_complete(self, success):
        """Handle update completion"""
        try:
            if success:
                self.log_message("Update completed successfully!", "info")
                QMessageBox.information(self, "Update Complete", 
                                      "Update completed successfully!\n\nNGBrowser will now restart to apply the update.")
                self.restart_application()
            else:
                self.log_message("Update failed", "error")
                if self.update_dialog:
                    self.update_dialog.show_progress(False)
                    
        except Exception as e:
            self.log_message(f"Error handling update completion: {str(e)}", "error")
    
    def on_update_error(self, error_message):
        """Handle update error"""
        self.log_message(f"Update error: {error_message}", "error")
        
        if self.update_dialog:
            self.update_dialog.show_progress(False)
            
        # Only show error dialog if it's a manual update check
        if hasattr(self, 'manual_update_check') and self.manual_update_check:
            QMessageBox.critical(self, "Update Error", f"Update failed: {error_message}")
            self.manual_update_check = False
    
    def on_update_status(self, message):
        """Handle update status messages"""
        self.log_message(f"Update: {message}", "info")
    
    def on_update_check_finished(self):
        """Handle update check finished (for manual checks)"""
        if hasattr(self, 'manual_update_check') and self.manual_update_check:
            if not hasattr(self, 'update_found') or not self.update_found:
                QMessageBox.information(self, "No Updates", "You are using the latest version of NGBrowser.")
            self.manual_update_check = False
    
    def restart_application(self):
        """Restart the application"""
        try:
            # Close all transfers first
            self.cancel_all_transfers()
            
            # Get the current executable path
            if getattr(sys, 'frozen', False):
                # Running as compiled executable
                exe_path = sys.executable
            else:
                # Running as script
                exe_path = os.path.join(self.script_dir, "NGBrowser.exe")
                if not os.path.exists(exe_path):
                    exe_path = sys.executable + " " + __file__
            
            # Close current instance
            self.close()
            
            # Start new instance
            if exe_path.endswith(".exe"):
                subprocess.Popen([exe_path], cwd=self.script_dir)
            else:
                subprocess.Popen([sys.executable, __file__], cwd=self.script_dir)
            
            # Exit current process
            sys.exit(0)
            
        except Exception as e:
            self.log_message(f"Error restarting application: {str(e)}", "error")
            QMessageBox.critical(self, "Restart Error", 
                               f"Could not restart application automatically: {str(e)}\n\nPlease restart NGBrowser manually.")
    
    def add_update_menu(self):
        """Add update menu to the application"""
        try:
            # Create menu bar if it doesn't exist
            if not self.menuBar():
                menubar = self.menuBar()
            else:
                menubar = self.menuBar()
            
            # Add Help menu
            help_menu = menubar.addMenu("Help")
            
            # Check for updates action
            check_updates_action = QAction("Check for Updates", self)
            check_updates_action.triggered.connect(self.check_for_updates_manual)
            help_menu.addAction(check_updates_action)
            
            # Update settings action
            update_settings_action = QAction("Update Settings", self)
            update_settings_action.triggered.connect(self.show_update_settings)
            help_menu.addAction(update_settings_action)
            
            help_menu.addSeparator()
            
            # About action
            about_action = QAction("About NGBrowser", self)
            about_action.triggered.connect(self.show_about)
            help_menu.addAction(about_action)
            
        except Exception as e:
            self.log_message(f"Error adding update menu: {str(e)}", "error")
    
    def show_update_settings(self):
        """Show update settings dialog"""
        try:
            dialog = QDialog(self)
            dialog.setWindowTitle("Update Settings")
            dialog.setModal(True)
            dialog.setFixedSize(400, 200)
            
            layout = QVBoxLayout(dialog)
            
            # Auto-check checkbox
            auto_check = QCheckBox("Automatically check for updates on startup")
            auto_check.setChecked(self.update_settings.get("auto_check", True))
            layout.addWidget(auto_check)
            
            # Check interval
            interval_layout = QHBoxLayout()
            interval_layout.addWidget(QLabel("Check every:"))
            interval_spin = QSpinBox()
            interval_spin.setRange(1, 168)  # 1 to 168 hours (1 week)
            interval_spin.setValue(self.update_settings.get("check_interval", 24))
            interval_spin.setSuffix(" hours")
            interval_layout.addWidget(interval_spin)
            interval_layout.addStretch()
            layout.addLayout(interval_layout)
            
            # Current version info
            version_label = QLabel(f"Current Version: {self.app_version}")
            version_label.setStyleSheet("color: #666; margin-top: 10px;")
            layout.addWidget(version_label)
            
            # Buttons
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.update_settings.set("auto_check", auto_check.isChecked())
                self.update_settings.set("check_interval", interval_spin.value())
                self.log_message("Update settings saved", "info")
                
        except Exception as e:
            self.log_message(f"Error showing update settings: {str(e)}", "error")
    
    def show_about(self):
        """Show about dialog"""
        try:
            about_text = f"""<h3>NGBrowser</h3>
            <p><b>Version:</b> {self.app_version}</p>
            <p><b>Description:</b> Advanced Cloud Storage Manager</p>
            <p>A modern GUI for rclone with real-time transfer monitoring,<br>
            comprehensive logging, and automatic updates.</p>
            <p><b>Built with:</b> Python, PyQt6, rclone</p>
            <p><small>© 2024 NGBrowser. All rights reserved.</small></p>
            """
            
            QMessageBox.about(self, "About NGBrowser", about_text)
            
        except Exception as e:
            self.log_message(f"Error showing about dialog: {str(e)}", "error")
    
    def init_quest_functionality(self):
        """Initialize Quest functionality after UI is ready"""
        try:
            # Now that UI is ready, we can safely initialize ADB
            self.adb_path = self.find_adb_path()
            
            # Initial device scan after a short delay
            QTimer.singleShot(1000, self.refresh_quest_devices)
            
        except Exception as e:
            self.log_message(f"Error initializing Quest functionality: {str(e)}", "error")
    
    # Quest VR Sideloading Methods
    def find_adb_path(self):
        """Find ADB executable path"""
        possible_paths = [
            "adb.exe",
            "adb",
            os.path.join(os.path.expanduser("~"), "AppData", "Local", "Android", "Sdk", "platform-tools", "adb.exe"),
            os.path.join("C:", "Users", os.getlogin(), "AppData", "Local", "Android", "Sdk", "platform-tools", "adb.exe"),
            os.path.join(self.script_dir, "adb.exe")
        ]
        
        for path in possible_paths:
            try:
                result = subprocess.run([path, "version"], capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    self.log_message(f"Found ADB at: {path}", "info")
                    return path
            except:
                continue
        
        self.log_message("ADB not found. Please install Android SDK or place adb.exe in the application folder.", "warning")
        return None
    
    def run_adb_command(self, command, timeout=30):
        """Run ADB command and return result"""
        if not self.adb_path:
            return None, "ADB not found"
        
        try:
            full_command = [self.adb_path] + command
            result = subprocess.run(full_command, capture_output=True, text=True, timeout=timeout)
            return result.stdout.strip(), result.stderr.strip()
        except subprocess.TimeoutExpired:
            return None, "Command timed out"
        except Exception as e:
            return None, str(e)
    
    def refresh_quest_devices(self):
        """Refresh and detect Quest devices"""
        self.log_message("Scanning for Quest devices...", "info")
        
        if not self.adb_path:
            self.quest_status_label.setText("❌ ADB not found")
            return
        
        stdout, stderr = self.run_adb_command(["devices"])
        
        if stdout:
            devices = []
            for line in stdout.split('\n')[1:]:  # Skip header line
                if line.strip() and '\t' in line:
                    device_id, status = line.split('\t')
                    if status == 'device':
                        devices.append(device_id)
            
            if devices:
                self.quest_connected = True
                self.connected_quest_device = devices[0]  # Use first device
                self.quest_status_label.setText(f"🟢 Quest Connected: {self.connected_quest_device}")
                self.log_message(f"Quest device connected: {self.connected_quest_device}", "info")
                self.get_device_info()
                self.refresh_installed_apps()
            else:
                self.quest_connected = False
                self.connected_quest_device = None
                self.quest_status_label.setText("🔴 No Quest Connected")
                self.log_message("No Quest devices found", "warning")
        else:
            self.quest_status_label.setText("❌ ADB Error")
            self.log_message(f"ADB error: {stderr}", "error")
    
    def get_device_info(self):
        """Get device information"""
        if not self.quest_connected:
            return
        
        # Get device model
        stdout, _ = self.run_adb_command(["shell", "getprop", "ro.product.model"])
        if stdout:
            self.device_model_label.setText(stdout)
        
        # Get battery info
        stdout, _ = self.run_adb_command(["shell", "dumpsys", "battery"])
        if stdout:
            for line in stdout.split('\n'):
                if 'level:' in line:
                    battery_level = line.split(':')[1].strip()
                    self.device_battery_label.setText(f"{battery_level}%")
                    break
        
        # Get storage info
        stdout, _ = self.run_adb_command(["shell", "df", "/data"])
        if stdout:
            lines = stdout.split('\n')
            if len(lines) > 1:
                parts = lines[1].split()
                if len(parts) >= 4:
                    used_gb = int(parts[2]) / 1024 / 1024
                    total_gb = int(parts[1]) / 1024 / 1024
                    self.device_storage_label.setText(f"{used_gb:.1f}GB / {total_gb:.1f}GB")
        
        # Get Android version
        stdout, _ = self.run_adb_command(["shell", "getprop", "ro.build.version.release"])
        if stdout:
            self.device_version_label.setText(f"Android {stdout}")
    
    def enable_wireless_adb(self):
        """Enable wireless ADB on Quest"""
        if not self.quest_connected:
            QMessageBox.warning(self, "No Device", "Please connect your Quest via USB first.")
            return
        
        self.log_message("Enabling wireless ADB...", "info")
        stdout, stderr = self.run_adb_command(["tcpip", "5555"])
        
        if "restarting in TCP mode" in stdout:
            self.log_message("Wireless ADB enabled. You can now disconnect USB.", "info")
            QMessageBox.information(self, "Wireless ADB", "Wireless ADB enabled successfully!\n\nYou can now disconnect the USB cable and use wireless connection.")
        else:
            self.log_message(f"Failed to enable wireless ADB: {stderr}", "error")
            QMessageBox.warning(self, "Error", f"Failed to enable wireless ADB: {stderr}")
    
    def pair_quest_device(self):
        """Pair Quest device wirelessly"""
        ip_address, ok = QInputDialog.getText(self, "Pair Quest", "Enter Quest IP address:")
        if ok and ip_address:
            self.log_message(f"Attempting to connect to {ip_address}:5555...", "info")
            stdout, stderr = self.run_adb_command(["connect", f"{ip_address}:5555"])
            
            if "connected" in stdout.lower():
                self.log_message(f"Connected to Quest at {ip_address}", "info")
                self.refresh_quest_devices()
            else:
                self.log_message(f"Failed to connect: {stderr}", "error")
                QMessageBox.warning(self, "Connection Failed", f"Failed to connect to Quest: {stderr}")
    
    def refresh_installed_apps(self):
        """Refresh list of installed apps"""
        if not self.quest_connected:
            return
        
        self.log_message("Refreshing installed apps...", "info")
        stdout, stderr = self.run_adb_command(["shell", "pm", "list", "packages", "-3"])
        
        if stdout:
            self.installed_apps_table.setRowCount(0)
            packages = []
            
            for line in stdout.split('\n'):
                if line.startswith('package:'):
                    package_name = line.split(':')[1].strip()
                    packages.append(package_name)
            
            # Get detailed info for each package
            for i, package in enumerate(packages):
                self.installed_apps_table.insertRow(i)
                
                # Get app name
                app_name = self.get_app_name(package)
                self.installed_apps_table.setItem(i, 0, QTableWidgetItem(app_name))
                self.installed_apps_table.setItem(i, 1, QTableWidgetItem(package))
                
                # Get version
                version = self.get_app_version(package)
                self.installed_apps_table.setItem(i, 2, QTableWidgetItem(version))
                
                # Get size (placeholder)
                self.installed_apps_table.setItem(i, 3, QTableWidgetItem("N/A"))
                
                # Add action buttons
                actions_widget = QWidget()
                actions_layout = QHBoxLayout(actions_widget)
                
                backup_btn = QPushButton("💾")
                backup_btn.setToolTip("Backup APK")
                backup_btn.clicked.connect(lambda checked, pkg=package: self.backup_apk(pkg))
                actions_layout.addWidget(backup_btn)
                
                uninstall_btn = QPushButton("🗑️")
                uninstall_btn.setToolTip("Uninstall")
                uninstall_btn.clicked.connect(lambda checked, pkg=package: self.uninstall_app(pkg))
                actions_layout.addWidget(uninstall_btn)
                
                self.installed_apps_table.setCellWidget(i, 4, actions_widget)
        
        self.log_message(f"Found {len(packages)} installed apps", "info")
    
    def get_app_name(self, package_name):
        """Get human-readable app name"""
        stdout, _ = self.run_adb_command(["shell", "pm", "dump", package_name])
        if stdout:
            for line in stdout.split('\n'):
                if 'labelRes=' in line:
                    # Try to get label resource
                    break
        return package_name.split('.')[-1].title()  # Fallback to package name
    
    def get_app_version(self, package_name):
        """Get app version"""
        stdout, _ = self.run_adb_command(["shell", "pm", "dump", package_name])
        if stdout:
            for line in stdout.split('\n'):
                if 'versionName=' in line:
                    return line.split('=')[1].strip()
        return "Unknown"
    
    def install_apk_from_pc(self):
        """Install APK from PC"""
        if not self.quest_connected:
            QMessageBox.warning(self, "No Device", "Please connect your Quest first.")
            return
        
        file_path, _ = QFileDialog.getOpenFileName(self, "Select APK File", "", "APK Files (*.apk)")
        if file_path:
            self.install_apk_file(file_path)
    
    def install_apk_from_cloud(self):
        """Install APK from cloud storage"""
        if not self.quest_connected:
            QMessageBox.warning(self, "No Device", "Please connect your Quest first.")
            return
        
        if not self.current_remote:
            QMessageBox.warning(self, "No Remote", "Please select a cloud storage remote first.")
            return
        
        # Show file dialog to select APK from cloud
        selected_row = self.file_table.currentRow()
        if selected_row >= 0:
            file_name = self.file_table.item(selected_row, 1).text()
            if file_name.lower().endswith('.apk'):
                self.download_and_install_apk(file_name)
            else:
                QMessageBox.warning(self, "Invalid File", "Please select an APK file.")
        else:
            QMessageBox.information(self, "Cloud APK Install", "Please go to the Explorer tab, navigate to your APK files, and select one to install.")
    
    def install_apk_file(self, file_path):
        """Install APK file to Quest"""
        self.log_message(f"Installing APK: {os.path.basename(file_path)}", "info")
        
        # Create progress dialog
        progress = QProgressDialog("Installing APK...", "Cancel", 0, 100, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.show()
        
        # Install APK
        stdout, stderr = self.run_adb_command(["install", file_path], timeout=120)
        
        progress.close()
        
        if "Success" in stdout:
            self.log_message(f"APK installed successfully: {os.path.basename(file_path)}", "info")
            QMessageBox.information(self, "Installation Complete", f"APK installed successfully: {os.path.basename(file_path)}")
            self.refresh_installed_apps()
        else:
            self.log_message(f"APK installation failed: {stderr}", "error")
            QMessageBox.warning(self, "Installation Failed", f"Failed to install APK: {stderr}")
    
    def download_and_install_apk(self, file_name):
        """Download APK from cloud and install"""
        self.log_message(f"Downloading and installing APK: {file_name}", "info")
        
        # Create temporary directory
        temp_dir = tempfile.mkdtemp()
        local_path = os.path.join(temp_dir, file_name)
        
        try:
            # Download APK
            remote_path = f"{self.current_remote.rstrip(':')}/{self.current_path.lstrip('/')}/{file_name}"
            
            # Use existing transfer system
            self.start_transfer(remote_path, local_path, "download")
            
            # Wait for download to complete (simplified)
            QTimer.singleShot(2000, lambda: self.check_and_install_apk(local_path, temp_dir))
            
        except Exception as e:
            self.log_message(f"Error downloading APK: {str(e)}", "error")
            shutil.rmtree(temp_dir, ignore_errors=True)
    
    def check_and_install_apk(self, local_path, temp_dir):
        """Check if APK download is complete and install"""
        try:
            if os.path.exists(local_path):
                self.install_apk_file(local_path)
            else:
                QTimer.singleShot(1000, lambda: self.check_and_install_apk(local_path, temp_dir))
                return
        finally:
            # Clean up temp directory
            QTimer.singleShot(5000, lambda: shutil.rmtree(temp_dir, ignore_errors=True))
    
    def backup_selected_apk(self):
        """Backup selected APK"""
        selected_row = self.installed_apps_table.currentRow()
        if selected_row >= 0:
            package_name = self.installed_apps_table.item(selected_row, 1).text()
            self.backup_apk(package_name)
    
    def backup_apk(self, package_name):
        """Backup APK file"""
        if not self.quest_connected:
            return
        
        # Get APK path
        stdout, _ = self.run_adb_command(["shell", "pm", "path", package_name])
        if stdout and stdout.startswith("package:"):
            apk_path = stdout.split(':')[1].strip()
            
            # Choose backup location
            backup_path, _ = QFileDialog.getSaveFileName(self, "Save APK Backup", f"{package_name}.apk", "APK Files (*.apk)")
            if backup_path:
                self.log_message(f"Backing up APK: {package_name}", "info")
                stdout, stderr = self.run_adb_command(["pull", apk_path, backup_path])
                
                if os.path.exists(backup_path):
                    self.log_message(f"APK backup completed: {backup_path}", "info")
                    QMessageBox.information(self, "Backup Complete", f"APK backup completed: {backup_path}")
                else:
                    self.log_message(f"APK backup failed: {stderr}", "error")
                    QMessageBox.warning(self, "Backup Failed", f"Failed to backup APK: {stderr}")
    
    def uninstall_selected_app(self):
        """Uninstall selected app"""
        selected_row = self.installed_apps_table.currentRow()
        if selected_row >= 0:
            package_name = self.installed_apps_table.item(selected_row, 1).text()
            app_name = self.installed_apps_table.item(selected_row, 0).text()
            
            reply = QMessageBox.question(self, "Uninstall App", f"Are you sure you want to uninstall '{app_name}'?", 
                                       QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                self.uninstall_app(package_name)
    
    def uninstall_app(self, package_name):
        """Uninstall app from Quest"""
        if not self.quest_connected:
            return
        
        self.log_message(f"Uninstalling app: {package_name}", "info")
        stdout, stderr = self.run_adb_command(["uninstall", package_name])
        
        if "Success" in stdout:
            self.log_message(f"App uninstalled successfully: {package_name}", "info")
            QMessageBox.information(self, "Uninstall Complete", f"App uninstalled successfully: {package_name}")
            self.refresh_installed_apps()
        else:
            self.log_message(f"App uninstallation failed: {stderr}", "error")
            QMessageBox.warning(self, "Uninstall Failed", f"Failed to uninstall app: {stderr}")
    
    def push_files_to_quest(self):
        """Push files from PC to Quest"""
        if not self.quest_connected:
            QMessageBox.warning(self, "No Device", "Please connect your Quest first.")
            return
        
        files, _ = QFileDialog.getOpenFileNames(self, "Select Files to Push", "", "All Files (*.*)")
        if files:
            quest_path = "/sdcard/Download/"
            for file_path in files:
                self.log_message(f"Pushing file: {os.path.basename(file_path)}", "info")
                stdout, stderr = self.run_adb_command(["push", file_path, quest_path])
                
                if stderr and "error" not in stderr.lower():
                    self.log_message(f"File pushed successfully: {os.path.basename(file_path)}", "info")
                else:
                    self.log_message(f"Failed to push file: {stderr}", "error")
            
            QMessageBox.information(self, "File Transfer", f"Pushed {len(files)} files to Quest Downloads folder.")
    
    def pull_files_from_quest(self):
        """Pull files from Quest to PC"""
        if not self.quest_connected:
            QMessageBox.warning(self, "No Device", "Please connect your Quest first.")
            return
        
        quest_path = "/sdcard/Download/"
        local_dir = QFileDialog.getExistingDirectory(self, "Select Destination Folder")
        
        if local_dir:
            self.log_message(f"Pulling files from Quest Downloads...", "info")
            stdout, stderr = self.run_adb_command(["pull", quest_path, local_dir])
            
            if stderr and "error" not in stderr.lower():
                self.log_message(f"Files pulled successfully to: {local_dir}", "info")
                QMessageBox.information(self, "File Transfer", f"Files pulled successfully to: {local_dir}")
            else:
                self.log_message(f"Failed to pull files: {stderr}", "error")
                QMessageBox.warning(self, "Transfer Failed", f"Failed to pull files: {stderr}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = RcloneGUI()
    window.show()
    sys.exit(app.exec())