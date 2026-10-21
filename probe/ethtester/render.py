"""Turns a snapshot and verdict into the 250×122 one-bit screen image."""

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .results import Snapshot
from .verdict import MAX_LOSS_PCT, Verdict

WIDTH, HEIGHT = 250, 122
OK, FAIL, SKIP, WAIT = "ok", "fail", "skip", "wait"

MONO = [("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 0),
        ("/System/Library/Fonts/Menlo.ttc", 0)]
MONO_BOLD = [("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 0),
             ("/System/Library/Fonts/Menlo.ttc", 1)]

ROW_HEIGHT = 13
LABEL_X, ICON_X, TEXT_X = 2, 34, 46
DIVIDER_Y = 81
HEADLINE_Y = 84
FOOTER_Y = 107


def load_font(candidates: list[tuple[str, int]], size: int):
    for path, index in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size, index=index)
    return ImageFont.load_default()


ROW_FONT = load_font(MONO, 11)
HEAD_FONT = load_font(MONO_BOLD, 18)


@dataclass(frozen=True)
class Row:
    label: str
    status: str
    text: str


def build_rows(s: Snapshot, v: Verdict) -> list[Row]:
    return [_link_row(s, v), _port_row(s), _ip_row(s, v), _gateway_row(s), _dns_row(s), _net_row(s)]


def _link_row(s: Snapshot, v: Verdict) -> Row:
    if not s.link.up:
        return Row("LINK", FAIL, "down")
    speed = f"{s.link.speed_mbps}M" if s.link.speed_mbps else "?M"
    text = f"{speed} {s.link.duplex or '?'}  flaps:{s.flaps_last_minute}"
    return Row("LINK", FAIL if v.code in ("SLOW_LINK", "FLAPPING") else OK, text)


def _port_row(s: Snapshot) -> Row:
    if not s.link.up:
        return Row("PORT", SKIP, "")
    if s.lldp and s.lldp.switch_name:
        port = f" / {s.lldp.port}" if s.lldp.port else ""
        return Row("PORT", OK, f"{s.lldp.switch_name}{port}")
    if s.lldp_given_up:
        return Row("PORT", SKIP, "no LLDP")
    return Row("PORT", WAIT, "…")


def _ip_row(s: Snapshot, v: Verdict) -> Row:
    if not s.link.up:
        return Row("IP", SKIP, "")
    d = s.dhcp
    if d is None or d.ip is None:
        if v.code == "NO_DHCP":
            return Row("IP", FAIL, "no address")
        return Row("IP", WAIT, "waiting…")
    text = f"{d.ip}/{d.prefix}" if d.prefix is not None else d.ip
    if s.seconds_to_ip is not None:
        text += f"  {s.seconds_to_ip:.1f}s"
    return Row("IP", OK, text)


def _gateway_row(s: Snapshot) -> Row:
    g = s.gateway
    if g is None:
        return Row("GW", SKIP, "")
    addr = (s.dhcp.gateway if s.dhcp else None) or "none"
    if g.reachable and g.loss_pct <= MAX_LOSS_PCT:
        rtt = f"{g.rtt_ms:.0f}ms " if g.rtt_ms is not None else ""
        return Row("GW", OK, f"{addr}  {rtt}{g.loss_pct:.0f}%")
    return Row("GW", FAIL, f"{addr}  {g.loss_pct:.0f}% loss")


def _dns_row(s: Snapshot) -> Row:
    if s.dns is None:
        return Row("DNS", SKIP, "")
    if s.dns.ok:
        return Row("DNS", OK, s.dns.server or "")
    return Row("DNS", FAIL, s.dns.server or "no server")


def _net_row(s: Snapshot) -> Row:
    if s.internet is None:
        return Row("NET", SKIP, "")
    if s.internet.ok:
        return Row("NET", OK, s.internet.public_ip or "reachable")
    return Row("NET", FAIL, "unreachable")


def _fit(draw: ImageDraw.ImageDraw, text: str, font, max_width: float) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text
    while text and draw.textlength(text + "…", font=font) > max_width:
        text = text[:-1]
    return text + "…"


def _draw_icon(draw: ImageDraw.ImageDraw, x: int, y: int, status: str) -> None:
    if status == OK:
        draw.line([(x, y + 5), (x + 3, y + 8), (x + 8, y + 1)], fill=0, width=2)
    elif status == FAIL:
        draw.line([(x + 1, y + 1), (x + 8, y + 8)], fill=0, width=2)
        draw.line([(x + 8, y + 1), (x + 1, y + 8)], fill=0, width=2)
    elif status == WAIT:
        for dx in (1, 4, 7):
            draw.rectangle([x + dx, y + 7, x + dx + 1, y + 8], fill=0)
    else:
        draw.line([(x + 1, y + 5), (x + 7, y + 5)], fill=0)


def render(rows: list[Row], v: Verdict, clock_text: str) -> Image.Image:
    image = Image.new("1", (WIDTH, HEIGHT), 255)
    draw = ImageDraw.Draw(image)
    for i, row in enumerate(rows):
        y = 2 + i * ROW_HEIGHT
        draw.text((LABEL_X, y), row.label, font=ROW_FONT, fill=0)
        _draw_icon(draw, ICON_X, y + 2, row.status)
        draw.text((TEXT_X, y), _fit(draw, row.text, ROW_FONT, WIDTH - TEXT_X - 2), font=ROW_FONT, fill=0)
    draw.line([(0, DIVIDER_Y), (WIDTH, DIVIDER_Y)], fill=0)

    headline, _, hint = v.text.partition(": ")
    draw.text((2, HEADLINE_Y), _fit(draw, headline, HEAD_FONT, WIDTH - 4), font=HEAD_FONT, fill=0)
    clock_width = draw.textlength(clock_text, font=ROW_FONT)
    draw.text((WIDTH - 2 - clock_width, FOOTER_Y), clock_text, font=ROW_FONT, fill=0)
    if hint:
        draw.text((2, FOOTER_Y), _fit(draw, hint, ROW_FONT, WIDTH - clock_width - 10), font=ROW_FONT, fill=0)
    return image


def render_message(title: str, subtitle: str) -> Image.Image:
    image = Image.new("1", (WIDTH, HEIGHT), 255)
    draw = ImageDraw.Draw(image)
    draw.text((4, 40), _fit(draw, title, HEAD_FONT, WIDTH - 8), font=HEAD_FONT, fill=0)
    draw.text((4, 70), _fit(draw, subtitle, ROW_FONT, WIDTH - 8), font=ROW_FONT, fill=0)
    return image
