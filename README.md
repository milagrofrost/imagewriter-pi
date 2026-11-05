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
