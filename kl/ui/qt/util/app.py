import os
import sys

from PySide6.QtCore import Qt, QTranslator, QCoreApplication
from PySide6.QtWidgets import QApplication

from kl import Logger
from kl.ui.qt import resources
from kl.ui.qt.common.config import cfg

logger = Logger.get_logger(__name__)

_translator = None



def _translation_dirs():
    """优先加载源码目录里的 .qm，避免 resources.py 里的旧副本静默失败。"""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), "i18n"), ":/i18n"


def install_translator(app):
    """Replace the installed Qt translator with one for ``cfg.language``; returns the locale."""
    global _translator
    if _translator is not None:
        app.removeTranslator(_translator)
        _translator.deleteLater()
        _translator = None
    locale = cfg.language.value
    translator = QTranslator(app)
    loaded_from = None
    for source in _translation_dirs():
        if translator.load(locale.name(), source):
            loaded_from = source
            break
    if loaded_from is not None:
        app.installTranslator(translator)
        _translator = translator
        logger.info(f"translator install success {locale} from {loaded_from}, "
                    f"{QCoreApplication.translate('MainWindow', 'Debug')}")
    else:
        translator.deleteLater()
        logger.info(f"No translation available for {locale}, falling back to English/default.")
    return locale


def init_app_config():
    logger.debug(f'resources.qt_resource_name {resources.qt_resource_name} theme {cfg.theme_mode}')

    app = QApplication(sys.argv)
    app.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app.setAttribute(Qt.AA_EnableHighDpiScaling)
    app.setAttribute(Qt.AA_UseHighDpiPixmaps)
    app.setAttribute(Qt.AA_DontCreateNativeWidgetSiblings)

    locale = install_translator(app)

    from kl.ui.qt.kit import Theme, theme
    theme.set_theme(Theme(cfg.theme_mode))
    theme.set_backdrop(cfg.backdrop)
    return app, locale


def show_info_bar(window, message, title=None, error=False):
    """Show a toast in ``window``; errors stay a little longer."""
    from kl.ui.qt.kit.feedback import show_toast
    if title is not None:
        title = QCoreApplication.translate('app', title)
    message = QCoreApplication.translate('app', message)
    return show_toast(window, message, title, tone="danger" if error else "neutral",
                      duration=8000 if error else 5000)


def get_localized_app_config(config, key):
    locale = cfg.language.value.name()

    if config.get(locale):
        config_dict = config.get(locale)
    else:
        config_dict = config.get('default')
    if config_dict:
        return config_dict.get(key)
