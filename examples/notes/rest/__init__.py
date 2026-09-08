"""Versioned HTTP routers for the notes app.

One module per API version, and the registry names the version it wants. The
infrastructure apps export a single `router` here instead, because none of them
is versioned.
"""
