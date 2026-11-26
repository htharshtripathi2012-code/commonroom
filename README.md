# Commonroom

Commonroom is a lightweight, self-hosted team chat for people on the same
network. It includes local accounts, teams, channels, direct messages, threaded
replies, and persistent SQLite storage. The web interface is server-rendered:
the main chat works with JavaScript disabled, while optional JavaScript checks
for new messages every few seconds.

## Run on your Wi-Fi

Use Python 3.10 or later; the application needs no third-party packages.

```sh
python web.py
```

Open the printed address on the host or another device on the same Wi-Fi.
Allow inbound TCP port 8000 through the host firewall if other devices cannot
connect. Create an account, create a team, then share its invite code with
people you trust. Team members can create channels and start direct messages.

By default, the SQLite database is stored at `data/commonroom.sqlite3` next to
the app. Set `CHAT_DB_PATH` to choose another location. Back up this database
to preserve accounts and messages.

## Safety and privacy

This app is intended for a trusted private network, not the public internet.
HTTP traffic is unencrypted, so do not reuse an important password and do not
expose the server directly to the internet. Passwords are stored as salted
PBKDF2 hashes; messages and account data are stored in the SQLite database.

## Checks

```sh
python -m py_compile chat_store.py web.py lan.py
python -m unittest discover -s tests -v
node --check static/app.js
```

See [`deploy/README.md`](deploy/README.md) for K3s and Argo CD deployment.
