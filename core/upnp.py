"""Open the host's port on the home router by UPnP, for the time of one attempt.

⛔ WHY THE EMULATOR DOES IT ITSELF. Hosting a direct game asks the player to forward a
port by hand in the box's web page, and that is where online play died before a single
byte was exchanged ("impossible to connect", Tixul/Ngpcraft_emulator issue #14).
MEASURED 2026-10-19 on a common ISP router: a hand-made NAT rule (TCP 7789 -> this PC,
enabled, the right device, Windows firewall open, no double NAT -- the box's own WAN
address was the public one) let NOTHING in: a GitHub runner timed out for five minutes.
The same port asked for by UPnP from this PC worked on the first try, and a five-minute
mirror match went through it. Every console and most PC games do exactly this.

What this module does, and only that:
  * find the Internet Gateway Device by SSDP (the standard multicast search);
  * ask it for its external address, add one TCP mapping to THIS PC with a lease, and
    remove it again;
  * never raise -- a router without UPnP (or with it off) is normal, and the caller
    falls back to the manual instructions it already shows.

Stdlib only, no Qt: the shell runs it on the thread that already waits for the peer.
"""

from __future__ import annotations

import os
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

SSDP_ADDR = ("239.255.255.250", 1900)
_SEARCH_TARGETS = (
    "urn:schemas-upnp-org:device:InternetGatewayDevice:1",
    "urn:schemas-upnp-org:device:InternetGatewayDevice:2",
)
_WAN_SERVICES = re.compile(
    r"<serviceType>\s*(urn:schemas-upnp-org:service:WAN(?:IP|PPP)Connection:\d)\s*"
    r"</serviceType>.*?<controlURL>\s*(.*?)\s*</controlURL>", re.S)
# Long enough for players to set a game up over Discord, short enough that a mapping a
# crashed emulator never removed closes itself. The shell deletes it far sooner.
DEFAULT_LEASE_S = 2 * 3600
DESCRIPTION = "NgpCraft link"


@dataclass
class Gateway:
    control_url: str
    service: str
    local_ip: str            # this PC's address on the router's side

    def _call(self, action: str, args: str = "", timeout: float = 4.0) -> str | None:
        body = ('<?xml version="1.0"?><s:Envelope '
                'xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
                's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
                f'<u:{action} xmlns:u="{self.service}">{args}</u:{action}>'
                '</s:Body></s:Envelope>')
        req = urllib.request.Request(
            self.control_url, data=body.encode("utf-8"),
            headers={"Content-Type": 'text/xml; charset="utf-8"',
                     "SOAPAction": f'"{self.service}#{action}"'})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError, ValueError):
            return None              # an HTTP 500 is a SOAP fault: a refusal, not a crash

    def external_ip(self) -> str:
        text = self._call("GetExternalIPAddress") or ""
        m = re.search(r"<NewExternalIPAddress>\s*([0-9.]+)\s*<", text)
        return m.group(1) if m else ""

    def add(self, port: int, *, lease_s: int = DEFAULT_LEASE_S,
            description: str = DESCRIPTION, protocol: str = "TCP") -> bool:
        def attempt(lease: int) -> bool:
            return self._call("AddPortMapping", (
                "<NewRemoteHost></NewRemoteHost>"
                f"<NewExternalPort>{int(port)}</NewExternalPort>"
                f"<NewProtocol>{protocol}</NewProtocol>"
                f"<NewInternalPort>{int(port)}</NewInternalPort>"
                f"<NewInternalClient>{self.local_ip}</NewInternalClient>"
                "<NewEnabled>1</NewEnabled>"
                f"<NewPortMappingDescription>{description}</NewPortMappingDescription>"
                f"<NewLeaseDuration>{int(lease)}</NewLeaseDuration>")) is not None
        # Some IGD:1 routers only accept a permanent mapping (error 725,
        # OnlyPermanentLeasesSupported). The shell removes it itself, so retry once.
        return attempt(lease_s) or attempt(0)

    def delete(self, port: int, protocol: str = "TCP") -> bool:
        return self._call("DeletePortMapping", (
            "<NewRemoteHost></NewRemoteHost>"
            f"<NewExternalPort>{int(port)}</NewExternalPort>"
            f"<NewProtocol>{protocol}</NewProtocol>")) is not None


def _local_ip_towards(host: str) -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((host, 1900))
        return s.getsockname()[0]
    except OSError:
        return ""
    finally:
        s.close()


def gateway_from_description(location: str, timeout: float = 4.0) -> Gateway | None:
    """Read an IGD's description document and keep its WAN connection service."""
    try:
        with urllib.request.urlopen(location, timeout=timeout) as r:
            text = r.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError):
        return None
    found = _WAN_SERVICES.findall(text)
    if not found:
        return None
    # WANIPConnection before WANPPPConnection: the PPP one is on modems that dial,
    # and on a box that has both it is usually the inactive one.
    found.sort(key=lambda sv: "PPP" in sv[0])
    service, control = found[0]
    base = re.search(r"<URLBase>\s*(.*?)\s*</URLBase>", text)
    url = urljoin(base.group(1) if base else location, control)
    local = _local_ip_towards(urlparse(location).hostname or "")
    if not local:
        return None
    return Gateway(control_url=url, service=service, local_ip=local)


def _candidate_ips() -> list[str]:
    """This PC's IPv4 addresses, the one that leads to the Internet first.

    ⛔ THE SEARCH MUST LEAVE BY THE RIGHT CARD. Left to itself, Windows sends multicast
    out of whichever interface it likes -- MEASURED here: the VirtualBox host-only
    adapter (192.168.56.1), where no router lives, so the box never heard the search
    and discovery returned nothing on a network where UPnP works.
    """
    ips: list[str] = []
    primary = _local_ip_towards("8.8.8.8")
    if primary:
        ips.append(primary)
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    return ips


def _search_from(ip: str, timeout: float) -> list[str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.settimeout(timeout)
    found: list[str] = []
    try:
        sock.bind((ip, 0))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
        for st in _SEARCH_TARGETS:
            msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n"
                   'MAN: "ssdp:discover"\r\nMX: 2\r\n' f"ST: {st}\r\n\r\n")
            sock.sendto(msg.encode("ascii"), SSDP_ADDR)
        while True:
            data, _ = sock.recvfrom(4096)
            m = re.search(rb"(?im)^location:\s*(\S+)", data)
            if m:
                loc = m.group(1).decode("ascii", "replace")
                if loc not in found:
                    found.append(loc)
                break                    # a home has one box: no need to sit out the wait
    except (socket.timeout, OSError):
        pass
    finally:
        sock.close()
    return found


def discover(timeout: float = 2.5) -> Gateway | None:
    """The home router, or None when nothing answers (no UPnP, or switched off).

    One interface at a time, the Internet-facing one first, so a PC with a single
    network card pays `timeout` once and the usual case answers in well under it.
    """
    locations: list[str] = []
    for ip in _candidate_ips()[:4]:
        locations = _search_from(ip, timeout)
        if locations:
            break
    for loc in locations:
        gw = gateway_from_description(loc)
        if gw is not None:
            return gw
    return None


@dataclass
class Mapping:
    """One port opened for one or more protocols. Closing is safe to repeat."""
    gateway: Gateway
    port: int
    external_ip: str
    protocols: list = None

    def __post_init__(self) -> None:
        if self.protocols is None:
            self.protocols = ["TCP"]

    def close(self, protocol: str | None = None) -> None:
        """Remove one protocol's rule, or all of them."""
        for proto in list(self.protocols):
            if protocol is None or proto == protocol:
                self.protocols.remove(proto)
                self.gateway.delete(self.port, proto)

    @property
    def open(self) -> bool:
        return bool(self.protocols)


def open_port(port: int, *, lease_s: int = DEFAULT_LEASE_S, timeout: float = 2.5,
              protocols: tuple = ("TCP",)) -> Mapping | None:
    """Forward TCP `port` on the router to this PC. None if it cannot be done.

    `NGPCRAFT_NO_UPNP=1` turns it off: the test suite sets it (conftest.py), and a
    player who does not want the emulator touching the box can too.
    """
    if os.environ.get("NGPCRAFT_NO_UPNP", "") not in ("", "0"):
        return None
    gw = discover(timeout)
    if gw is None:
        return None
    done = [proto for proto in protocols if gw.add(port, lease_s=lease_s, protocol=proto)]
    if "TCP" in protocols and "TCP" not in done:
        for proto in done:                 # the connection itself cannot come in: undo
            gw.delete(port, proto)
        return None
    return Mapping(gateway=gw, port=int(port), external_ip=gw.external_ip(),
                   protocols=done)
