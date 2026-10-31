"""Kato's side of ``lid_awake_core_lib``: one module per capability.

The lib keeps a Mac awake with the lid closed and knows nothing about kato.
These modules supply what only kato knows, each in its own file:

- ``settings.py``    the operator's switch, read fresh
- ``identity.py``    kato's names on the system, and its heartbeat file
- ``agents.py``      whether any of kato's agents is working
- ``status_text.py`` what the UI says, in kato's words
- ``wiring.py``      builds the lib's service from the above
"""
