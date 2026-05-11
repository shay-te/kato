FROM ghcr.io/openhands/agent-server:1.12.0-python

COPY patches/ /tmp/openhands-patches/

# Patch the installed openhands SDK files using Python's import system
# so the paths don't need to be hardcoded for a specific Python version.
RUN python3 -c "\
import openhands.sdk.git.utils as u; \
import openhands.sdk.git.git_changes as g; \
import shutil; \
shutil.copy('/tmp/openhands-patches/utils.py', u.__file__); \
shutil.copy('/tmp/openhands-patches/git_changes.py', g.__file__)"
