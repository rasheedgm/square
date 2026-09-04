"""Qt5 / Qt6 enum-scoping helper for `Qt.py`, shared by every desktop tool.

Qt6 scopes enums (`QtCore.Qt.AlignmentFlag.AlignCenter`); Qt5 does not
(`QtCore.Qt.AlignCenter`). `get_qt_enum` returns whichever exists so tool code
can name one constant.
"""

from Qt import QtCore, QtWidgets, QtGui


def get_qt_enum(parent_obj, enum_class_name, enum_name):
    if hasattr(parent_obj, enum_name):
        return getattr(parent_obj, enum_name)
    if hasattr(parent_obj, enum_class_name):
        enum_cls = getattr(parent_obj, enum_class_name)
        if hasattr(enum_cls, enum_name):
            return getattr(enum_cls, enum_name)
    raise AttributeError(
        f"Qt enum {enum_name!r} (class {enum_class_name}) not found on {parent_obj}")


# alignment / text
ALIGN_CENTER = get_qt_enum(QtCore.Qt, "AlignmentFlag", "AlignCenter")
ALIGN_RIGHT = get_qt_enum(QtCore.Qt, "AlignmentFlag", "AlignRight")
ALIGN_LEFT = get_qt_enum(QtCore.Qt, "AlignmentFlag", "AlignLeft")
ALIGN_TOP = get_qt_enum(QtCore.Qt, "AlignmentFlag", "AlignTop")
TEXT_SELECTABLE_BY_MOUSE = get_qt_enum(QtCore.Qt, "TextInteractionFlag", "TextSelectableByMouse")
ORIENTATION_HORIZONTAL = get_qt_enum(QtCore.Qt, "Orientation", "Horizontal")
ORIENTATION_VERTICAL = get_qt_enum(QtCore.Qt, "Orientation", "Vertical")
CURSOR_POINTING_HAND = get_qt_enum(QtCore.Qt, "CursorShape", "PointingHandCursor")
CONTEXT_MENU_CUSTOM = get_qt_enum(QtCore.Qt, "ContextMenuPolicy", "CustomContextMenu")
SCROLLBAR_AS_NEEDED = get_qt_enum(QtCore.Qt, "ScrollBarPolicy", "ScrollBarAsNeeded")
SCROLLBAR_ALWAYS_OFF = get_qt_enum(QtCore.Qt, "ScrollBarPolicy", "ScrollBarAlwaysOff")
SCROLLBAR_ALWAYS_ON = get_qt_enum(QtCore.Qt, "ScrollBarPolicy", "ScrollBarAlwaysOn")

# item views / headers
ITEM_IS_SELECTABLE = get_qt_enum(QtCore.Qt, "ItemFlag", "ItemIsSelectable")
ITEM_IS_ENABLED = get_qt_enum(QtCore.Qt, "ItemFlag", "ItemIsEnabled")
ITEM_IS_EDITABLE = get_qt_enum(QtCore.Qt, "ItemFlag", "ItemIsEditable")
SELECT_ROWS = get_qt_enum(QtWidgets.QAbstractItemView, "SelectionBehavior", "SelectRows")
SINGLE_SELECTION = get_qt_enum(QtWidgets.QAbstractItemView, "SelectionMode", "SingleSelection")
EXTENDED_SELECTION = get_qt_enum(QtWidgets.QAbstractItemView, "SelectionMode", "ExtendedSelection")
NO_DRAG_DROP = get_qt_enum(QtWidgets.QAbstractItemView, "DragDropMode", "NoDragDrop")
SELECTION_SELECT = get_qt_enum(QtCore.QItemSelectionModel, "SelectionFlag", "Select")
SELECTION_ROWS = get_qt_enum(QtCore.QItemSelectionModel, "SelectionFlag", "Rows")
HEADER_RESIZE_STRETCH = get_qt_enum(QtWidgets.QHeaderView, "ResizeMode", "Stretch")
HEADER_RESIZE_TO_CONTENTS = get_qt_enum(QtWidgets.QHeaderView, "ResizeMode", "ResizeToContents")
HEADER_RESIZE_INTERACTIVE = get_qt_enum(QtWidgets.QHeaderView, "ResizeMode", "Interactive")
HEADER_RESIZE_FIXED = get_qt_enum(QtWidgets.QHeaderView, "ResizeMode", "Fixed")
TOOLBUTTON_INSTANT_POPUP = get_qt_enum(QtWidgets.QToolButton, "ToolButtonPopupMode", "InstantPopup")
FRAME_NO_FRAME = get_qt_enum(QtWidgets.QFrame, "Shape", "NoFrame")

# widgets
ECHO_MODE_PASSWORD = get_qt_enum(QtWidgets.QLineEdit, "EchoMode", "Password")
DIALOG_ACCEPTED = get_qt_enum(QtWidgets.QDialog, "DialogCode", "Accepted")
DIALOG_OK = get_qt_enum(QtWidgets.QDialogButtonBox, "StandardButton", "Ok")
DIALOG_CANCEL = get_qt_enum(QtWidgets.QDialogButtonBox, "StandardButton", "Cancel")
DIALOG_SAVE = get_qt_enum(QtWidgets.QDialogButtonBox, "StandardButton", "Save")
DIALOG_CLOSE = get_qt_enum(QtWidgets.QDialogButtonBox, "StandardButton", "Close")
MSG_WARNING = get_qt_enum(QtWidgets.QMessageBox, "Icon", "Warning")

# message box / layout / size policy / filesystem model
MSGBOX_YES = get_qt_enum(QtWidgets.QMessageBox, "StandardButton", "Yes")
MSGBOX_NO = get_qt_enum(QtWidgets.QMessageBox, "StandardButton", "No")
FORM_FIELDS_GROW = get_qt_enum(QtWidgets.QFormLayout, "FieldGrowthPolicy",
                               "AllNonFixedFieldsGrow")
SIZE_EXPANDING = get_qt_enum(QtWidgets.QSizePolicy, "Policy", "Expanding")
SIZE_PREFERRED = get_qt_enum(QtWidgets.QSizePolicy, "Policy", "Preferred")
QDIR_ALL_DIRS = get_qt_enum(QtCore.QDir, "Filter", "AllDirs")
QDIR_NO_DOT_AND_DOTDOT = get_qt_enum(QtCore.QDir, "Filter", "NoDotDot")
QDIR_FILES = get_qt_enum(QtCore.QDir, "Filter", "Files")

# gui / drawing
FONT_BOLD = get_qt_enum(QtGui.QFont, "Weight", "Bold")
PEN_STYLE_NO_PEN = get_qt_enum(QtCore.Qt, "PenStyle", "NoPen")


def exec_dialog(dlg):
    """Qt6 `exec()` / Qt5 `exec_()`."""
    return dlg.exec() if hasattr(dlg, "exec") else dlg.exec_()
