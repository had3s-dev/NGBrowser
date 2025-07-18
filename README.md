# NGBrowser - Advanced Cloud Storage Manager

<div align="center">
  <img src="https://img.shields.io/badge/Version-1.0.0-blue.svg" alt="Version">
  <img src="https://img.shields.io/badge/Python-3.8+-green.svg" alt="Python">
  <img src="https://img.shields.io/badge/Platform-Windows-lightgrey.svg" alt="Platform">
  <img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License">
</div>

## 🚀 Overview

NGBrowser is a modern, feature-rich GUI for rclone that provides an intuitive interface for managing cloud storage transfers. Built with PyQt6, it offers real-time transfer monitoring, comprehensive logging, and automatic updates.

## ✨ Features

- **🌐 Multi-Cloud Support** - Works with all rclone-supported cloud storage providers
- **📊 Real-Time Monitoring** - Live transfer progress with ETA and speed tracking
- **🔄 Auto-Updates** - Automatic application updates via GitHub releases
- **📝 Comprehensive Logging** - Detailed logs with filtering and export capabilities
- **🎯 Advanced UI** - Modern tabbed interface with dashboard, explorer, and transfer management
- **⚡ Background Processing** - Non-blocking transfers with progress notifications
- **🛡️ Error Recovery** - Robust error handling and retry mechanisms
- **📱 Responsive Design** - Adaptive layout that works on different screen sizes

## 📋 Prerequisites

- **Python 3.8+** (for development)
- **rclone.exe** (included in releases)
- **rclone.conf** (your cloud storage configuration)
- **Windows OS** (primary support)

## 🛠️ Installation

### Option 1: Download Release (Recommended - comes with NG-Games CONF)
1. Go to [Releases](https://github.com/YourUsername/NGBrowser/releases)
2. Download the latest `NGBrowser.exe`
3. Place your `rclone.conf` in the same directory
4. Run `NGBrowser.exe`

### Option 2: Build from Source (BYO CONF)
1. **Clone the repository:**
   ```bash
   git clone https://github.com/YourUsername/NGBrowser.git
   cd NGBrowser
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Place rclone files:**
   - Download `rclone.exe` from [rclone.org](https://rclone.org/downloads/)
   - Place `rclone.exe` and your `rclone.conf` in the project directory

4. **Run the application:**
   ```bash
   python rclone_gui.py
   ```

5. **Build executable (optional):**
   ```bash
   python build.py
   ```

## 🔧 Configuration

### Setting up rclone.conf

1. **Create a new remote:**
   ```bash
   rclone config
   ```

2. **Or use an existing config file:**
   - Place your `rclone.conf` in the same directory as `NGBrowser.exe`
   - Or in `%USERPROFILE%\.config\rclone\rclone.conf`

### Supported Cloud Providers

- **Google Drive** - Full support with OAuth2
- **Dropbox** - Complete integration
- **OneDrive** - Microsoft cloud storage
- **Amazon S3** - AWS cloud storage
- **SFTP/FTP** - File servers
- **And 70+ more** - Full rclone compatibility

## 🎮 Usage

### Main Interface

1. **Dashboard Tab** - System overview and statistics
2. **Explorer Tab** - Browse and navigate cloud storage
3. **Transfers Tab** - Monitor active transfers
4. **Logs Tab** - View detailed operation logs

### Key Operations

- **Browse Files** - Double-click folders to navigate
- **Download Files** - Right-click → Download or use Download button
- **Upload Files** - Use Upload button in Explorer tab
- **Download Folders** - Right-click → Download as ZIP
- **Monitor Progress** - Real-time updates in Transfers tab

### Auto-Updates

- **Automatic Checking** - Checks for updates on startup
- **Manual Updates** - Help → Check for Updates
- **Settings** - Help → Update Settings
- **Version Skipping** - Skip unwanted versions

### Building

```bash
# Install dependencies
pip install -r requirements.txt

# Run development version
python rclone_gui.py

# Build executable
python build.py
```

## 📊 System Requirements

- **OS**: Windows 10/11 (primary), Windows 7/8.1 (limited)
- **RAM**: 512MB minimum, 1GB recommended
- **Storage**: 100MB for application, additional for transfers
- **Network**: Internet connection for cloud operations

## 🐛 Troubleshooting

### Common Issues

**❌ "rclone.exe not found"**
- Ensure `rclone.exe` is in the same directory as `NGBrowser.exe`
- Download from [rclone.org](https://rclone.org/downloads/)

**❌ "No remotes configured"**
- Check your `rclone.conf` file exists and has valid configurations
- Run `rclone config` to set up cloud storage connections

**❌ "Transfer failed"**
- Check internet connection
- Verify cloud storage credentials
- Review logs in the Logs tab

**❌ "Update check failed"**
- Check internet connection
- Verify GitHub repository access
- Disable auto-updates if needed

### Getting Help

1. **Check Logs** - View detailed error messages in Logs tab
2. **Issues** - Report bugs on [GitHub Issues](https://github.com/YourUsername/NGBrowser/issues)
3. **Discussions** - Ask questions in [GitHub Discussions](https://github.com/YourUsername/NGBrowser/discussions)

## 🤝 Contributing

Contributions are welcome! Please feel free to submit pull requests, report bugs, or suggest features.

### Development Setup

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- **rclone** - The powerful command-line program for cloud storage
---

<div align="center">
  <p>Made with ❤️ by the NexusRemains team</p>
</div>
