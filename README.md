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

## Administrator

Set `CHAT_ADMIN_USERNAME=admin` and `CHAT_ADMIN_PASSWORD` before starting the
server to create or promote the site administrator. Use a unique password of at
least 10 characters and keep it in your deployment's secret manager; do not
commit credentials. For an interactive local setup without putting the
password in shell history:

```sh
export CHAT_ADMIN_USERNAME=admin
read -rsp "Admin password: " CHAT_ADMIN_PASSWORD; echo
export CHAT_ADMIN_PASSWORD
python web.py
```

If that username already exists, startup promotes the account without changing
its existing password; otherwise it creates the account with the configured
password. When adding the K3s Secret after the app is already running, restart
the Deployment so the new environment variables are loaded.

Admins get a separate `/admin` control room to review accounts, recorded IP
addresses and messages (including direct messages), manage roles, revoke
sessions, disable/delete accounts, remove team members, delete teams, and delete
messages. Disabling an account retains its data; deleting it permanently erases
its messages and direct-message history. The admin view records the most recent
client IP and activity time for each account.

The server cannot discover a device's MAC address across routers or identify
its DNS resolver from ordinary web requests, so those cannot be filtered.
Behind a reverse proxy, Commonroom uses the request peer IP by default. Set
`CHAT_TRUST_PROXY=true` only when the app is reachable exclusively through a
trusted proxy that overwrites `X-Real-IP`; then that header is used for IP
filtering. For K3s, create a Kubernetes Secret named `commonroom-admin` in the
`commonroom` namespace with `username` and `password` keys to bootstrap the
admin account.

## Safety and privacy

This app is intended for a trusted private network, not the public internet.
HTTP traffic is unencrypted, so do not reuse an important password and do not
expose the server directly to the internet. Passwords are stored as salted
PBKDF2 hashes; messages and account data are stored in the SQLite database. Use
a VPN and HTTPS for remote administration, and tell users that admins can read
messages and that the server records their last IP address.

## Checks

```sh
python -m py_compile chat_store.py web.py lan.py
python -m unittest discover -s tests -v
node --check static/app.js
```

See [`deploy/README.md`](deploy/README.md) for K3s and Argo CD deployment.
