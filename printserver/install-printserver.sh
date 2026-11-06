#!/bin/sh
# Turn this Pi into a print server with three queues for one ImageWriter:
#   ImageWriter-Text  prints what it is given
#   ImageWriter-AI    has Claude rewrite the job as an ASCII printout
#   ImageWriter-Graphics  prints images and PDFs as real dots
#   ImageWriter-Banner    has Claude design banners, printed as one long strip
# Run with sudo from a copy of this repository. Safe to run again.
set -eu

[ "$(id -u)" = 0 ] || { echo "Run with sudo." >&2; exit 1; }
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

# No recommends: this keeps out cups-browsed, which this setup does not need
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
    cups cups-client cups-filters avahi-daemon poppler-utils ghostscript acl python3-venv \
    librsvg2-bin fontconfig

install -d -m 755 /opt/imagewriter
[ -x /opt/imagewriter/venv/bin/python ] || python3 -m venv /opt/imagewriter/venv
/opt/imagewriter/venv/bin/pip install -q --upgrade -r "$here/requirements.txt"
install -m 755 "$here/../bin/iwprint" /opt/imagewriter/iwprint
# CUPS runs a backend as lp only if it is not group- or world-writable
install -o root -g root -m 755 "$here/imagewriter-backend" /usr/lib/cups/backend/imagewriter

install -d -m 755 /etc/imagewriter
# Banner proof sheets, so a later job can print a design from one
install -d -m 755 /var/lib/imagewriter
install -d -o lp -g lp -m 755 /var/lib/imagewriter/banners
if [ ! -e /etc/imagewriter/imagewriter.env ]; then
    install -o root -g lp -m 640 "$here/imagewriter.env.example" /etc/imagewriter/imagewriter.env
fi

# CUPS drops supplementary groups, so lp gets access to the adapter by ACL
cat > /etc/udev/rules.d/70-imagewriter.rules <<'EOF'
SUBSYSTEM=="tty", KERNEL=="ttyUSB*", RUN+="/usr/bin/setfacl -m g:lp:rw /dev/%k"
EOF
udevadm control --reload-rules
for tty in /dev/ttyUSB*; do
    [ -e "$tty" ] && setfacl -m g:lp:rw "$tty"
done

systemctl enable --now cups >/dev/null
systemctl enable --now avahi-daemon >/dev/null
# Share on the local network and announce to phones. Remote administration stays off.
cupsctl --share-printers --no-remote-admin --no-remote-any PreserveJobFiles=No

add_queue() {
    # The PPD gives phones a paper size and AirPrint details; jobs still reach the backend unrendered
    lpadmin -p "$1" -v "$2" -P "$here/imagewriter.ppd" -D "$3" -L "$(hostname)" 2>/dev/null
    lpadmin -p "$1" -E -o printer-is-shared=true -o printer-error-policy=abort-job
}
add_queue ImageWriter-Text imagewriter:/text "ImageWriter - prints text as-is"
add_queue ImageWriter-AI imagewriter:/ai "ImageWriter - Claude translates the job to ASCII"
add_queue ImageWriter-Graphics imagewriter:/graphics "ImageWriter - prints images as dots"
add_queue ImageWriter-Banner imagewriter:/banner "ImageWriter - Claude designs banners"
lpstat -p

grep -q '^ANTHROPIC_API_KEY=.' /etc/imagewriter/imagewriter.env ||
    echo "NOTE: ImageWriter-AI and ImageWriter-Banner need an API key in /etc/imagewriter/imagewriter.env"
