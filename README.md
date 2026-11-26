# Chess

A lightweight chess web app for local-network games. It uses Python's standard
library; no package installation is required.

## Run on your Wi-Fi

Start the server on the computer hosting the game:

```sh
python web.py
```

The server prints its LAN address. Open that address in a browser on the host
or another device on the same Wi-Fi. Allow the server's port (8000 by default)
through the host firewall if another device cannot connect.

Choose **Same device** for hot-seat play, or **Over Wi-Fi** to create a room.
Share the room code and server address with the other player. The board scales
to mobile screens and rotates to the online player's perspective.

Online rooms support clocks with increments, live chat, draw offers with
accept/decline, and resignation. Room and chat data are held in memory and are
lost when the server restarts. Keep the server on a trusted network; it is
intended for LAN use, not direct exposure to the public internet.

## Debugging and checks

Each HTTP request is logged with a request ID, client address, route, status,
duration, and error category. The browser includes the request ID when showing
an HTTP error so it can be matched to the server log. Authentication tokens and
chat text are not logged.

Run the built-in checks with:

```sh
python -m py_compile chess.py web.py main.py rooms.py clocks.py lan.py
python clocks.py
python rooms.py
node --check static/app.js
```

See [`deploy/README.md`](deploy/README.md) for the K3s and Argo CD deployment
setup.
