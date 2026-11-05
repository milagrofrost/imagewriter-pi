#!/usr/bin/env python3
"""Tests that need no printer. Run on Linux: python3 tests/test_tools.py

iwprint is pointed at a pseudo-terminal, which stands in for the serial port.
Pseudo-terminals have no CTS line, so the busy-signal path is not covered.
"""
import os, pty, subprocess, sys, tempfile, threading, tty, unittest

BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin")


def tool(name, *args, **kwargs):
    return subprocess.run([sys.executable, os.path.join(BIN, name), *args], capture_output=True, **kwargs)


class IwprintTest(unittest.TestCase):
    def send(self, text, *extra):
        master, slave = pty.openpty()
        tty.setraw(master)
        got = bytearray()

        def reader():
            try:
                while True:
                    chunk = os.read(master, 4096)
                    if not chunk:
                        return
                    got.extend(chunk)
            except OSError:
                pass

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        r = tool("iwprint", "--port", os.ttyname(slave), "--no-flow", "--cps", "100000", "--line-overhead", "0", *extra, input=text)
        os.close(slave)
        t.join(2)
        os.close(master)
        self.assertEqual(r.returncode, 0, r.stderr)
        return bytes(got)

    def test_line_endings_become_crlf(self):
        self.assertEqual(self.send(b"one\ntwo\r\n"), b"one\r\ntwo\r\n")

    def test_blank_lines_kept_and_trailing_spaces_dropped(self):
        self.assertEqual(self.send(b"a  \n\nb\n"), b"a\r\n\r\nb\r\n")

    def test_graphics_rows_use_bit_0_as_top_pin_and_drop_trailing_blanks(self):
        # 3 columns wide, 9 tall: top dot in column 0, then a dot alone on the second row
        rows = [0x80, 0, 0, 0, 0, 0, 0, 0, 0x40]
        pbm = b"P4\n3 9\n" + bytes(rows)
        self.assertEqual(self.send(pbm, "--graphics"),
                         b"\x1bn\x1bT16\x1b>" + b"\x1bG0001\x01\r\n" + b"\x1bG0002\x00\x01\r\n" + b"\x1bN\x1bA\x1b<\r\n")

    def test_blank_graphics_row_only_feeds_paper(self):
        got = self.send(b"P4\n8 8\n" + bytes(8), "--graphics")
        self.assertEqual(got, b"\x1bn\x1bT16\x1b>\r\n\x1bN\x1bA\x1b<\r\n")

    def test_graphics_data_is_not_changed_by_the_port(self):
        # Columns that look like a line feed and an escape must arrive as they are
        columns = bytes([0x0A, 0x1B, 0x0D])
        lines = [sum(((columns[x] >> y) & 1) << (7 - x) for x in range(3)) for y in range(8)]
        got = self.send(b"P4 3 8\n" + bytes(lines), "--graphics")
        self.assertIn(b"\x1bG0003" + columns + b"\r\n", got)

    def test_reset_comes_before_text(self):
        self.assertEqual(self.send(b"x\n", "--reset"), b"\x1bN\x1bA\x1b<x\r\n")

    def test_too_wide_image_rejected_before_printing(self):
        r = tool("iwprint", "--port", "/dev/null", "--graphics", input=b"P4 577 1\n" + bytes(73))
        self.assertIn(b"576", r.stderr)
        self.assertNotEqual(r.returncode, 0)

    def test_short_image_rejected_before_printing(self):
        r = tool("iwprint", "--port", "/dev/null", "--graphics", input=b"P4 8 8\nxx")
        self.assertIn(b"cut short", r.stderr)

    def test_bad_baud_rejected(self):
        r = tool("iwprint", "--port", "/dev/null", "--baud", "1234", input=b"x\n")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(b"Unsupported baud", r.stderr)


class IwsignTest(unittest.TestCase):
    def test_every_shape_is_rectangular(self):
        ns = {"__name__": "iwsign"}
        with open(os.path.join(BIN, "iwsign")) as f:
            exec(f.read(), ns)
        for ch, rows in ns["G"].items():
            self.assertEqual(len(rows), 7, ch)
            self.assertEqual(len({len(r) for r in rows}), 1, ch)

    def test_whole_alphabet_fits_when_split(self):
        for text in ("ABCDEFGHIJ", "KLMNOPQRST", "UVWXYZ0123", "456789!?.,"):
            r = tool("iwsign", text)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertLessEqual(max(map(len, r.stdout.decode().splitlines())), 80)

    def test_too_wide_is_an_error(self):
        r = tool("iwsign", "ABCDEFGHIJKLMNOP")
        self.assertNotEqual(r.returncode, 0)

    def test_unknown_character_is_an_error(self):
        r = tool("iwsign", "A@B")
        self.assertIn(b"@", r.stderr)


if __name__ == "__main__":
    unittest.main()
