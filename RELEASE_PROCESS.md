# Release Process for NGBrowser

## Overview
This document outlines the process for creating releases that work with the auto-updater functionality.

## Prerequisites
- GitHub repository set up
- Build environment configured
- `rclone.exe` and `rclone.conf` ready

## Release Steps

### 1. Prepare the Release
1. **Update version number** in `rclone_gui.py`:
   ```python
   self.app_version = "1.1.0"  # Update this line
   ```

2. **Test the application** thoroughly:
   ```bash
   python rclone_gui.py
   ```

3. **Build the executable**:
   ```bash
   python build.py
   ```

### 2. Create GitHub Release

1. **Go to your GitHub repository**
2. **Click "Releases"** in the right sidebar
3. **Click "Create a new release"**
4. **Fill in the release information**:
   - **Tag version**: `v1.1.0` (include the 'v' prefix)
   - **Release title**: `NGBrowser v1.1.0`
   - **Description**: Write changelog with new features, fixes, etc.

5. **Upload the executable**:
   - Drag and drop `dist/NGBrowser.exe` into the release assets
   - You can also include `rclone.exe` if needed

6. **Publish the release**

### 3. Auto-Updater Detection

The auto-updater will automatically detect releases that:
- ✅ Have a version tag starting with 'v' (e.g., `v1.1.0`)
- ✅ Include a `.exe` file in the release assets
- ✅ Are marked as "Latest release" (not pre-release)

### 4. Version Numbering

Use semantic versioning (semver):
- **Major**: `2.0.0` - Breaking changes
- **Minor**: `1.1.0` - New features
- **Patch**: `1.0.1` - Bug fixes

## Testing Auto-Updates

### Local Testing
1. **Build a test version** with a higher version number
2. **Create a test release** on GitHub
3. **Run the current version** and check Help → Check for Updates
4. **Verify** the update dialog appears with correct information

### Release Checklist
- [ ] Version number updated in code
- [ ] Application tested thoroughly
- [ ] Executable built successfully
- [ ] GitHub release created with correct tag
- [ ] Release assets uploaded
- [ ] Auto-updater tested
- [ ] Documentation updated

## Troubleshooting

### Auto-Updater Not Working
1. **Check GitHub URL** in `auto_updater.py`
2. **Verify release is public** and not a draft
3. **Check release assets** include `.exe` file
4. **Test internet connection** and GitHub API access

### Build Issues
1. **Ensure all dependencies** are installed
2. **Check `rclone.exe`** is in the project directory
3. **Run build script** with administrator privileges if needed
4. **Clear build cache** by deleting `build/` and `dist/` folders

## Example Changelog Format

```markdown
## What's New in v1.1.0

### ✨ New Features
- Added Google Drive integration
- Improved transfer speed monitoring
- Enhanced error messages

### 🐛 Bug Fixes
- Fixed progress bar not updating
- Resolved download path issues
- Improved stability during large transfers

### 🔧 Improvements
- Better memory usage
- Faster file listing
- Enhanced UI responsiveness

### 📋 Technical
- Updated PyQt6 to latest version
- Improved error handling
- Code optimizations
```

## Support

If you encounter issues with the release process:
1. Check the GitHub Actions logs (if using automated builds)
2. Verify all files are included in the release
3. Test the auto-updater manually
4. Check the application logs for update errors
