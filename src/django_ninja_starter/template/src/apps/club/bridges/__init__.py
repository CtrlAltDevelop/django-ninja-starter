"""Event declarations for the apps that ship with this starter.

Each module here registers what one built-in app emits, and each is loaded only
when that app is actually installed -- so a deployment running the club without
the shop registers no shop events, and a mission referring to one is refused at
the point somebody writes it rather than silently never firing.

These are also the worked example for your own apps. A bridge is a module that
calls :func:`apps.club.events.register` at import time and, where the emitting
app sends signals, connects a receiver that calls :func:`apps.club.track`. Name
yours in ``DJANGO_CLUB_EVENT_SOURCES`` and it is loaded exactly the same way.
"""
