from pathlib import Path

from PIL import ImageDraw, ImageOps

from ethtester.render import (
    FAIL, HEIGHT, OK, ROW_FONT, SKIP, WAIT, WIDTH, Row, _fit, build_rows, render, render_message,
)
from ethtester.results import DhcpResult, GatewayResult, LinkResult, Snapshot
from ethtester.verdict import Verdict, evaluate
from tests.factories import ok_snapshot

OUT = Path(__file__).parent / "output"


def rows_for(s: Snapshot) -> list[Row]:
    return build_rows(s, evaluate(s, 1000, 20))


def test_rows_all_ok():
    assert rows_for(ok_snapshot()) == [
        Row("LINK", OK, "1000M full  flaps:0"),
        Row("PORT", OK, "SW-Office / gi0/14"),
        Row("IP", OK, "192.168.1.57/24  2.1s"),
        Row("GW", OK, "192.168.1.1  3ms 0%"),
        Row("DNS", OK, "192.168.1.1"),
        Row("NET", OK, "84.1.2.3"),
    ]


def test_rows_link_down():
    rows = rows_for(Snapshot(link=LinkResult(up=False)))
    assert rows[0] == Row("LINK", FAIL, "down")
    assert all(r.status == SKIP for r in rows[1:])


def test_rows_waiting_and_no_dhcp():
    waiting = ok_snapshot(dhcp=DhcpResult(), seconds_since_link=3.0, lldp=None,
                          gateway=None, dns=None, internet=None)
    rows = rows_for(waiting)
    assert rows[1] == Row("PORT", WAIT, "…")
    assert rows[2] == Row("IP", WAIT, "waiting…")
    assert [r.status for r in rows[3:]] == [SKIP, SKIP, SKIP]
    failed = ok_snapshot(dhcp=DhcpResult(), seconds_since_link=30.0, lldp=None,
                         lldp_given_up=True, gateway=None, dns=None, internet=None)
    rows = rows_for(failed)
    assert rows[1] == Row("PORT", SKIP, "no LLDP")
    assert rows[2] == Row("IP", FAIL, "no address")


def test_rows_slow_link_and_gateway_fail():
    rows = rows_for(ok_snapshot(link=LinkResult(up=True, speed_mbps=100, duplex="full")))
    assert rows[0] == Row("LINK", FAIL, "100M full  flaps:0")
    rows = rows_for(ok_snapshot(gateway=GatewayResult(False), dns=None, internet=None))
    assert rows[3] == Row("GW", FAIL, "192.168.1.1  100% loss")


def test_fit_truncates_to_width():
    draw = ImageDraw.Draw(render_message("x", "y"))
    text = _fit(draw, "a-very-long-switch-name-that-goes-on / GigabitEthernet1/0/48", ROW_FONT, 200)
    assert text.endswith("…")
    assert draw.textlength(text, font=ROW_FONT) <= 200


def is_drawn(image) -> bool:
    return ImageOps.invert(image.convert("L")).getbbox() is not None


def test_render_samples():
    OUT.mkdir(exist_ok=True)
    samples = {
        "ok": ok_snapshot(),
        "no_link": Snapshot(link=LinkResult(up=False)),
        "cable_100m": ok_snapshot(link=LinkResult(up=True, speed_mbps=100, duplex="full")),
        "no_dhcp": ok_snapshot(dhcp=DhcpResult(), seconds_since_link=30.0, lldp=None,
                               lldp_given_up=True, gateway=None, dns=None, internet=None),
    }
    for name, snap in samples.items():
        image = render(rows_for(snap), evaluate(snap, 1000, 20), "10:42")
        assert image.size == (WIDTH, HEIGHT)
        assert image.mode == "1"
        assert is_drawn(image)
        image.save(OUT / f"{name}.png")


def test_render_message():
    image = render_message("ethtester", "waiting for link  v0.1.0")
    assert image.size == (WIDTH, HEIGHT) and is_drawn(image)
