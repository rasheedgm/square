"""
DetailPanel -- the selected row(s) up close, and where conflicts get
resolved.

Single selection: full metadata, the source file list, and one action row
per unresolved issue (Skip / Version Up / Overwrite / Ignore, whichever the
issue offers). Multi selection: a compact batch header plus one action row
per issue KIND that appears anywhere in the selection, applied to the whole
selection at once. Missing colorspace/fps/resolution get inline set fields.
"""

from __future__ import annotations

import os
from Qt import QtCore, QtWidgets

from tools.ingest_tool.core.item import Action, Severity, IssueKind
from tools.qt_compat import SIZE_EXPANDING

_ACTION_LABEL = {
    Action.SKIP: "Skip",
    Action.VERSION_UP: "Version Up",
    Action.OVERWRITE: "Overwrite",
    Action.IGNORE: "Ignore",
}


class DetailPanel(QtWidgets.QScrollArea):
    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self._keys: list[str] = []

        self.setWidgetResizable(True)
        self._body = QtWidgets.QWidget()
        self.setWidget(self._body)
        self._lay = QtWidgets.QVBoxLayout(self._body)
        self._lay.setContentsMargins(10, 8, 10, 8)
        self._lay.setSpacing(6)
        self._lay.addStretch()

        bridge.event.connect(self._on_event)
        self._placeholder("Select a row to see its details and resolve any issues.")

    # ------------------------------------------------------------------

    def set_selection(self, keys) -> None:
        self._keys = list(keys or [])
        self._render()

    def _on_event(self, ev) -> None:
        if ev.kind in ("item_updated", "undo", "preflight_finished", "ingest_finished") and self._keys:
            self._render()

    # ------------------------------------------------------------------

    def _clear(self) -> None:
        while self._lay.count():
            it = self._lay.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()

    def _placeholder(self, text) -> None:
        self._clear()
        lbl = QtWidgets.QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("color:#64748B;")
        self._lay.addWidget(lbl)
        self._lay.addStretch()

    def _h(self, text, size=12, bold=True, colour="#E2E8F0"):
        lbl = QtWidgets.QLabel(text)
        lbl.setStyleSheet(f"color:{colour}; font-size:{size}px; font-weight:{'700' if bold else '400'};")
        lbl.setWordWrap(True)
        self._lay.addWidget(lbl)
        return lbl

    def _items(self):
        out = []
        for k in self._keys:
            it = self.bridge.controller.get(k)
            if it:
                out.append(it)
        return out

    def _render(self) -> None:
        items = self._items()
        if not items:
            self._placeholder("Select a row to see its details and resolve any issues.")
            return
        self._clear()
        if len(items) == 1:
            self._render_single(items[0])
        else:
            self._render_batch(items)
            self._lay.addStretch()

    # ------------------------------------------------------------------
    # Single
    # ------------------------------------------------------------------

    def _render_single(self, it) -> None:
        self._h(f"{it.source_name}", size=13)
        self._h(f"{it.status.value}", size=11, bold=False, colour="#94A3B8")
        self._h(
            f"{it.sequence_code} / {it.shot_code} / {it.media_type} / {it.media_name}   v{it.version:03d}",
            size=11, bold=False, colour="#CBD5E1",
        )
        self._h(f"→ {it.dest_dir}", size=10, bold=False, colour="#64748B")
        self._h(
            f"{it.frame_range_str}    fps {it.fps or '—'} · {it.resolution or '—'} · {it.colorspace or '—'}",
            size=10, bold=False, colour="#94A3B8",
        )
        if it.ledger_detail:
            self._h(f"ⓘ {it.ledger_detail}", size=10, bold=False, colour="#94A3B8")

        self._metadata_setters([it.key], it)

        for iss in it.unresolved_issues:
            self._issue_row(iss, [it.key])

        files = it.source_files
        self._h("Source files", size=10, colour="#64748B")
        box = QtWidgets.QPlainTextEdit()
        box.setReadOnly(True)
        box.setMinimumHeight(70)
        box.setSizePolicy(SIZE_EXPANDING, SIZE_EXPANDING)
        box.setPlainText("\n".join(os.path.basename(f) for f in files[:40])
                         + ("" if len(files) <= 40 else f"\n… {len(files) - 40} more"))
        self._lay.addWidget(box, 1)

    # ------------------------------------------------------------------
    # Batch
    # ------------------------------------------------------------------

    def _render_batch(self, items) -> None:
        self._h(f"{len(items)} rows selected", size=13)
        by_status: dict[str, int] = {}
        for it in items:
            by_status[it.status.value] = by_status.get(it.status.value, 0) + 1
        self._h("  ".join(f"{n}× {s}" for s, n in by_status.items()), size=10,
                bold=False, colour="#94A3B8")

        keys = [it.key for it in items]
        self._metadata_setters(keys, None)

        # one row per issue kind present anywhere in the selection
        kinds: dict = {}
        for it in items:
            for iss in it.unresolved_issues:
                kinds.setdefault(iss.kind, (iss, 0))
                stub, n = kinds[iss.kind]
                kinds[iss.kind] = (stub, n + 1)
        for kind, (stub, n) in kinds.items():
            self._issue_row(stub, keys, batch_count=n)

        row = QtWidgets.QHBoxLayout()
        for label, fn in (("Skip all", lambda: [self.bridge.skip(k) for k in keys]),
                          ("Include all", lambda: [self.bridge.include(k) for k in keys]),
                          ("Re-check", lambda: self.bridge.preflight(keys))):
            b = QtWidgets.QPushButton(label)
            b.clicked.connect(fn)
            row.addWidget(b)
        holder = QtWidgets.QWidget(); holder.setLayout(row)
        self._lay.addWidget(holder)

    # ------------------------------------------------------------------

    def _issue_row(self, iss, keys, batch_count=0) -> None:
        holder = QtWidgets.QFrame()
        holder.setStyleSheet(
            "QFrame{background:#161B26; border:1px solid #232B3B; border-radius:6px;}"
        )
        v = QtWidgets.QVBoxLayout(holder)
        v.setContentsMargins(8, 6, 8, 6)
        mark = "✗" if iss.severity == Severity.BLOCK else "⚠"
        head = f"{mark} {iss.kind.value}" + (f"  ({batch_count} rows)" if batch_count else "")
        t = QtWidgets.QLabel(head)
        t.setStyleSheet(f"color:{'#F87171' if iss.severity == Severity.BLOCK else '#FBBF24'}; font-weight:700;")
        v.addWidget(t)
        msg = QtWidgets.QLabel(iss.message)
        msg.setWordWrap(True)
        msg.setStyleSheet("color:#CBD5E1; font-size:11px;")
        v.addWidget(msg)

        if iss.actions:
            btns = QtWidgets.QHBoxLayout()
            for act in iss.actions:
                b = QtWidgets.QPushButton(_ACTION_LABEL.get(act, act.value))
                b.clicked.connect(lambda _c=False, a=act, k=iss.kind:
                                  self.bridge.resolve_many(keys, k, a))
                btns.addWidget(b)
            btns.addStretch()
            w = QtWidgets.QWidget(); w.setLayout(btns)
            v.addWidget(w)
        self._lay.addWidget(holder)

    def _metadata_setters(self, keys, single_item) -> None:
        need = ("resolution", "fps", "colorspace")
        if single_item is not None:
            missing = [f for f in need
                       if not single_item.metadata_verified.get(f)
                       and not str(getattr(single_item, f) or "").strip()]
        else:
            missing = need   # batch: always offer
        if not missing:
            return
        holder = QtWidgets.QFrame()
        holder.setStyleSheet("QFrame{background:#161B26;border:1px solid #232B3B;border-radius:6px;}")
        g = QtWidgets.QGridLayout(holder)
        g.setContentsMargins(8, 6, 8, 6)
        g.addWidget(QtWidgets.QLabel("Set metadata:"), 0, 0, 1, 3)
        for col, f in enumerate(("resolution", "fps", "colorspace")):
            edit = QtWidgets.QLineEdit()
            edit.setPlaceholderText(f)
            def _apply(_=None, field=f, e=edit):
                val = e.text().strip()
                if not val:
                    return
                for k in keys:
                    try:
                        self.bridge.set_field(k, field, float(val) if field == "fps" else val)
                    except Exception:
                        pass
            edit.editingFinished.connect(_apply)
            g.addWidget(edit, 1, col)
        self._lay.addWidget(holder)
