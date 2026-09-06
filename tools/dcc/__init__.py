"""DCC integrations -- the tool-#3 family.

Each `tools/dcc/<app>/` package runs inside that DCC's own Python. It imports
`square_core` directly and authenticates through the shared
`~/.square/session.json`, like every other tool; the DCC-specific code is only
the menu, the panel, and the node building.
"""
