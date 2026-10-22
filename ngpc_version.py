"""The version this emulator reports, and compares against the latest GitHub release.

"dev" in the repository: a build from source or from build_exe.bat is not a release,
and must never be offered to "update" itself into one. The CI overwrites this file with
the tag it is building (v3.10.0 -> "3.10.0") right before PyInstaller packages it, so
only a released binary knows its number -- see .github/workflows/build.yml.
"""
VERSION = "dev"
