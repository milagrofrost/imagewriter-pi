#!/usr/bin/env python3
"""Backend tests that need no printer, API key or network.

Run on the print server: /opt/imagewriter/venv/bin/python tests/test_backend.py
ImageWriter-AI is pointed at a local stand-in for the Claude API.
"""
import base64, http.server, io, json, os, subprocess, sys, tempfile, threading, unittest

from PIL import Image

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "printserver", "imagewriter-backend")


def sse(reply, stop_reason):
    events = [
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_test", "type": "message", "role": "assistant", "model": "test", "content": [],
            "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 7, "output_tokens": 1}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": reply}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None}, "usage": {"output_tokens": 9}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join("event: %s\ndata: %s\n\n" % (n, json.dumps(d)) for n, d in events).encode()


class FakeClaude(http.server.BaseHTTPRequestHandler):
    reply = "HELLO\n"
    stop_reason = "end_turn"
    requests = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeClaude.requests.append((dict(self.headers), body))
        payload = sse(FakeClaude.reply, FakeClaude.stop_reason)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeClaude)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        FakeClaude.reply, FakeClaude.stop_reason = "HELLO\n", "end_turn"
        FakeClaude.requests = []

    def render(self, mode, data, key="test-key", **env):
        with tempfile.NamedTemporaryFile() as f:
            f.write(data)
            f.flush()
            e = dict(os.environ, ANTHROPIC_BASE_URL="http://127.0.0.1:%d" % self.server.server_address[1], **env)
            e.pop("ANTHROPIC_API_KEY", None)
            e.pop("ANTHROPIC_AUTH_TOKEN", None)
            if key:
                e["ANTHROPIC_API_KEY"] = key
            return subprocess.run([sys.executable, BACKEND, "--render", mode, f.name], capture_output=True, env=e)


def make_pdf(text):
    return subprocess.run(["gs", "-q", "-dSAFER", "-dBATCH", "-dNOPAUSE", "-sDEVICE=pdfwrite", "-sOutputFile=-", "-"],
                          input=("%%!PS\n/Courier findfont 12 scalefont setfont 72 700 moveto (%s) show showpage\n" % text).encode(),
                          capture_output=True, check=True).stdout


class TextQueue(Base):
    def test_plain_text_passes_through(self):
        r = self.render("text", b"  keep   my spacing\n\nsecond\n")
        self.assertEqual(r.stdout, b"  keep   my spacing\n\nsecond\n")

    def test_control_codes_cannot_reach_the_printer(self):
        r = self.render("text", b"a\x1b!b\x07c\r\n")
        self.assertEqual(r.stdout, b"a!bc\n")

    def test_unicode_becomes_ascii(self):
        r = self.render("text", "café “ok” — 日\n".encode())
        self.assertEqual(r.stdout, b'cafe "ok" -- ?\n')

    def test_long_lines_wrap_at_80(self):
        r = self.render("text", b"x" * 100 + b"\n")
        self.assertEqual(r.stdout, b"x" * 80 + b"\n" + b"x" * 20 + b"\n")

    def test_page_limit_stops_runaway_jobs(self):
        r = self.render("text", b"line\n" * 500, IMAGEWRITER_MAX_PAGES="1")
        lines = r.stdout.decode().splitlines()
        self.assertEqual(len(lines), 66)
        self.assertIn("1-page limit", lines[-1])

    def test_pdf_is_converted_to_text(self):
        r = self.render("text", make_pdf("Hello PDF"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(b"Hello PDF", r.stdout)

    def test_postscript_is_converted_to_text(self):
        r = self.render("text", b"%!PS\n/Courier findfont 12 scalefont setfont 72 700 moveto (Hello PS) show showpage\n")
        self.assertIn(b"Hello PS", r.stdout)

    def test_image_is_rejected_with_advice(self):
        buf = io.BytesIO()
        Image.new("RGB", (4, 4)).save(buf, "PNG")
        r = self.render("text", buf.getvalue())
        self.assertEqual(r.returncode, 5)
        self.assertIn(b"ImageWriter-AI", r.stderr)
        self.assertEqual(r.stdout, b"")

    def test_unknown_binary_is_rejected_not_printed(self):
        r = self.render("text", b"\x1b%-12345X@PJL\x00\x01\x02" * 50)
        self.assertEqual(r.returncode, 5)
        self.assertEqual(r.stdout, b"")


class AiQueue(Base):
    def test_request_shape_and_reply(self):
        r = self.render("ai", make_pdf("Hello"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, b"HELLO\n")
        headers, body = FakeClaude.requests[0]
        self.assertEqual(body["model"], "claude-opus-5-5")
        self.assertEqual(body["fallbacks"], "default")
        self.assertIn("server-side-fallback-2026-07-01", headers["anthropic-beta"])
        self.assertTrue(body["stream"])
        self.assertEqual(body["messages"][0]["content"][0]["type"], "document")

    def test_postscript_status_messages_stay_out_of_the_pdf(self):
        job = b"%!PS\n(driver status) print flush\n/Courier findfont 12 scalefont setfont 72 700 moveto (Hi) show showpage\n"
        r = self.render("ai", job)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = FakeClaude.requests[0][1]["messages"][0]["content"][0]["source"]["data"]
        self.assertTrue(base64.b64decode(data).startswith(b"%PDF-"))

    def test_large_image_is_shrunk_to_jpeg(self):
        buf = io.BytesIO()
        Image.new("RGB", (4000, 3000), "white").save(buf, "PNG")
        r = self.render("ai", buf.getvalue())
        self.assertEqual(r.returncode, 0, r.stderr)
        source = FakeClaude.requests[0][1]["messages"][0]["content"][0]["source"]
        self.assertEqual(source["media_type"], "image/jpeg")

    def test_reply_is_forced_onto_the_paper(self):
        FakeClaude.reply = "“wide” " + "y" * 90 + "\x1b\n"
        r = self.render("ai", b"hello\n")
        self.assertTrue(all(len(l) <= 80 for l in r.stdout.decode().splitlines()))
        self.assertNotIn(b"\x1b", r.stdout)

    def test_code_fences_are_removed(self):
        FakeClaude.reply = "```text\n  /##\n```\ncaption\n"
        r = self.render("ai", b"hello\n")
        self.assertEqual(r.stdout, b"  /##\ncaption\n")

    def test_long_reply_lines_wrap_between_words(self):
        FakeClaude.reply = "word " * 30 + "\n"
        r = self.render("ai", b"hello\n")
        lines = r.stdout.decode().splitlines()
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(set(l.split()) == {"word"} and len(l) <= 80 for l in lines))

    def test_refusal_prints_nothing(self):
        FakeClaude.stop_reason = "refusal"
        r = self.render("ai", b"hello\n")
        self.assertEqual(r.returncode, 5)
        self.assertEqual(r.stdout, b"")
        self.assertIn(b"declined", r.stderr)

    def test_truncated_reply_prints_nothing(self):
        FakeClaude.stop_reason = "max_tokens"
        r = self.render("ai", b"hello\n")
        self.assertEqual(r.returncode, 5)
        self.assertEqual(r.stdout, b"")

    def test_missing_key_is_explained_without_a_request(self):
        r = self.render("ai", b"hello\n", key=None)
        self.assertEqual(r.returncode, 5)
        self.assertIn(b"API key", r.stderr)
        self.assertEqual(FakeClaude.requests, [])

    def test_input_page_limit_stops_costly_jobs(self):
        r = self.render("ai", make_pdf("Hello"), IMAGEWRITER_MAX_INPUT_PAGES="0")
        self.assertEqual(r.returncode, 5)
        self.assertEqual(FakeClaude.requests, [])


class GraphicsQueue(Base):
    def pbm(self, data, **env):
        r = self.render("graphics", data, **env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return Image.open(io.BytesIO(r.stdout)), r

    def png(self, im):
        buf = io.BytesIO()
        im.save(buf, "PNG")
        return buf.getvalue()

    def test_image_scales_to_the_width_of_the_paper_in_whole_rows(self):
        from PIL import ImageDraw
        im = Image.new("L", (100, 50), 255)
        ImageDraw.Draw(im).rectangle((0, 0, 99, 49), outline=0, width=2)
        out, _ = self.pbm(self.png(im))
        self.assertEqual(out.mode, "1")
        self.assertEqual(out.width, 576)
        self.assertEqual(out.height % 8, 0)

    def test_tall_image_fits_one_page(self):
        out, _ = self.pbm(self.png(Image.new("L", (100, 2000), 0)))
        self.assertLessEqual(out.height, 792)

    def test_line_art_has_no_dither(self):
        im = Image.new("L", (576, 80), 255)
        im.paste(0, (0, 0, 288, 80))
        out, _ = self.pbm(self.png(im))
        self.assertEqual(out.getpixel((100, 40)), 0)
        self.assertEqual(out.getpixel((400, 40)), 255)
        left = [out.getpixel((x, 40)) for x in range(280)]
        self.assertEqual(set(left), {0})

    def test_shading_is_dithered(self):
        im = Image.new("L", (576, 80))
        for x in range(576):
            for y in range(80):
                im.putpixel((x, y), x * 255 // 575)
        out, _ = self.pbm(self.png(im))
        middle = [out.getpixel((x, 40)) for x in range(250, 330)]
        self.assertEqual(set(middle), {0, 255})

    def test_transparent_background_prints_as_paper(self):
        im = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        im.paste((0, 0, 0, 255), (40, 40, 60, 60))
        out, _ = self.pbm(self.png(im))
        self.assertEqual(out.getpixel((5, 5)), 255)

    def test_pdf_pages_fill_all_but_the_last_sheet(self):
        out = subprocess.run(["gs", "-q", "-dSAFER", "-dBATCH", "-dNOPAUSE", "-sstdout=%stderr", "-sDEVICE=pdfwrite", "-sOutputFile=-", "-"],
                             input=b"%!PS\n/Courier findfont 40 scalefont setfont 72 700 moveto (One) show showpage 72 700 moveto (Two) show showpage\n",
                             capture_output=True, check=True).stdout
        r = self.render("graphics", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.count(b"P4\n"), 2)
        first = Image.open(io.BytesIO(r.stdout))
        self.assertEqual(first.height, 792)

    def test_page_limit_applies(self):
        out = subprocess.run(["gs", "-q", "-dSAFER", "-dBATCH", "-dNOPAUSE", "-sstdout=%stderr", "-sDEVICE=pdfwrite", "-sOutputFile=-", "-"],
                             input=b"%!PS\n/Courier findfont 40 scalefont setfont 72 700 moveto (One) show showpage 72 700 moveto (Two) show showpage\n",
                             capture_output=True, check=True).stdout
        r = self.render("graphics", out, IMAGEWRITER_MAX_PAGES="1")
        self.assertEqual(r.stdout.count(b"P4\n"), 1)
        self.assertIn(b"first 1 of 2", r.stderr)

    def test_blank_job_prints_nothing(self):
        r = self.render("graphics", self.png(Image.new("L", (50, 50), 255)))
        self.assertEqual(r.returncode, 5)
        self.assertEqual(r.stdout, b"")

    def test_text_is_rejected_with_advice(self):
        r = self.render("graphics", b"hello\n")
        self.assertEqual(r.returncode, 5)
        self.assertIn(b"ImageWriter-Text", r.stderr)

    def test_damaged_image_is_rejected(self):
        r = self.render("graphics", b"\x89PNG\r\n\x1a\n" + b"junk" * 20)
        self.assertEqual(r.returncode, 5)
        self.assertEqual(r.stdout, b"")

    def test_no_request_reaches_claude(self):
        self.pbm(self.png(Image.new("L", (50, 50), 0)), key=None)
        self.assertEqual(FakeClaude.requests, [])


if __name__ == "__main__":
    unittest.main()
