"""Insights: what you repeat, and what could be automated.

``events`` turns the raw tables into one timeline (focus spans labelled with what
was on screen, browser visits, clipboard copies, prompts typed to AI tools).
``patterns`` mines that timeline for loops, habits, carries and repeated asks and
ranks them as automation candidates. ``steps`` replays a stretch of time as an
ordered list of steps, so a workflow can be captured while you do it.

Everything is computed locally from the Retrace database, read-only.
"""

from .events import Events, load_events
from .patterns import mine
from .steps import steps

__all__ = ["Events", "load_events", "mine", "steps"]
