"""设备层安全约束：INPUT 结构体、SendInput 返回值、DPI 感知、严格前台校验。"""
from __future__ import annotations

import ctypes
import unittest

from host import device


class InputStructTest(unittest.TestCase):
    def test_size_matches_abi(self):
        expected = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
        self.assertEqual(device._INPUT_EXPECTED_SIZE, expected)
        self.assertEqual(ctypes.sizeof(device._INPUT), expected)

    def test_union_covers_mouse_keyboard_hardware(self):
        # 缺 MOUSEINPUT 时 sizeof 会退回 32 并导致 SendInput err 87
        self.assertEqual(ctypes.sizeof(device._INPUT._U),
                         ctypes.sizeof(device._MOUSEINPUT))
        self.assertGreaterEqual(ctypes.sizeof(device._MOUSEINPUT), 24)

    def test_anonymous_union_assignable(self):
        inp = device._INPUT(type=device.INPUT_KEYBOARD)
        inp.ki = device._KEYBDINPUT(wVk=0x52, wScan=0x13, dwFlags=0,
                                    time=0, dwExtraInfo=0)
        self.assertEqual(inp.ki.wScan, 0x13)


class SendInputReturnTest(unittest.TestCase):
    def setUp(self):
        self._orig = device.user32

    def tearDown(self):
        device.user32 = self._orig

    def test_failure_raises(self):
        class Fake:
            def MapVirtualKeyW(self, vk, mode):
                return 0x13

            def SendInput(self, *args):
                return 0

        device.user32 = Fake()
        with self.assertRaises(RuntimeError):
            device._send_vk(0x52, False)

    def test_falls_back_to_vk_then_succeeds(self):
        calls = []

        class Fake:
            def MapVirtualKeyW(self, vk, mode):
                return 0x13

            def SendInput(self, *args):
                calls.append(args)
                return 0 if len(calls) == 1 else 1

        device.user32 = Fake()
        device._send_vk(0x52, False)  # 第一次扫描码失败，退回 VK 成功
        self.assertEqual(len(calls), 2)


class ForegroundGateTest(unittest.TestCase):
    def test_zero_hwnd_refused(self):
        self.assertFalse(device.ensure_foreground(0, retries=2, delay=0))

    def test_success_returns_true(self):
        orig = device.activate_window
        device.activate_window = lambda hwnd: True
        try:
            self.assertTrue(device.ensure_foreground(0x1234, retries=1, delay=0))
        finally:
            device.activate_window = orig


class DpiTest(unittest.TestCase):
    def test_mode_reported(self):
        self.assertIn(device.DPI_MODE,
                      {"per-monitor-v2", "per-monitor", "system", "none"})

    def test_callable(self):
        self.assertIsInstance(device.enable_dpi_awareness(), str)


if __name__ == "__main__":
    unittest.main()
