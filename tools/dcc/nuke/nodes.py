"""SquareRead / SquareWrite -- ordinary Nuke Read / Write nodes, pre-pathed and
coloured from the pipeline. Plain builder functions (not gizmos) so the only
thing to deploy is this package.

`nuke` is passed in rather than imported, so these are unit-testable with a
fake.
"""

from __future__ import annotations


def _fwd(path: str) -> str:
    return str(path).replace("\\", "/")


def _set(node, knob: str, value) -> None:
    try:
        node[knob].setValue(value)
    except Exception:
        pass


def square_read(nuke, *, path: str, colorspace: str = "", frame_in=None,
                frame_out=None, label: str = ""):
    """A Read node on a published plate / element."""
    node = nuke.createNode("Read", inpanel=False)
    _set(node, "file", _fwd(path))
    if frame_in:
        _set(node, "first", int(frame_in))
        _set(node, "origfirst", int(frame_in))
    if frame_out:
        _set(node, "last", int(frame_out))
        _set(node, "origlast", int(frame_out))
    if colorspace:
        _set(node, "colorspace", colorspace)
    _set(node, "label", label or "[Square] {}".format("plate"))
    return node


def square_write(nuke, *, path: str, colorspace: str = "", label: str = ""):
    """A Write node pointed at the next output version's render location."""
    node = nuke.createNode("Write", inpanel=False)
    _set(node, "file", _fwd(path))
    _set(node, "file_type", "exr")
    _set(node, "create_directories", True)
    if colorspace:
        _set(node, "colorspace", colorspace)
    _set(node, "label", label or "[Square] output")
    return node
