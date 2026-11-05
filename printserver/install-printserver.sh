#!/bin/sh
# Turn this Pi into a print server with two queues for one ImageWriter:
#   ImageWriter-Text  prints what it is given
#   ImageWriter-AI    has Claude rewrite the job as an ASCII printout
# Run with sudo from a copy of this repository. Safe to run again.
set -eu

[ "$(id -u)" = 0 ] || { echo "Run with sudo." >&2; exit 1; }
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

# No recommends: this keeps out cups-browsed, which this setup does not need
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
    cups cups-client poppler-utils ghostscript acl python3-venv

install -d -m 755 /opt/imagewriter
[ -x /opt/imagewriter/venv/bin/python ] || python3 -m venv /opt/imagewriter/venv
/opt/imagewriter/venv/bin/pip install -q --upgrade -r "$here/requirements.txt"
install -m 755 "$here/../bin/iwprint" /opt/imagewriter/iwprint
# CUPS runs a backend as lp only if it is not group- or world-writable
install -o root -g root -m 755 "$here/imagewriter-backend" /usr/lib/cups/backend/imagewriter

install -d -m 755 /etc/imagewriter
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
# Share on the local network. Remote administration stays off.
cupsctl --share-printers --no-remote-admin --no-remote-any PreserveJobFiles=No

add_queue() {
    lpadmin -p "$1" -v "$2" -m raw -D "$3" -L "$(hostname)" 2>/dev/null
    lpadmin -p "$1" -E -o printer-is-shared=true -o printer-error-policy=abort-job
}
add_queue ImageWriter-Text imagewriter:/text "ImageWriter - prints text as-is"
add_queue ImageWriter-AI imagewriter:/ai "ImageWriter - Claude translates the job to ASCII"
lpstat -p

grep -q '^ANTHROPIC_API_KEY=.' /etc/imagewriter/imagewriter.env ||
    echo "NOTE: ImageWriter-AI needs an API key in /etc/imagewriter/imagewriter.env"
