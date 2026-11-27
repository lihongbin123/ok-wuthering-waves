import pydirectinput

from kl.device.capture_methods.base import BaseCaptureMethod
from kl.device.interaction_methods.genshin import GenshinInteraction
from kl.util.logger import Logger
from kl.util.process import is_admin

logger = Logger.get_logger(__name__)

class ForegroundPostMessageInteraction(GenshinInteraction):
    def __init__(self, capture: BaseCaptureMethod, hwnd_window):
        super().__init__(capture, hwnd_window)
        pydirectinput.FAILSAFE = False
        self.check_clickable = True
        if not is_admin():
            logger.error(f"You must be an admin to use Win32Interaction")

    def clickable(self):
        if self.check_clickable:
            return self.hwnd_window.is_foreground()
        else:
            return True

    def should_capture(self):
        return self.clickable()

    def on_run(self):
        self.hwnd_window.bring_to_front()
