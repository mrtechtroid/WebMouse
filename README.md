# WebMouse

Use your phone or tablet as a wireless **touchpad, stylus pad and keyboard** for your computer. Open a web page on the phone, enter a PIN, and you are connected. Nothing to install on the phone.

- **Touchpad** with relative and absolute modes, tap-to-click, right/middle click, two-finger scroll, pinch zoom, tap-and-drag
- **Stylus** support: hover moves the pointer, touching down clicks, the barrel button right-clicks; palm rejection while the pen is in use
- **Keyboard** tab: live typing, modifiers (tap = next key, double tap = hold), arrows, F-keys, shortcuts, media keys
- **Screen mirroring** (optional) inside the touchpad, so the stylus works like a drawing tablet
- Named devices, a list of paired devices, light and dark themes
- **LAN only**, PIN protected, configurable with a TOML file

## Install and run

```bash
pip install git+https://github.com/mrtechtroid/webmouse.git
webmouse
```

<!-- ```bash
pip install webmouse           # or: pipx install webmouse
webmouse
``` -->

It prints a URL, a PIN and a QR code. Scan the QR code (it signs you in directly) or open the URL on your phone and type the PIN. Phone and computer must be on the same network.

Python 3.10 or newer. On Linux, `pynput` pulls in `python-xlib` and `evdev` (building `evdev` needs a C compiler and kernel headers: `apt install build-essential linux-libc-dev`).

For HTTPS with a self-signed certificate: `pip install "webmouse[tls]"` and `webmouse --tls`.

```
webmouse --help                       # webmouse -h
webmouse --port 9000 --pin 482913     # fixed port and PIN
webmouse --no-stream                  # no screen mirroring
webmouse --init-config                # write a commented config file
```

## Using it

| Gesture (relative mode) | Action |
|---|---|
| One finger drag | Move pointer |
| Tap | Left click |
| Two-finger tap | Right click |
| Three-finger tap | Middle click |
| Two-finger drag | Scroll |
| Pinch | Zoom (Ctrl + wheel) |
| Tap, then touch and drag | Drag and drop (double-tap = double click) |

In **absolute** mode the pad is your screen: touch a spot and the pointer goes there. Turn on **Screen** to see the desktop in the pad. The stylus is always absolute by default (Settings > Stylus), with hover to move, contact to press, barrel button for right-click. The on-screen Left/Middle/Right buttons can be held with one finger while you drag on the pad with another.

## Configuration

`webmouse --init-config` writes a fully commented file to your user config directory (`~/.config/webmouse/config.toml`, `~/Library/Application Support/webmouse/config.toml`, or `%APPDATA%\webmouse\config.toml`). Lookup order: `--config FILE`, `$PADLINK_CONFIG`, `./webmouse.toml`, the user config directory. Precedence: defaults < file < environment (`PADLINK_HOST/PORT/PIN/BACKEND/PUBLIC_URL/TLS/STREAM`) < command line.

The `[ui]` section sets defaults for new devices (theme, pointer speed, natural scrolling, and so on); each device can override them in Settings.

## Security model

webmouse lets whoever is connected control your computer, so it is strict by default:

- **LAN only by default.** Connections from anything other than loopback, private (10/8, 172.16/12, 192.168/16), link-local or IPv6 unique-local addresses are rejected before any other processing. Extra ranges (for example a Tailscale network) must be added explicitly in `allowed_networks`. `X-Forwarded-For` is never trusted.
- **PIN pairing.** A random PIN is generated at every start. Wrong guesses lock the client IP out, with the lockout doubling on repeat offences. Comparison is constant time.
- **Sessions.** After pairing, the browser gets a random token in an `HttpOnly`, `SameSite=Strict` cookie. Sessions expire (default 12 hours), are capped (default 5 devices), and can be removed from the Settings tab.
- **Web hardening.** Host header allow-list (blocks DNS rebinding), `Origin` checks on WebSocket and POST requests, a strict Content-Security-Policy, bounded message sizes, and every input message is validated and clamped.
- **Optional HTTPS** (`--tls`). Without it, traffic on your Wi-Fi is unencrypted. The PIN and keystrokes can be read by anyone able to sniff your network, so use `--tls` on networks you do not fully trust.

### Connect Remotely with Cloudflare Quick Tunnels

To use WebMouse from your phone without being on the same Wi-Fi network? You can use [Cloudflare Quick Tunnels](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/) to expose your local WebMouse server over HTTPS.

**1. Start WebMouse**

Run WebMouse on your computer:

```bash
webmouse
```

By default, it runs on port `8765`.

**2. Install `cloudflared`**

Download and install Cloudflare's `cloudflared` CLI using the [official installation guide](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/).

**3. Start a Quick Tunnel**

Open a **second terminal** and run:

```bash
cloudflared tunnel --url http://localhost:8765
```

Cloudflare will generate a temporary public URL similar to:

```text
https://random-words.trycloudflare.com
```

**4. Connect your phone**

* Open the generated HTTPS URL in your phone's browser.
* Enter the PIN displayed in the WebMouse terminal.
* Start using your phone as a touchpad, stylus pad, and keyboard!

> **⚠️ Security Note:** Quick Tunnels expose your WebMouse server to the public internet. Anyone who obtains the URL can reach the server, so keep your PIN and URL private. Use this for temporary testing, and stop the tunnel when you're done. Quick Tunnels provide temporary URLs and are not intended for production use.

## Platform notes

| Platform | Input | Screen mirroring |
|---|---|---|
| Windows | pynput | yes. Windows cannot control elevated (admin) windows unless webmouse also runs elevated |
| macOS | pynput | yes. Grant your terminal **Accessibility** (input) and **Screen Recording** (mirroring) in System Settings > Privacy & Security |
| Linux, X11 | pynput | yes |
| Linux, Wayland | `uinput` (automatic) | **no**: Wayland blocks screen capture by ordinary apps |

**Wayland / uinput.** webmouse creates virtual input devices via `/dev/uinput`, which needs permission:

```bash
echo 'KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"' | sudo tee /etc/udev/rules.d/99-uinput.rules
sudo usermod -aG input $USER     # log out and in again
sudo udevadm control --reload && sudo udevadm trigger
```

The uinput backend types using a US keyboard layout (it injects key codes, not characters). Force a backend with `--backend pynput|uinput`.

## Troubleshooting

- **Phone cannot connect:** same Wi-Fi? Some guest networks isolate devices from each other. Check your firewall allows the port (default 8765). Try using the phone hotspot and see if that works. If so, the Wi-Fi router is probably blocking the port.
- **"Forbidden" page:** you are reaching it from a non-LAN address, or via a hostname that is not this machine's name. Use the IP printed at startup, or add the name to `allowed_hosts`.
- **Pointer does not move on macOS / Windows admin windows:** see the platform notes above.
- **Several network interfaces (VPN, Docker):** the QR code uses the first LAN address. Set `public_url` in the config to pin the one you want.
- **Stream is laggy:** lower the quality in Settings or `[stream]` in the config. The stream pauses when you leave the Touchpad tab.

## Development

```bash
pip install -e ".[dev,tls]"
pytest
webmouse --backend dummy -v      # run without injecting any input
```

## License
Apache License 2.0
Copyright (C) 2026 Mr Techtroid