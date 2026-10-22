"""core/upnp.py against a FAKE box on loopback -- the suite never touches the real one
(conftest.py sets NGPCRAFT_NO_UPNP, and these tests talk to their own server)."""

from __future__ import annotations

import re
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from core import upnp

SERVICE = "urn:schemas-upnp-org:service:WANIPConnection:1"
DESC = f"""<?xml version="1.0"?><root><device><deviceList><device><serviceList>
<service><serviceType>urn:schemas-upnp-org:service:WANPPPConnection:1</serviceType>
<controlURL>/ppp</controlURL></service>
<service><serviceType>{SERVICE}</serviceType><controlURL>/ctl</controlURL></service>
</serviceList></device></deviceList></device></root>"""


class _Box(BaseHTTPRequestHandler):
    mappings: dict[int, str] = {}
    refuse_leases = False
    calls: list[str] = []

    def log_message(self, *a):  # noqa: D401 -- quiet
        pass

    def do_GET(self):
        self._reply(200, DESC)

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"])).decode()
        action = self.headers["SOAPAction"].split("#")[1].strip('"')
        _Box.calls.append(f"{self.path}:{action}")
        port = int((re.findall(r"<NewExternalPort>(\d+)<", body) or ["0"])[0])
        if action == "GetExternalIPAddress":
            return self._reply(200, "<NewExternalIPAddress>203.0.113.7</NewExternalIPAddress>")
        if action == "AddPortMapping":
            lease = int(re.findall(r"<NewLeaseDuration>(\d+)<", body)[0])
            if _Box.refuse_leases and lease != 0:
                return self._reply(500, "<errorCode>725</errorCode>")
            _Box.mappings[port] = re.findall(r"<NewInternalClient>(.*?)<", body)[0]
            return self._reply(200, "<ok/>")
        if action == "DeletePortMapping":
            if _Box.mappings.pop(port, None) is None:
                return self._reply(500, "<errorCode>714</errorCode>")
            return self._reply(200, "<ok/>")
        self._reply(500, "<errorCode>401</errorCode>")

    def _reply(self, code, text):
        data = text.encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _serve():
    _Box.mappings, _Box.calls, _Box.refuse_leases = {}, [], False
    srv = HTTPServer(("127.0.0.1", 0), _Box)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/desc.xml"


def test_a_port_is_opened_to_this_pc_then_closed_again():
    srv, loc = _serve()
    try:
        gw = upnp.gateway_from_description(loc)
        assert gw is not None and gw.service == SERVICE, "it picked the PPP service"
        assert gw.control_url.endswith("/ctl")
        assert gw.local_ip == "127.0.0.1"
        assert gw.add(7789)
        assert _Box.mappings == {7789: "127.0.0.1"}
        assert gw.external_ip() == "203.0.113.7"
        m = upnp.Mapping(gateway=gw, port=7789, external_ip="203.0.113.7")
        m.close()
        m.close()                                   # twice is harmless
        assert _Box.mappings == {}
        assert _Box.calls.count("/ctl:DeletePortMapping") == 1
    finally:
        srv.shutdown()


def test_a_box_that_only_takes_permanent_mappings_still_gets_one():
    srv, loc = _serve()
    _Box.refuse_leases = True                       # error 725 on any lease
    try:
        assert upnp.gateway_from_description(loc).add(7789)
        assert 7789 in _Box.mappings
    finally:
        srv.shutdown()


def test_nothing_is_raised_when_no_box_answers():
    with socket.socket() as s:                      # a port with nobody behind it
        s.bind(("127.0.0.1", 0))
        dead = f"http://127.0.0.1:{s.getsockname()[1]}/desc.xml"
    assert upnp.gateway_from_description(dead, timeout=0.5) is None


def test_the_switch_keeps_the_suite_off_the_real_router(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("discovery ran although NGPCRAFT_NO_UPNP is set")
    monkeypatch.setattr(upnp, "discover", boom)
    monkeypatch.setenv("NGPCRAFT_NO_UPNP", "1")
    assert upnp.open_port(7789) is None


def test_the_hosting_worker_keeps_the_rule_for_the_session(monkeypatch):
    """The WHOLE rule -- TCP included -- is handed to the shell, which keeps it for as
    long as the session runs. It used to delete the TCP rule as soon as the peer was in
    ("a connected peer needs no inbound rule"): on some boxes that drops the live NAT
    session, and measured 2026-10-22 against a remote machine nothing but the hellos got
    through and both PCs saw [WinError 10054] ~20 s later, mid cartridge trade."""
    import ngpc_shell
    from PyQt6.QtCore import Qt

    closed = []

    class _M:
        external_ip = "203.0.113.7"

        def __init__(self):
            self.protocols = ["TCP", "UDP"]

        def close(self, protocol=None):
            for p in list(self.protocols):
                if protocol is None or p == protocol:
                    self.protocols.remove(p)
                    closed.append(p)

    asked = []
    monkeypatch.setattr(upnp, "open_port",
                        lambda port, **kw: (asked.append(kw.get("protocols")), _M())[1])
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    w = ngpc_shell._NetConnect("host", "", port)
    said = []
    # Direct: emitted from the accepting thread, and this test runs no event loop.
    w.upnp.connect(lambda ok, ip: said.append((ok, ip)), Qt.ConnectionType.DirectConnection)
    got = []
    t = threading.Thread(target=lambda: got.append(w._accept()), daemon=True)
    t.start()
    peer = socket.create_connection(("127.0.0.1", port), timeout=5)
    t.join(5)
    try:
        assert got, "nobody was accepted"
        assert asked == [("TCP", "UDP")]
        assert said == [(True, "203.0.113.7")]
        assert closed == [], "a rule was closed while the connection is live"
        assert w.upnp_udp is not None and w.upnp_udp.protocols == ["TCP", "UDP"],             "the rule was not handed over whole"
    finally:
        peer.close()
        for c in got:
            c.close()


def test_a_cancelled_attempt_leaves_no_rule_at_all(monkeypatch):
    import ngpc_shell
    closed = []

    class _M:
        external_ip = ""
        protocols = ["TCP", "UDP"]

        def close(self, protocol=None):
            closed.append(protocol)

    monkeypatch.setattr(upnp, "open_port", lambda port, **kw: _M())
    w = ngpc_shell._NetConnect("host", "", 0)
    w.POLL_S = 0.05
    threading.Timer(0.3, w.cancel).start()
    try:
        w._accept()
    except Exception:  # noqa: BLE001 -- _Cancelled, as asked
        pass
    assert closed == [None], "a cancelled host left a rule open on the box"
