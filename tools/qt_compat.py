"""
Qt Compatibility Helper for Qt.py
Handles Qt5 vs Qt6 Enum scoping differences across QtCore, QtWidgets, and QtGui.
"""

from Qt import QtCore, QtWidgets, QtGui

def get_qt_enum(parent_obj, enum_class_name, enum_name):
    """
    Safely retrieves Qt enum across Qt5 (unscoped) and Qt6 (scoped).
    Example: get_qt_enum(QtCore.Qt, "AlignmentFlag", "AlignCenter")
    Example: get_qt_enum(QtWidgets.QHeaderView, "ResizeMode", "Interactive")
    """
    # 1. Try Qt5 style: parent_obj.AlignCenter
    if hasattr(parent_obj, enum_name):
        return getattr(parent_obj, enum_name)
    
    # 2. Try Qt6 style: parent_obj.AlignmentFlag.AlignCenter
    if hasattr(parent_obj, enum_class_name):
        enum_cls = getattr(parent_obj, enum_class_name)
        if hasattr(enum_cls, enum_name):
            return getattr(enum_cls, enum_name)
            
    raise AttributeError(f"Qt Enum '{enum_name}' (class {enum_class_name}) not found on {parent_obj}")

# Pre-resolved Enums
ALIGN_CENTER = get_qt_enum(QtCore.Qt, "AlignmentFlag", "AlignCenter")
ITEM_IS_SELECTABLE = get_qt_enum(QtCore.Qt, "ItemFlag", "ItemIsSelectable")
ITEM_IS_ENABLED = get_qt_enum(QtCore.Qt, "ItemFlag", "ItemIsEnabled")
HEADER_RESIZE_INTERACTIVE = get_qt_enum(QtWidgets.QHeaderView, "ResizeMode", "Interactive")
SELECT_ROWS = get_qt_enum(QtWidgets.QAbstractItemView, "SelectionBehavior", "SelectRows")
ECHO_MODE_PASSWORD = get_qt_enum(QtWidgets.QLineEdit, "EchoMode", "Password")
DIALOG_ACCEPTED = get_qt_enum(QtWidgets.QDialog, "DialogCode", "Accepted")
FONT_BOLD = get_qt_enum(QtGui.QFont, "Weight", "Bold")
ORIENTATION_HORIZONTAL = get_qt_enum(QtCore.Qt, "Orientation", "Horizontal")
QDIR_ALL_DIRS           = get_qt_enum(QtCore.QDir, "Filter", "AllDirs")
QDIR_NO_DOT_AND_DOTDOT  = get_qt_enum(QtCore.QDir, "Filter", "NoDotDot")
QDIR_FILES              = get_qt_enum(QtCore.QDir, "Filter", "Files")
CONTEXT_MENU_CUSTOM     = get_qt_enum(QtCore.Qt, "ContextMenuPolicy", "CustomContextMenu")
NO_DRAG_DROP            = get_qt_enum(QtWidgets.QAbstractItemView, "DragDropMode", "NoDragDrop")
EXTENDED_SELECTION      = get_qt_enum(QtWidgets.QAbstractItemView, "SelectionMode", "ExtendedSelection")
SCROLLBAR_AS_NEEDED     = get_qt_enum(QtCore.Qt, "ScrollBarPolicy", "ScrollBarAsNeeded")
SCROLLBAR_ALWAYS_OFF    = get_qt_enum(QtCore.Qt, "ScrollBarPolicy", "ScrollBarAlwaysOff")
SCROLLBAR_ALWAYS_ON     = get_qt_enum(QtCore.Qt, "ScrollBarPolicy", "ScrollBarAlwaysOn")
TOOLBUTTON_INSTANT_POPUP = get_qt_enum(QtWidgets.QToolButton, "ToolButtonPopupMode", "InstantPopup")
HEADER_RESIZE_STRETCH   = get_qt_enum(QtWidgets.QHeaderView, "ResizeMode", "Stretch")
HEADER_RESIZE_TO_CONTENTS = get_qt_enum(QtWidgets.QHeaderView, "ResizeMode", "ResizeToContents")
TEXT_SELECTABLE_BY_MOUSE = get_qt_enum(QtCore.Qt, "TextInteractionFlag", "TextSelectableByMouse")
CURSOR_POINTING_HAND     = get_qt_enum(QtCore.Qt, "CursorShape", "PointingHandCursor")
PEN_STYLE_NO_PEN         = get_qt_enum(QtCore.Qt, "PenStyle", "NoPen")
