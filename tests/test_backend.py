#!/usr/bin/env python3
"""Backend tests that need no printer, API key or network.

Run on the print server: /opt/imagewriter/venv/bin/python tests/test_backend.py
ImageWriter-AI is pointed at a local stand-in for the Claude API.
"""
import base64, http.server, io, json, os, subprocess, sys, tempfile, threading, unittest

from PIL import Image

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "printserver", "imagewriter-backend")


def sse(reply, stop_reason):
    """A streamed reply: text, or {"tool_use": input} for a measure_text call."""
    if isinstance(reply, dict):
        block = {"type": "tool_use", "id": "toolu_test", "name": "measure_text", "input": {}}
        delta = {"type": "input_json_delta", "partial_json": json.dumps(reply["tool_use"])}
        stop_reason = "tool_use"
    else:
        block, delta = {"type": "text", "text": ""}, {"type": "text_delta", "text": reply}
    events = [
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_test", "type": "message", "role": "assistant", "model": "test", "content": [],
            "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 7, "output_tokens": 1}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": block}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": delta}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None}, "usage": {"output_tokens": 9}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join("event: %s\ndata: %s\n\n" % (n, json.dumps(d)) for n, d in events).encode()


class FakeClaude(http.server.BaseHTTPRequestHandler):
    reply = "HELLO\n"
    script = []  # replies for successive requests; once used up, every request gets reply
    stop_reason = "end_turn"
    requests = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeClaude.requests.append((dict(self.headers), body))
        reply = FakeClaude.script.pop(0) if FakeClaude.script else FakeClaude.reply
        payload = sse(reply, FakeClaude.stop_reason)
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
        FakeClaude.requests, FakeClaude.script = [], []

    def backend(self, args, data, key="test-key", **env):
        """Run the backend with the job saved to a file, which replaces "JOB" in args."""
        with tempfile.NamedTemporaryFile() as f:
            f.write(data)
            f.flush()
            e = dict(os.environ, ANTHROPIC_BASE_URL="http://127.0.0.1:%d" % self.server.server_address[1], **env)
            e.pop("ANTHROPIC_API_KEY", None)
            e.pop("ANTHROPIC_AUTH_TOKEN", None)
            if key:
                e["ANTHROPIC_API_KEY"] = key
            args = [f.name if a == "JOB" else a for a in args]
            return subprocess.run([sys.executable, BACKEND, *args], capture_output=True, env=e)

    def render(self, mode, data, **kwargs):
        return self.backend(["--render", mode, "JOB"], data, **kwargs)


def png_bytes(im):
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


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


class Preview(Base):
    # At 216 pixels per inch: a 0.5-inch border, a 1-inch heading, then 9.5 x 11-inch sheets
    DPI = 216
    SHEET_WIDTH, SHEET_HEIGHT = 2052, 2376
    TOP = DPI
    PRINT_LEFT = DPI // 2 + DPI * 3 // 4  # the printer's first column, 0.75 inch into the sheet

    def preview(self, mode, data, **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "preview.png")
            r = self.backend(["--preview", mode, "JOB", out], data, **kwargs)
            self.assertEqual(r.returncode, 0, r.stderr)
            with Image.open(out) as im:
                return im.convert("L")

    def darkest(self, im, left, top, width, height):
        return im.crop((left, top, left + width, top + height)).getextrema()[0]

    def test_text_is_drawn_in_dots_where_the_printer_puts_it(self):
        im = self.preview("text", b"XXXX\n")
        self.assertEqual(im.size, (self.DPI + self.SHEET_WIDTH, self.TOP + self.SHEET_HEIGHT + self.DPI // 2))
        self.assertLess(self.darkest(im, self.PRINT_LEFT, self.TOP, self.DPI * 4 // 10, self.DPI // 6), 80)
        # Nothing after the fourth character: ten characters per inch
        self.assertGreater(self.darkest(im, self.PRINT_LEFT + self.DPI // 2, self.TOP, self.DPI, self.DPI // 6), 200)

    def test_each_66_lines_starts_a_new_sheet(self):
        im = self.preview("text", b"line\n" * 70)
        self.assertEqual(im.height, self.TOP + 2 * self.SHEET_HEIGHT + self.DPI // 2)

    def test_image_is_drawn_as_dots(self):
        im = self.preview("graphics", png_bytes(Image.new("L", (100, 100), 0)))
        self.assertLess(self.darkest(im, self.PRINT_LEFT, self.TOP, self.DPI, self.DPI), 80)

    def test_refused_job_shows_blank_paper_without_failing(self):
        im = self.preview("graphics", b"hello\n")
        # The reason is printed in grey on the sheet, never as dark ink
        self.assertGreater(self.darkest(im, self.PRINT_LEFT, self.TOP, 8 * self.DPI, 11 * self.DPI), 100)

    def test_all_shows_the_three_queues_side_by_side(self):
        im = self.preview("all", b"hello\n")
        self.assertEqual(im.width, self.DPI // 2 + 3 * (self.SHEET_WIDTH + self.DPI // 2))
        self.assertEqual(len(FakeClaude.requests), 1)

    def test_text_and_graphics_previews_never_reach_claude(self):
        self.preview("text", b"hello\n", key=None)
        self.preview("graphics", png_bytes(Image.new("L", (50, 50), 0)), key=None)
        self.assertEqual(FakeClaude.requests, [])

    def test_unknown_queue_is_a_usage_error(self):
        r = self.backend(["--preview", "colour", "JOB", "/dev/null"], b"hello\n")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(b"usage", r.stderr)


def design_svg(length=1440, extra=""):
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d 576">'
            '<rect x="20" y="20" width="%d" height="536" fill="none" stroke="black" stroke-width="12"/>'
            '<text x="%d" y="380" font-family="Nimbus Sans" font-size="300" text-anchor="middle">HI</text>%s</svg>'
            % (length, length - 40, length // 2, extra))


def proposal(*svgs):
    return json.dumps({"action": "propose", "proof": "", "option": "",
                       "designs": [{"title": "Design %d" % i, "svg": svg} for i, svg in enumerate(svgs)]})


class BannerQueue(Base):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.state = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def banner(self, data, **env):
        return self.render("banner", data, IMAGEWRITER_STATE_DIR=self.state, **env)

    def proofs(self):
        folder = os.path.join(self.state, "banners")
        proofs = []
        for name in sorted(n for n in os.listdir(folder) if n.endswith(".json")):
            with open(os.path.join(folder, name)) as f:
                proofs.append(json.load(f))
        return proofs

    def propose(self, *svgs):
        FakeClaude.reply = proposal(*svgs)
        r = self.banner(b"HAPPY HALLOWEEN with bats\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def test_request_prints_a_proof_sheet_and_saves_the_designs(self):
        r = self.propose(design_svg(1440), design_svg(2160), design_svg(2880))
        sheet = Image.open(io.BytesIO(r.stdout))
        self.assertEqual(sheet.width, 576)
        self.assertLessEqual(sheet.height, 792)
        self.assertEqual(r.stdout.count(b"P4\n"), 1)
        [proof] = self.proofs()
        self.assertEqual([d["letter"] for d in proof["designs"]], ["A", "B", "C"])
        self.assertEqual([d["length"] for d in proof["designs"]], [1440, 2160, 2880])
        self.assertIn(proof["code"].encode(), r.stderr)
        body = FakeClaude.requests[0][1]
        self.assertEqual(body["output_config"]["format"]["type"], "json_schema")
        self.assertIn("viewBox", body["system"][0]["text"] if isinstance(body["system"], list) else body["system"])

    def test_typed_choice_prints_the_whole_banner_without_claude(self):
        self.propose(design_svg(1440), design_svg(2160))
        code = self.proofs()[0]["code"]
        FakeClaude.requests = []
        r = self.banner(("print %s b\n" % code.lower()).encode(), key=None)
        self.assertEqual(r.returncode, 0, r.stderr)
        strip = Image.open(io.BytesIO(r.stdout))
        self.assertEqual(strip.size, (576, 2160))  # sideways: the banner's length runs down the paper
        self.assertEqual(FakeClaude.requests, [])

    def test_choice_in_other_words_goes_through_claude(self):
        self.propose(design_svg(1440), design_svg(2160))
        code = self.proofs()[0]["code"]
        FakeClaude.reply = json.dumps({"action": "print", "proof": code, "option": "A", "designs": []})
        r = self.banner(b"the first one please\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(Image.open(io.BytesIO(r.stdout)).size, (576, 1440))
        context = json.dumps(FakeClaude.requests[-1][1]["messages"])
        self.assertIn(code, context)
        self.assertIn("viewBox", context)  # the newest proof's designs, for changes to build on

    def test_unknown_proof_is_refused(self):
        r = self.banner(b"print ZZZ A\n")
        self.assertEqual(r.returncode, 5)
        self.assertEqual(r.stdout, b"")
        self.assertIn(b"no banner proof sheet ZZZ", r.stderr)

    def test_unsafe_or_wrong_size_designs_are_dropped(self):
        self.propose(design_svg(1440, '<image href="file:///etc/passwd" width="10" height="10"/>'),
                     design_svg(1440).replace("576", "500", 1),
                     design_svg(1440))
        [proof] = self.proofs()
        self.assertEqual([(d["letter"], d["title"]) for d in proof["designs"]], [("A", "Design 2")])

    def test_no_usable_design_prints_nothing(self):
        FakeClaude.reply = proposal(design_svg(1440, "<script>alert(1)</script>"),
                                    design_svg(1440, '<rect fill="url(http://example.com/x)" width="5" height="5"/>'),
                                    design_svg(99999))
        r = self.banner(b"HELLO\n")
        self.assertEqual(r.returncode, 5)
        self.assertEqual(r.stdout, b"")

    def test_length_limit_is_a_setting(self):
        FakeClaude.reply = proposal(design_svg(2160))
        r = self.banner(b"HELLO\n", IMAGEWRITER_MAX_BANNER_FEET="2")
        self.assertEqual(r.returncode, 5)
        self.assertIn(b"2 ft", r.stderr)

    def test_old_proofs_expire(self):
        self.propose(design_svg(1440))
        folder = os.path.join(self.state, "banners")
        path = os.path.join(folder, os.listdir(folder)[0])
        os.utime(path, (0, 0))
        code = os.path.basename(path)[:-5]
        r = self.banner(("print %s A\n" % code).encode())
        self.assertEqual(r.returncode, 5)
        self.assertFalse(os.path.exists(path))

    def test_claude_can_measure_lettering_first(self):
        item = {"text": "HI", "font_family": "Nimbus Sans", "font_size": 100, "font_weight": "normal", "letter_spacing": 0}
        FakeClaude.script = [{"tool_use": {"items": [item, dict(item, text="HIHIHI")]}}, proposal(design_svg(1440))]
        r = self.banner(b"HI\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        result = FakeClaude.requests[1][1]["messages"][-1]["content"][0]
        self.assertEqual(result["type"], "tool_result")
        short, long = json.loads(result["content"])
        self.assertTrue(40 < short["width"] < 200, short)
        self.assertAlmostEqual(long["width"] / short["width"], 3.4, delta=0.4)  # 3 copies and 2 gaps between them
        self.assertTrue(60 < short["height"] < 90, short)

    def test_lettering_past_the_end_is_sent_back_once(self):
        spill = design_svg(1440, '<rect x="1300" y="100" width="400" height="50"/>')
        FakeClaude.script = [proposal(spill, design_svg(2160))]
        FakeClaude.reply = proposal(spill, design_svg(2160))  # the "fix" changes nothing
        r = self.banner(b"HI\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(FakeClaude.requests), 2)
        feedback = FakeClaude.requests[1][1]["messages"][-1]["content"]
        self.assertIn("Design 1", feedback)
        self.assertRegex(feedback, r"\b2[56]\d units past the end")  # 1700 drawn on a 1440-long banner
        [proof] = self.proofs()
        self.assertEqual([d["length"] for d in proof["designs"]], [2160])

    def test_fixed_design_is_used(self):
        spill = design_svg(1440, '<rect x="-50" y="100" width="100" height="50"/>')
        FakeClaude.script = [proposal(spill), proposal(design_svg(1800))]
        r = self.banner(b"HI\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(b"past the start", r.stderr)
        [proof] = self.proofs()
        self.assertEqual(proof["designs"][0]["length"], 1800)

    def test_preview_draws_the_proof_sheet(self):
        FakeClaude.reply = proposal(design_svg(1440))
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "p.png")
            r = self.backend(["--preview", "banner", "JOB", out], b"HELLO\n", IMAGEWRITER_STATE_DIR=self.state)
            self.assertEqual(r.returncode, 0, r.stderr)
            with Image.open(out) as im:
                self.assertGreater(im.width, 2000)


if __name__ == "__main__":
    unittest.main()
