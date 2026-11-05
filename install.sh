#!/bin/sh
# Install the ImageWriter tools for the current user.
#   ./install.sh [--prefix DIR]    (default: ~/.local)
set -eu

PREFIX="$HOME/.local"
if [ "${1:-}" = "--prefix" ]; then
    PREFIX="${2:?--prefix needs a directory}"
elif [ -n "${1:-}" ]; then
    echo "usage: ./install.sh [--prefix DIR]" >&2
    exit 2
fi

command -v python3 >/dev/null || { echo "python3 is required: sudo apt install python3" >&2; exit 1; }

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
mkdir -p "$PREFIX/bin"
for tool in iwprint iwlines iwsign; do
    install -m 755 "$here/bin/$tool" "$PREFIX/bin/$tool"
    echo "installed $PREFIX/bin/$tool"
done

case ":$PATH:" in
    *":$PREFIX/bin:"*) ;;
    *) echo "NOTE: add $PREFIX/bin to your PATH, or log out and back in if your profile already handles it." ;;
esac

if id -nG | tr ' ' '\n' | grep -qx dialout; then
    echo "$(id -un) is already in the dialout group."
else
    echo "NOTE: serial access needs the dialout group. Run this, then log out and back in:"
    echo "  sudo usermod -aG dialout $(id -un)"
fi

if ls /dev/serial/by-id/* >/dev/null 2>&1 || [ -e /dev/ttyUSB0 ]; then
    echo "Serial adapter found. Next: iwlines --watch"
else
    echo "No serial adapter found yet. Plug it in, then run: iwlines --watch"
fi
