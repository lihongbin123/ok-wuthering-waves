"""kl's own PySide6 widget kit.

Built only on the Qt modules PySide6 ships (QtCore, QtGui,
QtWidgets, QtSvg). Call ``theme.install(root)`` on a top-level widget to style it and
its children; plain Qt inputs inside it are styled too.
"""

from kl.ui.qt.kit.buttons import Button
from kl.ui.qt.kit.cards import Card, SettingGroup, SettingRow
from kl.ui.qt.kit.dialogs import Dialog, confirm
from kl.ui.qt.kit.feedback import Pill, ProgressRing, StatusDot, Toast, set_toast_anchor, set_toast_top_margin, show_toast
from kl.ui.qt.kit.icons import ICON_NAMES, icon, load_icon
from kl.ui.qt.kit.inputs import ComboBox, NumberBox
from kl.ui.qt.kit.labels import Label
from kl.ui.qt.kit.layout import FlowLayout
from kl.ui.qt.kit.motion import AnimatedStackedWidget
from kl.ui.qt.kit.navigation import NavigationView, NavItem
from kl.ui.qt.kit.options import OptionCard, OptionGroup
from kl.ui.qt.kit.popover import Popover
from kl.ui.qt.kit.switch import Switch
from kl.ui.qt.kit.table import Table
from kl.ui.qt.kit.tabs import TabItem, TabStrip
from kl.ui.qt.kit.theme import Theme, theme
from kl.ui.qt.kit.window import ContentLayer, KitWindow, TitleBar

__all__ = [
    "AnimatedStackedWidget", "Button", "Card", "ComboBox", "ContentLayer", "Dialog", "FlowLayout", "ICON_NAMES", "KitWindow", "Label",
    "NavItem",
    "NavigationView", "NumberBox", "OptionCard", "OptionGroup", "Pill", "Popover", "ProgressRing", "SettingGroup",
    "SettingRow", "StatusDot", "Switch", "TabItem", "TabStrip", "Table", "Theme", "TitleBar", "Toast", "confirm", "icon",
    "load_icon", "set_toast_anchor", "set_toast_top_margin", "show_toast", "theme",
]
