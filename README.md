# imagewriter-pi

Print plain text and ASCII art on an Apple ImageWriter I from a Raspberry Pi, over a USB-to-serial adapter. There is no driver or CUPS: the printer prints the ASCII it receives, so a small Python script is all it takes.

```
Raspberry Pi -> USB-serial adapter -> serial cable -> ImageWriter
```

**Tested on one setup:** a 64-bit Raspberry Pi running Debian with kernel 6.18, a Prolific PL2303 adapter, and an ImageWriter I at 9600 baud. Other adapters and cables may behave differently, especially at the flow-control step below.

## You need

- Python 3, which Raspberry Pi OS already includes
- A USB-serial adapter that Linux supports
- A serial cable wired for a printer, not a straight-through modem cable. The ImageWriter I has a DB-25 port; most adapters are DB-9.

## Install

Get this folder onto the Pi, then:

```bash
./install.sh
```

This copies `iwprint`, `iwlines` and `iwsign` to `~/.local/bin`. It tells you if your PATH or `dialout` group membership needs changing; if you get added to `dialout`, log out and back in.

## Bring up a new Pi

**1. Check that the adapter appears.** You should see a `/dev/serial/by-id/` entry or `/dev/ttyUSB0`:

```bash
ls -l /dev/serial/by-id/ /dev/ttyUSB*
```

**2. Check the cable's busy line.** With the printer on, run this and press **Select** a few times:

```bash
iwlines --watch
```

`CTS` should go to 1 with the Select light on and 0 with it off. If `CTS` never changes, `iwprint` will wait forever under its default settings, so use `--no-flow` in the next steps. On the tested cable, `CTS`, `DSR` and `CD` all changed together.

**3. Print a test.** Load paper and make sure the Select light is on:

```bash
echo "Hello from the Pi" | iwprint
```

| What comes out | What to do |
|---|---|
| Garbled characters | Baud rate mismatch. Check the printer's DIP switches and pass `--baud` to match. |
| Lines print over each other | The printer needs a line feed setting changed in its DIP switches. |
| Double-spaced lines | The printer is adding its own line feed. Change its DIP switch. |
| Nothing | Check the Select light, then the cable. |

**4. Do a long test before trusting it.** Short prints can pass even when pacing is wrong. Run the full sample, about 4 pages and 3 minutes:

```bash
iwprint examples/ai_slop.txt
```

## Everyday use

```bash
iwprint notes.txt                        # print a file
iwsign "HAPPY" "HALLOWEEN!" | iwprint    # print a big sign
iwsign "HAPPY" > sign.txt                # save a sign without printing it
```

The printer fits 80 columns and 66 lines per page. Keep text plain ASCII.

| `iwprint` option | Purpose |
|---|---|
| `--port PATH` | Choose the device, or set `IWPRINT_PORT`. By default it auto-detects a single adapter. |
| `--baud N` | Match the printer's DIP switches. Default 9600. |
| `--cps N` | Pacing in characters per second. Default 100. Lower it if long jobs garble. |
| `--line-overhead S` | Extra seconds per line. Default 0.35. |
| `--no-flow` | Ignore the busy signal and rely on pacing alone. |

## Why it paces every line

Sending a 4-page job at full speed with RTS/CTS flow control on garbled it about a page and a half in. Sending the same kind of text one line at a time at about the printer's own speed printed cleanly, so `iwprint` does that and still checks the busy signal before each line.

The likely cause is data already in flight in the USB adapter when the printer signals busy, but that was never measured directly. Treat it as a working theory.

## Print server

`printserver/` turns the Pi into a CUPS print server with two printers for the one ImageWriter:

| Printer | What it does |
|---|---|
| `ImageWriter-Text` | Prints what it's given. Plain text goes through unchanged, apart from wrapping at 80 columns and replacing anything the printer can't print. PDFs and PostScript have their text extracted. Images are rejected. |
| `ImageWriter-AI` | Sends the job to Claude, which rewrites it as a readable ASCII printout. Handles PDFs, images, web pages and text. |

**Anything sent to `ImageWriter-AI` is uploaded to the Anthropic API and billed to your API key.** Don't send it documents you wouldn't want to leave your network.

Install from a copy of this repo on the Pi. This installs CUPS and shares both printers on your local network, with remote administration off:

```bash
sudo sh printserver/install-printserver.sh
```

Then add your API key to `/etc/imagewriter/imagewriter.env`, which is readable only by root and the `lp` group:

```bash
sudo nano /etc/imagewriter/imagewriter.env
```

Print from the Pi or preview without using paper:

```bash
lp -d ImageWriter-Text notes.txt
lp -d ImageWriter-AI article.pdf
/usr/lib/cups/backend/imagewriter --render ai article.pdf
```

Other computers can add the printers from `http://PI-ADDRESS:631/printers/ImageWriter-Text` and `http://PI-ADDRESS:631/printers/ImageWriter-AI`. The client must send PDF, PostScript, an image or plain text. Other formats are rejected rather than printed as junk.

### Phones

Both printers are announced on the local network with Avahi, so iPhones and Android phones on the same network can look for them in their normal print menu. Discovery usually doesn't cross VLANs or guest Wi-Fi. On Android, you may need to turn on the Default Print Service under your printing settings.

Raster jobs (URF or PWG) from a phone are converted to PDF before they reach the backend. They contain only images, so `ImageWriter-Text` refuses them and suggests `ImageWriter-AI`. The same goes for photos.

**Once announced, `ImageWriter-AI` appears in the print menu of everyone on your network, and their jobs are billed to your API key.** To stop announcing a printer while still using it from this Pi, run `sudo lpadmin -p ImageWriter-AI -o printer-is-shared=false`.

**Limits:** each job stops at `IMAGEWRITER_MAX_PAGES` (default 4), and `ImageWriter-AI` rejects PDFs over `IMAGEWRITER_MAX_INPUT_PAGES` (default 50). A failed job is dropped without retrying, and the reason appears in the CUPS job log. Change these in the same settings file.

## Extras

`extras/photo2ascii.py` converts a photo to cartoon-style ASCII art. It needs Pillow and NumPy, and it's hit and miss: faces need a tight crop and usually a hand-traced outline to remove the background.

```bash
sudo apt install python3-pil python3-numpy
python3 extras/photo2ascii.py photo.jpg --crop 170,90,970,1150 --preview preview.png > photo.txt
```

Check the preview image before spending paper. See `--help` for the outline and tuning options.

## Tests

These need no printer:

```bash
python3 tests/test_tools.py
```

They don't cover the busy-signal path, which needs real hardware. Step 2 above checks that.

On a print server, this also tests the backend using a local stand-in for the Claude API, so it needs no key and spends nothing:

```bash
/opt/imagewriter/venv/bin/python tests/test_backend.py
```
