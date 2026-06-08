# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development

No build step, test suite, or linter. Edit `main.py` or `ha_client.py` directly and restart the service to test. To run without the service: `python3 main.py` (requires `/dev/fb0` write access). There are no automated tests — verify changes visually on the display.

## What this is

A fullscreen pygame dashboard that displays live Home Assistant sensor data. It renders to `/dev/fb0` directly (bypassing X11/Wayland), managed by a systemd service. The **same code and service file** run on both hosts; all Python changes affect both deployments.

| | Raspberry Pi | macmini1 |
|---|---|---|
| OS | Raspberry Pi OS (aarch64) | Ubuntu 24.04.4 LTS |
| Display | 1024×600, 16bpp RGB565 | 1920×1080, 32bpp BGRX |
| Safe margin | none (`.env` omits it) | `FB_SAFE_MARGIN_X=0.03`, `FB_SAFE_MARGIN_Y=0.04` |
| Repo path | `~/ha-dashboard` | `~/repos/ha-dashboard` (symlinked to `~/ha-dashboard`) |
| Code changes | made here, pushed to origin | pulled automatically every 10 min via `ha-dashboard-update` timer |
| Dependencies | pip | apt |

The framebuffer layer auto-adapts to the panel's size/depth/stride and scales the canvas to fit (see "Display path").

## Dependencies

```bash
# Raspberry Pi (pip)
pip install pygame numpy websockets

# Ubuntu/Debian (apt — keeps system /usr/bin/python3, matches the service file)
sudo apt install -y python3-pygame python3-numpy python3-websockets
```

## Configuration

`config.py` reads `HA_HOST`, `HA_PORT`, and `HA_TOKEN` from environment variables, falling back to defaults. `HA_WS_URL`, `SCREEN_WIDTH` (1024), and `SCREEN_HEIGHT` (600) are derived there. The service loads secrets via `EnvironmentFile=/home/jdk201/ha-dashboard/.env` (git-ignored); set `HA_TOKEN=...` in that file. To point at a different HA instance, edit the env var defaults in `config.py` or override in `.env`.

## Running and managing the service

These commands work identically on both hosts.

```bash
# Run directly (renders to /dev/fb0 — must have write access)
python3 main.py

# Service management
sudo systemctl start ha-dashboard
sudo systemctl stop ha-dashboard
sudo systemctl restart ha-dashboard
sudo systemctl status ha-dashboard
journalctl -u ha-dashboard -f   # live logs

# Install or update service file
sudo cp ha-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
```

## Architecture

The app has two threads:

1. **WebSocket thread** (`ws_thread` → `HAClient.run`): Connects to HA at `HA_WS_URL`, fetches a full state snapshot on connect, then subscribes to `state_changed` events. On each event, it merges the new state into `self.states` and calls `on_state_change(self.states)` — passing the **full** states dict every time, not a diff. `on_state_change` in `main.py` replaces `_states` under `_states_lock` and sets `_connected = True`. Reconnects automatically on failure with a 5-second backoff.

2. **Pygame main loop** (`main`): Polls at 4 FPS but rendering is **event-driven** via `_dirty` (a `threading.Event`). A frame is only drawn when `_dirty` is set *and* at least 1 second has elapsed since the last render. `_dirty` is set by: `on_state_change` (only for entities listed in `_WATCHED_ENTITIES`, or `None` on initial snapshot load), `on_forecast`, and the main loop itself once per minute (to tick the clock). **When adding a new entity to a card, add it to `_WATCHED_ENTITIES` in `main.py` or state changes for that entity will silently skip re-renders.** If `_connected` is False, shows a connecting splash instead.

**Forecast data** flows via a separate `on_forecast` callback and `_forecast_lock`. `HAClient` requests forecast on connect (via `call_service weather.get_forecasts`) and re-requests whenever `weather.pirateweather` changes. Message IDs 1 and 2 are reserved for the handshake/subscription; dynamic requests start at 3.

## Display path

`SDL_VIDEODRIVER=offscreen` is set in `main.py` and the service file — pygame renders into an in-memory surface, never touching a real display. After each frame, `_write_to_fb()` packs the surface into the framebuffer's native format via numpy and writes directly to `/dev/fb0`. `pygame.display.flip()` is not called.

The canvas is always rendered at `SCREEN_WIDTH × SCREEN_HEIGHT` (1024×600). `_open_fb()` reads the real panel geometry from `/sys/class/graphics/fb0/{virtual_size,bits_per_pixel,stride}` and `_write_to_fb()` adapts:
- **Depth**: 16bpp → RGB565 (Pi); 32bpp → BGRX little-endian (Intel). If red/blue ever look swapped, swap the B/R channel assignment in `_write_to_fb()`.
- **Stride**: rows are padded to the fb's `stride` when it exceeds `width × bytes`.
- **Scaling**: when the panel is larger than the canvas, the canvas is `smoothscale`d up, aspect-preserved, and centered; the surround is filled with `BG`.
- **`FB_SAFE_MARGIN`** (env, default `0`): fractional inset on each side so a TV that overscans doesn't clip the edges. The Pi leaves it `0` (pixel-identical to before); `macmini1` sets `0.04` in its `.env`. `FB_DEVICE` (env, default `/dev/fb0`) overrides the device.
- **`FB_SAFE_MARGIN_X` / `FB_SAFE_MARGIN_Y`** (env, optional): per-axis margin overrides. When either is set, the canvas fills each axis independently instead of aspect-fitting — useful to widen past the aspect-locked side bars (a deliberate horizontal stretch). `macmini1` uses `X=0.03, Y=0.04` to pull the sides out ~1 inch each while keeping the vertical inset. Lower = larger on that axis.

On the Pi specifically, the `vc4drmfb` driver holds DRM master permanently (`vc4.kms_fbdev=0` is silently ignored — `vc4: unknown parameter 'kms_fbdev' ignored`), so SDL's kmsdrm driver can never acquire DRM master; direct `/dev/fb0` file I/O is the working path. On Intel the same direct-fb path is used.

**Restoring the console**: when the service is stopped, the last rendered frame stays frozen on screen. To restore the Linux console run: `sudo python3 -c "import fcntl,os; fd=os.open('/dev/tty1',os.O_RDWR); fcntl.ioctl(fd,0x4B3A,0); os.close(fd)"`

## Layout system

The screen is divided into a fixed header (`HDR_H = 90px`) and a 2-column × 3-row card grid. `card_rect(col, row)` returns the `pygame.Rect` for any card position. Card rendering functions each receive the surface, font dict, and rect, and are called from `main()` with explicit `card_rect` positions:

| Position | Card |
|---|---|
| `card_rect(0, 0)` | `draw_climate` |
| `card_rect(1, 0)` | `draw_system_status` |
| `card_rect(0, 1)` | `draw_security` (titled **"HOME"** on screen) |
| `card_rect(1, 1)` | `draw_family` |
| `card_rect(0, 2)` | `draw_calendar` |
| `card_rect(1, 2)` | `draw_lights` |

**Adding a new card**: write a `draw_*` function, call it from `main()`, and add any new entity IDs to `_WATCHED_ENTITIES`. Use `draw_card()` for the card background/header, then `row()` for content:

```python
def row(surf, fonts, x, y, label, value, val_color=TEXT, label_w=160) -> int:
    # renders label in white (DIM) at x, value in val_color at x+label_w; returns y + LINE_H (28px)
```

**Row spacing**: each card has 128px of content (CARD_H=154 − TITLE_H=26). `LINE_H=28` is the default but cards with 5 rows use a manual `y += 25` instead of the `row()` return value to fit without clipping. Cards with 4 rows use `y += 30` to fill the space evenly. Don't rely on `LINE_H` — check the math for the target card.

**Colors**: `DIM = (255,255,255)` (white) is used for secondary/label text everywhere. `LIGHTS_DIM = (100,112,148)` (gray) is reserved for off-state indicators in the lights panel only. `ACCENT = (70,150,255)` (blue) is used for card titles and right-justified header annotations (e.g. Public IP, Steam sales). Health sensor values map to `GREEN`/`YELLOW`/`RED` via `STATUS_COLOR = {"healthy": GREEN, "warning": YELLOW, "critical": RED}`.

**Font sizes** (Ubuntu/sans): `xl`=52 bold, `lg`=34 bold, `md`=22, `sm`=17.

**Custom sensor shapes** (not visible from entity names alone):

- `sensor.family_locations` — state is unused; `attr_of(..., "members", {})` returns a dict keyed by lowercase first name (`"jonathan"`, `"laura"`, etc.), each value `{"state": "home"|"not_home"|"unknown", "location": str, "battery": int|None, "charging": bool, "distance_miles": float|None}`.
- `sensor.upcoming_calendar_events` — a pipe-delimited `|` string of event descriptions (e.g. `"School play | Doctor appt"`). Split on `|`, strip whitespace; up to 5 shown. Empty or `"--"` means no events.

## Raspberry Pi system notes

- **OS**: Raspberry Pi OS (aarch64); Python 3.13 at `/usr/bin/python3`; no virtual environment, packages installed system-wide with pip
- **journald**: configured for volatile (RAM) storage via `/etc/systemd/journald.conf.d/volatile.conf` — logs do not persist across reboots; done to reduce SD card write latency (was averaging 828 ms/op)
- **Monitoring**: `prometheus-node-exporter` runs and reports to Grafana; SD card write latency alerts will read higher than SSD baselines — this is normal for an SD card

## Deployment on Ubuntu/Intel (macmini1)

Second deployment of this exact repo. Edits are made on the Raspberry Pi (`py-dashboard`) and pushed to origin; this host pulls them automatically (see "Keeping in sync").

**Host**: `macmini1` — Ubuntu 24.04.4 LTS, kernel 6.8.0-124-generic, Intel Core i5-3210M, 8 GB RAM, IP `192.168.68.152`. No desktop environment — pure TTY. Display is a 1080p TV on the **direct HDMI** port (`HDMI-A-3`); a USB-to-HDMI adapter (Norelsys NS1081) is also plugged in but has no Linux DRM driver and never produces signal — ignore it. `sudo` is passwordless.

**Install**

```bash
sudo apt install -y python3-pygame python3-numpy python3-websockets
sudo usermod -aG video jdk201          # grant /dev/fb0 write (service picks it up on next start)
# Repos live in ~/repos on this host; the service path is ~/ha-dashboard, so symlink it.
git clone https://github.com/jdk20111/ha-dashboard.git /home/jdk201/repos/ha-dashboard
ln -s /home/jdk201/repos/ha-dashboard /home/jdk201/ha-dashboard
# .env (git-ignored): HA_HOST/HA_TOKEN plus the overscan/width insets
cat >> /home/jdk201/ha-dashboard/.env <<'ENV'
FB_SAFE_MARGIN=0.04
FB_SAFE_MARGIN_X=0.03
FB_SAFE_MARGIN_Y=0.04
ENV
sudo cp /home/jdk201/ha-dashboard/ha-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now ha-dashboard
```

The `ha-dashboard.service` file is shared with the Pi unchanged (it uses the `~/ha-dashboard` path) — apt's `python3-*` keep `/usr/bin/python3` and websockets 10.4, which `ha_client.py` is compatible with. On `macmini1` the checkout lives at `~/repos/ha-dashboard` with `~/ha-dashboard` symlinked to it, so the shared unit, `update.sh`, and `EnvironmentFile` all resolve without editing the unit.

**Keeping in sync** (macmini1 only): `update.sh` + `ha-dashboard-update.{service,timer}` poll `origin` every hour and restart only when the branch moved. Install once:

```bash
sudo cp /home/jdk201/ha-dashboard/ha-dashboard-update.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now ha-dashboard-update.timer
```

Force an immediate sync with `sudo systemctl start ha-dashboard-update` (or `sudo /home/jdk201/ha-dashboard/update.sh`).

**TV / console notes** (machine-specific, migrated from the retired `macmini-dashboard` repo):
- **HDMI link autosuspend** — the i915 GPU runtime-suspends when idle and drops the HDMI link (console/dashboard "appears then disappears"). Fixed by pinning `power/control=on` for PCI `0000:00:02.0` via the `i915-no-runtime-pm.service` systemd unit (oneshot, `After=multi-user.target`, `RemainAfterExit=yes`). A udev rule alone is insufficient (the driver resets it to `auto` after coldplug). The dashboard's continuous `/dev/fb0` writes also keep the GPU active, which helps.
- **Overscan** — the TV crops ~2.5–5% of the edges. Handled in software via `FB_SAFE_MARGIN=0.04`; alternatively set the TV's aspect to "Just Scan"/"Screen Fit"/"1:1" and drop the margin.
- **Bare console legibility** (for logging into the TTY directly): `TerminusBold 32x16` in `/etc/default/console-setup`, and `GRUB_GFXMODE=1920x1080` + `GRUB_GFXPAYLOAD_LINUX=keep` in `/etc/default/grub`.
