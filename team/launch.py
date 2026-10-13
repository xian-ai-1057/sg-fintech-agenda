"""Interactive desktop launcher. No installation, background service or network changes."""
from __future__ import annotations
import getpass
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
import threading
import time
from urllib.request import urlopen
import webbrowser

from .server import ROOT, Store, Server


def is_configured(path: Path) -> bool:
    """Read an existing database without creating or changing it."""
    if not path.exists():
        return False
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'").fetchone()
        return bool(exists and db.execute("SELECT 1 FROM settings WHERE key='password_hash' AND value<>''").fetchone())


def wait_ready(url, instance, attempts=40):
    for _ in range(attempts):
        try:
            with urlopen(url + 'api/health', timeout=.5) as response:
                data = json.load(response)
            if data.get('service') == 'sff-team-calendar' and data.get('instance') == instance:
                return True
        except (OSError, ValueError):
            pass
        time.sleep(.1)
    return False


def main():
    if sys.version_info < (3, 10):
        print('Python 3.10+ is required. Install it from https://www.python.org/downloads/ .')
        return 1
    os.umask(0o077)
    data = ROOT / 'team-data' / 'calendar.sqlite3'
    # Check port before prompting for any first-run setup. Bind the real server
    # later; if a race occupies it then, fail safely without killing anything.
    probe = socket.socket()
    try:
        probe.bind(('127.0.0.1', 8766))
    except OSError:
        print('Port 8766 is already in use. Close the earlier SFF window if it is yours, or run team.server manually with another --port. No process was stopped.')
        return 1
    finally:
        probe.close()
    try:
        configured = is_configured(data)
        store = Store(data)
        if not configured:
            print('First launch: create your administrator password locally (12+ characters).')
            password = getpass.getpass('Administrator password: ')
            confirmation = getpass.getpass('Confirm password: ')
            if password != confirmation:
                print('Passwords do not match. Nothing was reset; run the launcher again.')
                return 1
            store.setup(password)
        server = Server(('127.0.0.1', 8766), store, ['http://127.0.0.1:8766', 'http://localhost:8766'])
    except (sqlite3.Error, OSError, ValueError) as exc:
        print('Unable to start SFF:', exc)
        print('Existing data has not been reset. Check the project folder permissions or port.')
        return 1
    url = 'http://127.0.0.1:8766/'
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        if not wait_ready(url, server.instance):
            print('SFF did not become ready. Browser was not opened. Please retry.')
            return 1
        print('SFF is running: ' + url)
        print('The original calendar is the homepage. Click the team calendar button to collaborate.')
        print('Keep this window open. Press Ctrl+C to stop. Localhost only; teammates cannot connect until you configure LAN access separately.')
        try:
            opened = webbrowser.open(url)
        except webbrowser.Error:
            opened = False
        if not opened:
            print('Could not open a browser automatically. Open the URL above manually.')
        while worker.is_alive():
            worker.join(.5)
    except KeyboardInterrupt:
        print('\nStopping SFF. Your saved selections are retained.')
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
