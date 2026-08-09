"""Warnings and errors this producer raises.

Kept in one module, mirroring egm-signal's ``exceptions``, so a caller can
import and filter them without reaching into an orchestrator.
"""

from __future__ import annotations


class EmptyBankWarning(UserWarning):
    """No traces survived, so no bank was written.

    A bank with an empty ``traces`` group is a valid document and a useless
    artifact: nothing downstream can train on, evaluate, or plot it, and its
    presence on disk reads as a successful run. Writing one converts "the
    settings produced nothing" into a file someone discovers is empty much
    later — so the run warns and writes nothing instead.

    On the activation path this is a live outcome rather than a corner case.
    Every IAFDB patient is arrhythmic, so if the detection settings fail to
    suppress fibrillatory side-peaks, nearly every window holds a neighbour
    and is dropped. Its own category so that case can be caught
    programmatically while tuning.
    """


__all__ = ["EmptyBankWarning"]
