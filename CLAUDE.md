# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A fullscreen pygame dashboard that displays live Home Assistant sensor data. It renders to `/dev/fb0` directly (bypassing X11/Wayland), managed by a systemd service. The same codebase runs on a **Raspberry Pi** (1024×600, 16bpp RGB565) and on an **Intel/Ubuntu box** such as `macmini1` (1920×1080, 32bpp) — the framebuffer layer auto-adapts to the panel's size/depth/stride and scales the canvas to fit (see "Display path"). The Pi is the original target; `macmini1` is a second deployment kept in sync via git (see "Deployment on Ubuntu/Intel").

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

2. **Pygame main loop** (`main`): Runs at 10 FPS. Reads `_connected` under the lock each frame and renders all cards. If `_connected` is False, shows a connecting splash.

**Forecast data** flows via a separate `on_forecast` callback and `_forecast_lock`. `HAClient` requests forecast on connect (via `call_service weather.get_forecasts`) and re-requests whenever `weather.forecast_home` changes. Message IDs 1 and 2 are reserved for the handshake/subscription; dynamic requests start at 3.

## Display path

`SDL_VIDEODRIVER=offscreen` is set in `main.py` and the service file — pygame renders into an in-memory surface, never touching a real display. After each frame, `_write_to_fb()` packs the surface into the framebuffer's native format via numpy and writes directly to `/dev/fb0`. `pygame.display.flip()` is not called.

The canvas is always rendered at `SCREEN_WIDTH × SCREEN_HEIGHT` (1024×600). `_open_fb()` reads the real panel geometry from `/sys/class/graphics/fb0/{virtual_size,bits_per_pixel,stride}` and `_write_to_fb()` adapts:
- **Depth**: 16bpp → RGB565 (Pi); 32bpp → BGRX little-endian (Intel). If red/blue ever look swapped, swap the B/R channel assignment in `_write_to_fb()`.
- **Stride**: rows are padded to the fb's `stride` when it exceeds `width × bytes`.
- **Scaling**: when the panel is larger than the canvas, the canvas is `smoothscale`d up, aspect-preserved, and centered; the surround is filled with `BG`.
- **`FB_SAFE_MARGIN`** (env, default `0`): fractional inset on each side so a TV that overscans doesn't clip the edges. The Pi leaves it `0` (pixel-identical to before); `macmini1` sets `0.04` in its `.env`. `FB_DEVICE` (env, default `/dev/fb0`) overrides the device.
- **`FB_SAFE_MARGIN_X` / `FB_SAFE_MARGIN_Y`** (env, optional): per-axis margin overrides. When either is set, the canvas fills each axis independently instead of aspect-fitting — useful to widen past the aspect-locked side bars (a deliberate horizontal stretch). `macmini1` uses `X=0.03, Y=0.04` to pull the sides out ~1 inch each while keeping the vertical inset. Lower = larger on that axis.

On the Pi specifically, the `vc4drmfb` driver holds DRM master permanently (`vc4.kms_fbdev=0` is silently ignored — `vc4: unknown parameter 'kms_fbdev' ignored`), so SDL's kmsdrm driver can never acquire DRM master; direct `/dev/fb0` file I/O is the working path. On Intel the same direct-fb path is used.

**Why not kmsdrm**: the `vc4drmfb` kernel driver holds DRM master permanently (the `vc4.kms_fbdev=0` parameter is silently ignored on this kernel — `vc4: unknown parameter 'kms_fbdev' ignored`), so SDL's kmsdrm driver can never acquire DRM master. Direct `/dev/fb0` file I/O is the working path.

**Restoring the console**: when the service is stopped, the last rendered frame stays frozen on screen. To restore the Linux console run: `sudo python3 -c "import fcntl,os; fd=os.open('/dev/tty1',os.O_RDWR); fcntl.ioctl(fd,0x4B3A,0); os.close(fd)"`

## Layout system

The screen is divided into a fixed header (`HDR_H = 90px`) and a 2-column × 3-row card grid. `card_rect(col, row)` returns the `pygame.Rect` for any card position. Card rendering functions (`draw_climate`, `draw_power`, etc.) each receive the surface, font dict, and rect, and are called from `main()` with explicit `card_rect` positions.

**Adding a new card**: write a `draw_*` function, then call it from `main()` with a `card_rect(col, row)` position. Use `draw_card()` to render the card background/header, then lay out content with the `row()` helper:

```python
def row(surf, fonts, x, y, label, value, val_color=TEXT, label_w=160) -> int:
    # renders label in DIM at x, value in val_color at x+label_w; returns y + LINE_H (28px)
```

**Font sizes** (Ubuntu/sans): `xl`=52 bold, `lg`=34 bold, `md`=22, `sm`=17.

## Raspberry Pi system notes

- **OS**: Raspberry Pi OS (aarch64); Python 3.13 at `/usr/bin/python3`; no virtual environment, packages installed system-wide with pip
- **journald**: configured for volatile (RAM) storage via `/etc/systemd/journald.conf.d/volatile.conf` — logs do not persist across reboots; done to reduce SD card write latency (was averaging 828 ms/op)
- **Monitoring**: `prometheus-node-exporter` runs and reports to Grafana; SD card write latency alerts will read higher than SSD baselines — this is normal for an SD card

## Deployment on Ubuntu/Intel (macmini1)

Second deployment of this exact repo. Edits are made upstream on `py-dashboard`; this host pulls them automatically (see "Keeping in sync").

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

**Keeping in sync**: `update.sh` + `ha-dashboard-update.{service,timer}` poll `origin` every 10 min and `git pull` + restart only when the branch moved. Install once:

```bash
sudo cp /home/jdk201/ha-dashboard/ha-dashboard-update.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now ha-dashboard-update.timer
```

Force an immediate sync with `sudo systemctl start ha-dashboard-update` (or `sudo /home/jdk201/ha-dashboard/update.sh`).

**TV / console notes** (machine-specific, migrated from the retired `macmini-dashboard` repo):
- **HDMI link autosuspend** — the i915 GPU runtime-suspends when idle and drops the HDMI link (console/dashboard "appears then disappears"). Fixed by pinning `power/control=on` for PCI `0000:00:02.0` via the `i915-no-runtime-pm.service` systemd unit (oneshot, `After=multi-user.target`, `RemainAfterExit=yes`). A udev rule alone is insufficient (the driver resets it to `auto` after coldplug). The dashboard's continuous `/dev/fb0` writes also keep the GPU active, which helps.
- **Overscan** — the TV crops ~2.5–5% of the edges. Handled in software via `FB_SAFE_MARGIN=0.04`; alternatively set the TV's aspect to "Just Scan"/"Screen Fit"/"1:1" and drop the margin.
- **Bare console legibility** (for logging into the TTY directly): `TerminusBold 32x16` in `/etc/default/console-setup`, and `GRUB_GFXMODE=1920x1080` + `GRUB_GFXPAYLOAD_LINUX=keep` in `/etc/default/grub`.
