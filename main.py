import asyncio
import math
import os
import signal
import sys
import threading
import time
from datetime import datetime
import numpy as np
import pygame
from config import HA_WS_URL, HA_TOKEN, SCREEN_WIDTH, SCREEN_HEIGHT
from ha_client import HAClient

os.environ.setdefault("SDL_VIDEODRIVER", "offscreen")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------
_states: dict = {}
_states_lock = threading.Lock()
_connected = False
_forecast: list = []
_forecast_lock = threading.Lock()


def on_state_change(new_states: dict):
    global _states, _connected
    with _states_lock:
        _states = new_states
        _connected = True


def on_forecast(forecast: list):
    global _forecast
    with _forecast_lock:
        _forecast = forecast


def ws_thread():
    client = HAClient(HA_WS_URL, HA_TOKEN, on_state_change, on_forecast)
    asyncio.run(client.run())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _s(entity_id: str) -> dict | None:
    return _states.get(entity_id)


def state_of(entity_id: str, default: str = "--") -> str:
    s = _s(entity_id)
    return s["state"] if s else default


def attr_of(entity_id: str, attr: str, default=None):
    s = _s(entity_id)
    return s["attributes"].get(attr, default) if s else default


def fmt_temp(val) -> str:
    try:
        return f"{float(val):.0f}°F"
    except (TypeError, ValueError):
        return str(val) if val else "--"


def fmt_power() -> str:
    try:
        w = float(state_of("sensor.xcel_itron_instantaneous_demand_value", "0"))
        return f"{w / 1000:.2f} kW" if w >= 1000 else f"{w:.0f} W"
    except (TypeError, ValueError):
        return "--"


def fmt_speed(entity_id: str) -> str:
    try:
        val = float(state_of(entity_id, "0"))
        return f"{val / 1024:.1f} MiB/s" if val >= 1024 else f"{val:.0f} KiB/s"
    except (TypeError, ValueError):
        return "--"


WEATHER_LABELS = {
    "sunny": "Sunny", "clear-night": "Clear", "partlycloudy": "Partly Cloudy",
    "cloudy": "Cloudy", "fog": "Foggy", "hail": "Hail",
    "lightning": "Lightning", "lightning-rainy": "T-Storm",
    "pouring": "Pouring", "rainy": "Rainy", "snowy": "Snowy",
    "snowy-rainy": "Sleet", "windy": "Windy", "windy-variant": "Gusty",
}

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------
BG        = (12,  15,  25)
CARD_BG   = (22,  28,  48)
CARD_HEAD = (32,  42,  72)
TEXT      = (210, 218, 235)
DIM       = (100, 112, 148)
ACCENT    = (70,  150, 255)
GREEN     = (75,  200, 110)
ORANGE    = (255, 160,  40)
RED       = (240,  80,  70)
YELLOW    = (240, 200,  50)

# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------
PAD      = 12
HDR_H    = 90
CARD_W   = (SCREEN_WIDTH  - PAD * 3) // 2   # 2 columns
CARD_H   = (SCREEN_HEIGHT - HDR_H - PAD * 4) // 3  # 3 rows
TITLE_H  = 26
LINE_H   = 28

# Header zones: [clock | forecast | current weather]
FC_X0    = 337                       # forecast zone left edge
FC_X1    = 687                       # forecast zone right edge
FC_COL_W = (FC_X1 - FC_X0) // 5     # 70px per forecast column (~30px gap between days)


def card_rect(col: int, row: int) -> pygame.Rect:
    x = PAD + col * (CARD_W + PAD)
    y = HDR_H + PAD + row * (CARD_H + PAD)
    return pygame.Rect(x, y, CARD_W, CARD_H)


def draw_card(surf: pygame.Surface, fonts: dict, rect: pygame.Rect, title: str):
    pygame.draw.rect(surf, CARD_BG, rect, border_radius=8)
    head = pygame.Rect(rect.x, rect.y, rect.w, TITLE_H)
    pygame.draw.rect(surf, CARD_HEAD, head,
                     border_top_left_radius=8, border_top_right_radius=8)
    surf.blit(fonts["sm"].render(title, True, ACCENT), (rect.x + 10, rect.y + 5))


def row(surf, fonts, x, y, label, value, val_color=TEXT, label_w=160):
    surf.blit(fonts["sm"].render(label, True, DIM), (x, y + 4))
    surf.blit(fonts["md"].render(str(value), True, val_color), (x + label_w, y))
    return y + LINE_H


# ---------------------------------------------------------------------------
# Weather icon drawing
# ---------------------------------------------------------------------------
def _draw_cloud(surf, cx, cy, w, h):
    c = (150, 165, 185)
    pygame.draw.ellipse(surf, c, pygame.Rect(cx - w//2, cy - h//4, w, h//2 + 4))
    pygame.draw.circle(surf, c, (cx - w//4, cy), h//2)
    pygame.draw.circle(surf, c, (cx + w//6, cy - h//3), h//3 + 1)


def draw_wx_icon(surf, cx, cy, condition):
    if condition == "sunny":
        pygame.draw.circle(surf, YELLOW, (cx, cy), 9)
        for a in range(0, 360, 45):
            r = math.radians(a)
            pygame.draw.line(surf, YELLOW,
                (int(cx + 12*math.cos(r)), int(cy + 12*math.sin(r))),
                (int(cx + 17*math.cos(r)), int(cy + 17*math.sin(r))), 2)
    elif condition == "clear-night":
        pygame.draw.circle(surf, (220, 210, 160), (cx, cy), 11)
        pygame.draw.circle(surf, BG, (cx + 6, cy - 4), 9)
    elif condition == "partlycloudy":
        pygame.draw.circle(surf, YELLOW, (cx - 6, cy + 5), 8)
        for a in range(0, 360, 60):
            r = math.radians(a)
            pygame.draw.line(surf, YELLOW,
                (int(cx - 6 + 10*math.cos(r)), int(cy + 5 + 10*math.sin(r))),
                (int(cx - 6 + 14*math.cos(r)), int(cy + 5 + 14*math.sin(r))), 2)
        _draw_cloud(surf, cx + 4, cy - 2, 16, 10)
    elif condition == "cloudy":
        _draw_cloud(surf, cx, cy, 22, 14)
    elif condition == "fog":
        for i in range(4):
            pygame.draw.line(surf, DIM, (cx - 14, cy - 8 + i*6), (cx + 14, cy - 8 + i*6), 2)
    elif condition in ("rainy", "pouring"):
        _draw_cloud(surf, cx, cy - 6, 20, 12)
        for i in range(3):
            pygame.draw.line(surf, ACCENT, (cx - 8 + i*8, cy + 6), (cx - 11 + i*8, cy + 15), 2)
    elif condition in ("lightning", "lightning-rainy"):
        _draw_cloud(surf, cx, cy - 6, 20, 12)
        pts = [(cx+2, cy+5), (cx-4, cy+13), (cx+1, cy+13), (cx-5, cy+21)]
        pygame.draw.lines(surf, YELLOW, False, pts, 2)
    elif condition in ("snowy", "snowy-rainy", "hail"):
        _draw_cloud(surf, cx, cy - 6, 20, 12)
        for i in range(3):
            pygame.draw.circle(surf, TEXT, (cx - 8 + i*8, cy + 13), 2)
    elif condition in ("windy", "windy-variant"):
        for i in range(3):
            y = cy - 6 + i*7
            pygame.draw.arc(surf, DIM, pygame.Rect(cx - 14, y - 3, 20, 8), 0, math.pi, 2)
            pygame.draw.line(surf, DIM, (cx + 6, y + 1), (cx + 14, y + 1), 2)
    else:
        pygame.draw.circle(surf, DIM, (cx, cy), 11, 2)


# ---------------------------------------------------------------------------
# Card renderers
# ---------------------------------------------------------------------------
def draw_header(surf: pygame.Surface, fonts: dict):
    # Left zone: clock + date
    now = time.localtime()
    surf.blit(fonts["xl"].render(time.strftime("%-I:%M %p", now), True, TEXT), (PAD + 4, 8))
    surf.blit(fonts["sm"].render(time.strftime("%A, %B %-d", now), True, DIM), (PAD + 6, 62))

    # Right zone: current conditions
    wx_state = state_of("weather.pirateweather", "")
    wx_label = WEATHER_LABELS.get(wx_state, wx_state.replace("-", " ").title())
    wx_temp  = attr_of("weather.pirateweather", "temperature")
    wx_hum   = attr_of("weather.pirateweather", "humidity")
    wx_wind  = attr_of("weather.pirateweather", "wind_speed")

    with _forecast_lock:
        fc_today = _forecast[0] if _forecast else None
    today_hi = fmt_temp(fc_today["temperature"]) if fc_today and "temperature" in fc_today else None
    today_lo = fmt_temp(fc_today["templow"])      if fc_today and "templow"     in fc_today else None

    parts = [wx_label]
    if wx_temp is not None:
        parts.append(fmt_temp(wx_temp))
    if wx_hum is not None:
        parts.append(f"Hum {wx_hum}%")
    if wx_wind is not None:
        parts.append(f"Wind {wx_wind:.0f} mph")

    wx_line1 = f"{parts[0]}  {parts[1]}" if len(parts) >= 2 else parts[0]
    wx_line2 = "  ".join(parts[2:]) if len(parts) > 2 else ""
    s1 = fonts["lg"].render(wx_line1, True, TEXT)
    surf.blit(s1, (SCREEN_WIDTH - s1.get_width() - PAD, 10))
    if wx_line2:
        s2 = fonts["sm"].render(wx_line2, True, DIM)
        surf.blit(s2, (SCREEN_WIDTH - s2.get_width() - PAD, 50))
    if today_hi or today_lo:
        wx_line3 = "  ".join(p for p in [f"Hi {today_hi}" if today_hi else "", f"Lo {today_lo}" if today_lo else ""] if p)
        s3 = fonts["sm"].render(wx_line3, True, DIM)
        surf.blit(s3, (SCREEN_WIDTH - s3.get_width() - PAD, 70))

    # Center zone: 5-day forecast icons + hi/lo
    with _forecast_lock:
        fc = _forecast[:5]
    for i, entry in enumerate(fc):
        cx = FC_X0 + i * FC_COL_W + FC_COL_W // 2
        try:
            day = datetime.fromisoformat(entry["datetime"]).strftime("%a").upper()
        except (KeyError, ValueError):
            day = "---"
        cond   = entry.get("condition", "")
        hi_str = fmt_temp(entry["temperature"]) if "temperature" in entry else "--"
        lo_str = fmt_temp(entry["templow"])     if "templow"     in entry else "--"

        day_s = fonts["sm"].render(day, True, ACCENT)
        surf.blit(day_s, (cx - day_s.get_width() // 2, 2))
        draw_wx_icon(surf, cx, 36, cond)
        hi_s = fonts["sm"].render(hi_str, True, ORANGE)
        surf.blit(hi_s, (cx - hi_s.get_width() // 2, 56))
        lo_s = fonts["sm"].render(lo_str, True, ACCENT)
        surf.blit(lo_s, (cx - lo_s.get_width() // 2, 72))

    pygame.draw.line(surf, CARD_HEAD, (0, HDR_H - 1), (SCREEN_WIDTH, HDR_H - 1))


def draw_climate(surf, fonts, rect):
    draw_card(surf, fonts, rect, "CLIMATE")
    x, y = rect.x + 10, rect.y + TITLE_H + 6

    up_temp = state_of("sensor.ecobee_upstairs_current_temperature")
    dn_temp = state_of("sensor.downstairs_temperature")
    hum     = state_of("sensor.ecobee_upstairs_current_humidity")
    t_lo = attr_of("climate.ecobee_thermostat_thermostat", "target_temp_low")
    t_hi = attr_of("climate.ecobee_thermostat_thermostat", "target_temp_high")

    y = row(surf, fonts, x, y, "Upstairs",   fmt_temp(up_temp))
    y = row(surf, fonts, x, y, "Downstairs", fmt_temp(dn_temp))
    y = row(surf, fonts, x, y, "Humidity",   f"{hum}%")

    tstat_val = f"{fmt_temp(t_lo)} – {fmt_temp(t_hi)}" if t_lo and t_hi else "--"
    y = row(surf, fonts, x, y, "Set Range",  tstat_val)


def draw_power(surf, fonts, rect):
    draw_card(surf, fonts, rect, "POWER & NETWORK")
    x, y = rect.x + 10, rect.y + TITLE_H + 4

    pw = fmt_power()
    pw_surf = fonts["lg"].render(pw, True, YELLOW)
    surf.blit(pw_surf, (rect.x + rect.w // 2 - pw_surf.get_width() // 2, y))
    y += 40

    dl = fmt_speed("sensor.m5_download_speed")
    ul = fmt_speed("sensor.m5_upload_speed")
    clients = state_of("sensor.tp_link_router_total_clients")

    y = row(surf, fonts, x, y, "Download", dl, GREEN)
    y = row(surf, fonts, x, y, "Upload",   ul, ACCENT)
    row(surf, fonts, x, y, "Devices", f"{clients} connected", DIM)


def draw_security(surf, fonts, rect):
    draw_card(surf, fonts, rect, "HOME")
    x, y = rect.x + 10, rect.y + TITLE_H + 6

    garage = state_of("cover.garage_door")
    g_color = GREEN if garage == "closed" else ORANGE
    y = row(surf, fonts, x, y, "Garage Door",
            garage.upper() if garage != "--" else "--", g_color)

    water = state_of("sensor.water_tank_level")
    y = row(surf, fonts, x, y, "Waterfall", water)

    ht_temp = attr_of("climate.hottub", "current_temperature")
    ht_set  = attr_of("climate.hottub", "temperature")
    ht_act  = attr_of("climate.hottub", "hvac_action", "")
    ht_val  = fmt_temp(ht_temp)
    if ht_set:
        ht_val += f"  (set {fmt_temp(ht_set)})"
    if ht_act and ht_act != "off":
        ht_val += f"  ▲ heating"
    y = row(surf, fonts, x, y, "Hot Tub", ht_val)

    water_age = state_of("sensor.hot_tub_water_age")
    y = row(surf, fonts, x, y, "Hot Tub Water Age", f"{water_age} days")

    filter_age = state_of("sensor.furnace_filter_age")
    row(surf, fonts, x, y, "Furnace Filter Age", f"{filter_age} days")


def _draw_face(surf, cx, cy, happy, color):
    r = 7
    pygame.draw.circle(surf, color, (cx, cy), r, 1)
    pygame.draw.circle(surf, color, (cx - 2, cy - 2), 1)
    pygame.draw.circle(surf, color, (cx + 2, cy - 2), 1)
    if happy:
        pygame.draw.arc(surf, color, pygame.Rect(cx - 3, cy, 6, 4), math.pi, 2 * math.pi, 1)
    else:
        pygame.draw.arc(surf, color, pygame.Rect(cx - 3, cy + 1, 6, 4), 0, math.pi, 1)


def _draw_battery(surf, bx, by, pct, color, charging=False):
    pygame.draw.rect(surf, color, pygame.Rect(bx, by, 18, 10), 1)
    pygame.draw.rect(surf, color, pygame.Rect(bx + 18, by + 3, 3, 4))
    fill_w = max(1, int(16 * pct / 100))
    pygame.draw.rect(surf, color, pygame.Rect(bx + 1, by + 1, fill_w, 8))
    if charging:
        pts = [(bx + 11, by + 1), (bx + 7, by + 5), (bx + 10, by + 5), (bx + 7, by + 9)]
        pygame.draw.lines(surf, RED, False, pts, 2)


def draw_family(surf, fonts, rect):
    draw_card(surf, fonts, rect, "FAMILY")
    x, y = rect.x + 10, rect.y + TITLE_H + 4

    order  = ["jonathan", "laura", "jonny", "bella", "andrew"]
    labels = {"jonathan": "Jonathan", "laura": "Laura", "jonny": "Jonny",
              "bella": "Bella", "andrew": "Andrew"}
    members = attr_of("sensor.family_locations", "members", {})

    BAT_X  = rect.right - 50
    DIST_X = rect.right - 130
    ROW_H  = 25

    for key in order:
        data = members.get(key, {})
        loc_state = data.get("state", "unknown")
        loc_str   = (data.get("location") or "Unknown")[:23]
        bat       = data.get("battery")
        charging  = data.get("charging", False)
        dist      = data.get("distance_miles")

        if loc_state == "home":
            loc_color = GREEN
        elif loc_state == "not_home":
            loc_color = ORANGE
        else:
            loc_color = DIM

        try:
            bat_val = int(float(bat))
            bat_str = f"{bat_val}%"
            bat_color = GREEN if bat_val > 50 else YELLOW if bat_val > 20 else RED
        except (TypeError, ValueError):
            bat_val, bat_str, bat_color = 0, "--", DIM

        try:
            dist_str = f"{float(dist):.1f} mi"
        except (TypeError, ValueError):
            dist_str = "--"

        cy = y + ROW_H // 2
        surf.blit(fonts["sm"].render(labels[key], True, TEXT), (x, y + 3))
        surf.blit(fonts["md"].render(loc_str, True, loc_color), (x + 95, y))
        surf.blit(fonts["sm"].render(dist_str, True, DIM), (DIST_X, y + 3))
        _draw_battery(surf, BAT_X - 26, cy - 5, bat_val, bat_color, charging)
        surf.blit(fonts["sm"].render(bat_str, True, bat_color), (BAT_X, y + 3))
        y += ROW_H


def draw_calendar(surf, fonts, rect):
    draw_card(surf, fonts, rect, "UPCOMING EVENTS")
    x, y = rect.x + 10, rect.y + TITLE_H + 6

    steam = state_of("sensor.steam_sales", "")
    if steam and steam != "--":
        s = fonts["sm"].render(f"Steam {steam}", True, ACCENT)
        surf.blit(s, (rect.right - s.get_width() - 10, rect.y + 5))

    raw = state_of("sensor.upcoming_calendar_events", "")
    events = [e.strip() for e in raw.split("|") if e.strip()] if raw and raw != "--" else []

    if not events:
        surf.blit(fonts["sm"].render("No upcoming events", True, DIM), (x, y + 6))
        return

    max_w = rect.w - 20
    for ev in events[:5]:
        s = fonts["md"].render(ev, True, TEXT)
        if s.get_width() > max_w:
            while s.get_width() > max_w and len(ev) > 3:
                ev = ev[:-1]
                s = fonts["md"].render(ev + "...", True, TEXT)
            s = fonts["md"].render(ev + "...", True, TEXT)
        surf.blit(s, (x, y))
        y += 24
        if y > rect.bottom - 10:
            break


def draw_lights(surf, fonts, rect):
    draw_card(surf, fonts, rect, "LIGHTS")
    x, y = rect.x + 10, rect.y + TITLE_H + 6

    lights = [
        ("Garage 1",    "light.garage_light_center"),
        ("Garage 2",    "light.shop_lights"),
        ("Garage OL",   "light.garage_outside_light_left"),
        ("Garage OR",   "light.garage_outdoor_light_right"),
        ("Kitchen Bar", "light.kitchen_bar_lights"),
        ("Kitchen Ctr", "light.kitchen_counter_lights"),
        ("Landscape",   "light.landscape_lights"),
        ("LR Standing", "light.livingroom_standing_lamp"),
        ("LR Table",    "light.livingroom_table_lamp"),
        ("Master L",    "light.bedroom_lamp_laura"),
        ("Master R",    "light.master_bedroom_lamp"),
        ("Porch",       "light.porch_light"),
        ("TV Light",    "light.tv_light"),
    ]

    COLS = 3
    ROWS = (len(lights) + COLS - 1) // COLS
    COL_W = (rect.w - 10) // COLS
    ROW_H = 22

    for i, (label, eid) in enumerate(lights):
        col = i // ROWS
        row = i % ROWS
        lx = x + col * COL_W
        ly = y + row * ROW_H
        st = state_of(eid)
        color = GREEN if st == "on" else DIM
        surf.blit(fonts["sm"].render("●", True, color), (lx, ly))
        surf.blit(fonts["sm"].render(label, True, TEXT if st == "on" else DIM), (lx + 14, ly + 2))


_fb_file = None

def _tty_cursor(visible: bool):
    seq = b'\033[?25h' if visible else b'\033[?25l'
    try:
        with open('/dev/tty1', 'wb') as tty:
            tty.write(seq)
    except OSError:
        pass

def _open_fb():
    global _fb_file
    try:
        _fb_file = open("/dev/fb0", "r+b")
    except OSError as e:
        print(f"Warning: cannot open /dev/fb0: {e}", flush=True)

def _write_to_fb(surface: pygame.Surface):
    if _fb_file is None:
        return
    px = pygame.surfarray.array3d(surface)   # (W, H, 3) uint8, RGB
    px = px.transpose(1, 0, 2)              # (H, W, 3)
    r = px[:, :, 0].astype(np.uint16)
    g = px[:, :, 1].astype(np.uint16)
    b = px[:, :, 2].astype(np.uint16)
    rgb565 = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
    _fb_file.seek(0)
    _fb_file.write(rgb565.tobytes())
    _fb_file.flush()


def draw_connecting(surf, fonts):
    msg = "Connecting to Home Assistant..."
    s = fonts["md"].render(msg, True, ORANGE)
    surf.blit(s, (SCREEN_WIDTH // 2 - s.get_width() // 2,
                  SCREEN_HEIGHT // 2 - s.get_height() // 2))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    _open_fb()
    _tty_cursor(False)
    pygame.init()
    screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
    pygame.mouse.set_visible(False)

    fonts = {
        "xl": pygame.font.SysFont("ubuntu,sans", 52, bold=True),
        "lg": pygame.font.SysFont("ubuntu,sans", 34, bold=True),
        "md": pygame.font.SysFont("ubuntu,sans", 22),
        "sm": pygame.font.SysFont("ubuntu,sans", 17),
    }

    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))

    threading.Thread(target=ws_thread, daemon=True).start()
    clock = pygame.time.Clock()

    try:
        while True:
            screen.fill(BG)

            with _states_lock:
                connected = _connected

            if not connected:
                draw_connecting(screen, fonts)
            else:
                draw_header(screen, fonts)
                draw_climate(screen,  fonts, card_rect(0, 0))
                draw_power(screen,    fonts, card_rect(1, 0))
                draw_security(screen, fonts, card_rect(0, 1))
                draw_family(screen,   fonts, card_rect(1, 1))
                draw_calendar(screen, fonts, card_rect(0, 2))
                draw_lights(screen,   fonts, card_rect(1, 2))

            _write_to_fb(screen)
            clock.tick(10)
    finally:
        pygame.quit()
        if _fb_file:
            _fb_file.close()
        _tty_cursor(True)


if __name__ == "__main__":
    main()
