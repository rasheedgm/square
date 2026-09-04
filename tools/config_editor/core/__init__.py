"""Headless core for the config editor -- see `editor.ConfigStore`."""

from .editor import ConfigStore, FieldView, NotAuthorized, ADMIN_ROLES

__all__ = ["ConfigStore", "FieldView", "NotAuthorized", "ADMIN_ROLES"]
