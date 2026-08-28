import ipaddress
import json
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from open_node_agent.diagnostics import host_name
from open_node_agent.runtime import RuntimeFailure


class RouteTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    carrier: Literal["telecom", "unicom", "mobile"]
    region: str = Field(default="", max_length=120)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=80, ge=1, le=65535)

    @field_validator("host")
    @classmethod
    def validate_host(cls, value):
        return host_name(value.strip())


class RouteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    ip_version: Literal[4, 6] = 4
    timeout_seconds: int = Field(default=25, ge=10, le=45)
    targets: list[RouteTarget] = Field(min_length=1, max_length=3)


def text(value, limit=255):
    return "".join(char for char in str(value or "") if ord(char) >= 32)[:limit]


def decode_trace(output: str) -> dict:
    # NextTrace 1.7.1 prints a non-root capability advisory before its JSON document.
    offset = 0
    for line in output.splitlines(keepends=True):
        if line.lstrip().startswith("{"):
            try:
                raw = json.loads(output[offset:])
            except ValueError:
                pass
            else:
                if isinstance(raw, dict) and isinstance(raw.get("Hops"), list):
                    return raw
        offset += len(line)
    raise RuntimeFailure("NextTrace did not return a valid JSON trace")


def trace_result(raw: dict, target: str) -> dict:
    if not isinstance(raw, dict) or not isinstance(raw.get("Hops"), list):
        raise RuntimeFailure("Invalid NextTrace JSON response")
    hops, asns = [], []
    entry = None
    for probes in raw["Hops"][:30]:
        if not isinstance(probes, list):
            raise RuntimeFailure("Invalid NextTrace hop group")
        for probe in probes[:2]:
            if not isinstance(probe, dict) or probe.get("Success") is not True:
                continue
            address = probe.get("Address")
            if not isinstance(address, dict):
                continue
            try:
                ip = str(ipaddress.ip_address(address.get("IP")))
                ttl, rtt = int(probe["TTL"]), float(probe["RTT"]) / 1_000_000
            except (KeyError, ValueError, TypeError):
                continue
            if not 1 <= ttl <= 30 or not math.isfinite(rtt) or rtt < 0:
                continue
            geo = probe.get("Geo") if isinstance(probe.get("Geo"), dict) else {}
            asn = text(geo.get("asnumber"), 32).upper().removeprefix("AS")
            asn = str(int(asn)) if asn.isascii() and asn.isdigit() and 0 < int(asn) < 2**32 else ""
            hop = {
                "hop": ttl,
                "ip": ip,
                "rtt_ms": round(rtt, 3),
                "asn": asn,
                "country": text(geo.get("country_en") or geo.get("country")),
                "region": text(geo.get("prov_en") or geo.get("prov")),
                "owner": text(geo.get("owner") or geo.get("isp")),
            }
            hops.append(hop)
            if asn and asn not in asns:
                asns.append(asn)
            location = (hop["country"] + " " + hop["region"]).lower()
            excluded = any(
                name in location
                for name in (
                    "hong kong",
                    "macau",
                    "macao",
                    "taiwan",
                    "\u9999\u6e2f",
                    "\u6fb3\u95e8",
                    "\u53f0\u6e7e",
                )
            )
            if (
                entry is None
                and not excluded
                and hop["country"].lower() in {"china", "cn", "\u4e2d\u56fd"}
            ):
                entry = hop
            break
    route = "Unknown"
    # An observed backbone ASN does not establish a commercial GIA service tier.
    for asn, label in (
        ("23764", "CTG"),
        ("4809", "CN2"),
        ("58807", "CMIN2"),
        ("9929", "9929"),
        ("10099", "10099"),
        ("4134", "163"),
        ("4837", "4837"),
        ("58453", "CMI"),
        ("9808", "CMI"),
        ("4538", "CERNET"),
        ("7497", "CSTNET"),
    ):
        if asn in asns:
            route = label
            break
    reason = "Observed ASN path: " + (", ".join("AS" + asn for asn in asns) or "unavailable")
    if entry is None:
        reason += "; mainland entry not identified"
    if route in {"CN2", "CTG"}:
        reason += "; commercial service tier is unverified"
    result = {
        "success": bool(hops),
        "hops": hops,
        "entry_hop": entry,
        "path_asns": asns,
        "route_type": route,
        "reason": reason,
        "reached": any(hop["ip"] == target for hop in hops),
    }
    if not hops:
        result["error"] = "No responding route hops"
    return result
