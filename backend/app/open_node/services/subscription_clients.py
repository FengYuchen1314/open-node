"""Client schema conversion for the supported, version-pinned subscription targets."""

from copy import deepcopy
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

EXTRA_FORMATS = frozenset({"loon", "quantumult-x", "shadowrocket", "stash", "surfboard", "egern"})
TEXT_NODE_FORMATS = frozenset({"loon", "quantumult-x", "surfboard"})


def select_client_format(value, user_agent: str = ""):
    from open_node.domain.subscriptions import SubscriptionClientFormat

    if value is not None and value != "auto":
        return SubscriptionClientFormat(value)
    # Specific clients precede generic Clash/Surge substrings, as in pinned client_ua.go.
    agent = user_agent[:4096].casefold()
    for keywords, target in (
        (("stash",), "stash"),
        (("shadowrocket",), "shadowrocket"),
        (("surge",), "surge"),
        (("loon",), "loon"),
        (("quantumult%20x", "quantumult x", "quantumultx"), "quantumult-x"),
        (("egern",), "egern"),
        (("surfboard",), "surfboard"),
        (("sing-box", "sfi/", "sfa/", "sfm/", "sft/"), "sing-box"),
        (("v2rayn", "v2rayng", "v2box"), "base64"),
    ):
        if any(keyword in agent for keyword in keywords):
            return SubscriptionClientFormat(target)
    return SubscriptionClientFormat.CLASH


def protocol(proxy: dict[str, Any]) -> str:
    value = str(proxy.get("type") or "").lower()
    return {
        "ss": "shadowsocks",
        "socks5": "socks",
        "hy2": "hysteria2",
        "hysteria": "hysteria2",
    }.get(value, value)


def network(proxy: dict[str, Any]) -> str:
    value = str(proxy.get("network") or "tcp").lower()
    if protocol(proxy) == "hysteria2" and value == "hysteria":
        return "tcp"
    if value == "ws" and record(proxy.get("ws-opts")).get("v2ray-http-upgrade"):
        return "httpupgrade"
    return {"raw": "tcp", "http-upgrade": "httpupgrade"}.get(value, value)


def record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def upgrade_options(proxy: dict[str, Any]) -> dict[str, Any]:
    options = record(proxy.get("http-upgrade-opts"))
    if options:
        return deepcopy(options)
    ws = record(proxy.get("ws-opts"))
    headers = deepcopy(record(ws.get("headers")))
    host = next((headers.pop(key) for key in list(headers) if key.lower() == "host"), "")
    return {"path": ws.get("path", "/"), "host": host, "headers": headers}


def public_proxy(proxy: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(proxy)
    for key in list(result):
        if key.startswith("_open_node_"):
            result.pop(key, None)
    return result


def unsupported_reason(proxy: dict[str, Any], target: str) -> str | None:
    if target in EXTRA_FORMATS:
        from open_node.services.subscription_extra_clients import unsupported_reason as extra_reason

        # Share credential/TLS/type validation, not the target's wire representation.
        return unsupported_reason(proxy, "clash") or extra_reason(proxy, target)
    kind, transport = protocol(proxy), network(proxy)
    if proxy.get("dialer-proxy"):
        if target not in {"clash", "stash", "sing-box", "xray"}:
            return "This client format cannot represent chained proxies"
        if target == "xray" and proxy.get("_open_node_has_load_balance"):
            return "Xray subscriptions cannot represent chained load-balancer groups"
    if proxy.get("_open_node_proxy_groups") and target not in {"clash", "stash"}:
        return "This client format cannot represent chained load-balancer groups"
    supported = {"vless", "vmess", "trojan", "shadowsocks", "hysteria2", "anytls", "socks", "http"}
    if target in {"clash", "xray"}:
        supported.add("snell")
    if target == "clash":
        supported.add("mieru")
    if target == "surge":
        supported = {
            "vmess",
            "trojan",
            "shadowsocks",
            "snell",
            "anytls",
            "hysteria2",
            "http",
            "socks",
        }
    if target in {"uri-list", "base64"}:
        supported.discard("anytls")
    if proxy.get("shadow-tls-opts") and target != "clash":
        return "AnyTLS ShadowTLS requires native Mihomo/Clash export"
    if (
        target == "xray"
        and transport == "xhttp"
        and record(proxy.get("xhttp-opts")).get("reuse-settings")
    ):
        return "Mihomo XHTTP XMUX settings require native Clash export"
    if kind not in supported:
        return "Protocol is not supported by this client format"
    if not isinstance(proxy.get("server"), str) or not proxy["server"].strip():
        return "Server address is missing"
    value = proxy.get("port")
    if not (type(value) is int or isinstance(value, str) and value.isascii() and value.isdigit()):
        return "Server port is invalid"
    if not 1 <= int(value) <= 65535:
        return "Server port is invalid"
    credential = {
        "vless": ("uuid", "id"),
        "vmess": ("uuid", "id"),
        "trojan": ("password",),
        "shadowsocks": ("password",),
        "hysteria2": ("password", "auth"),
        "anytls": ("password",),
        "snell": ("psk",),
        "mieru": ("password",),
    }.get(kind)
    if credential and not any(proxy.get(key) for key in credential):
        return "User credential is missing"
    if kind == "snell":
        version = proxy.get("version", 4)
        if type(version) is not int or version not in (4, 5, 6):
            return "Snell version is not supported"
        if target == "clash" and version == 6:
            return "Snell v6 requires the Xray compatibility client"
        if version == 6 and (proxy.get("mode") or "default") not in ("default", "unshaped"):
            return "Snell v6 mode does not provide authenticated user access"
        if version != 6 and record(proxy.get("obfs-opts")).get("mode", "none") not in (
            "",
            "none",
            "http",
            "tls",
        ):
            return "Snell obfuscation mode is not supported"
    if kind == "mieru":
        if not proxy.get("username"):
            return "Mieru username is missing"
        if str(proxy.get("transport") or "TCP").upper() not in ("TCP", "UDP"):
            return "Mieru transport is not supported"
    if kind in {"vless", "vmess", "trojan"}:
        transports = {
            "clash": {"tcp", "ws", "grpc", "http", "h2", "httpupgrade", "xhttp"},
            "sing-box": {"tcp", "ws", "grpc", "h2", "httpupgrade"},
            "xray": {"tcp", "ws", "grpc", "httpupgrade", "xhttp"},
            "uri-list": {"tcp", "ws", "grpc", "httpupgrade"},
            "base64": {"tcp", "ws", "grpc", "httpupgrade"},
            "surge": {"tcp", "ws"},
        }
        if target in transports and transport not in transports[target]:
            return "Transport is not supported by this client format"
        if target == "clash" and kind == "trojan" and transport in {"http", "h2"}:
            return "This Trojan transport is not supported by Mihomo"
    elif transport != "tcp":
        return "This protocol does not support a V2Ray transport wrapper"
    tls = record(proxy.get("tls"))
    if "skip-cert-verify" in proxy and type(proxy["skip-cert-verify"]) is not bool:
        return "Certificate verification flag must be a boolean"
    if "insecure" in tls and type(tls["insecure"]) is not bool:
        return "Certificate verification flag must be a boolean"
    if proxy.get("reality-opts") and (proxy.get("tls") is False or tls.get("enabled") is False):
        return "REALITY cannot disable TLS"
    if kind in {"anytls", "hysteria2"} and (
        proxy.get("tls") is False or tls.get("enabled") is False
    ):
        return "This protocol requires TLS"
    if target != "sing-box" and tls.keys() - {"enabled", "server_name", "insecure", "alpn", "utls"}:
        return "Custom TLS options require native sing-box export"
    if target != "sing-box" and record(tls.get("utls")).keys() - {"enabled", "fingerprint"}:
        return "Custom uTLS options require native sing-box export"
    if target not in {"clash", "surge"} and any(
        proxy.get(key)
        for key in ("fingerprint", "name-cert-verify", "certificate", "private-key", "ca", "ca-str")
    ):
        return "Mihomo certificate verification options require Clash export"
    if (
        target == "clash"
        and kind == "trojan"
        and (proxy.get("tls") is False or tls.get("enabled") is False)
    ):
        return "Mihomo Trojan requires TLS"
    if target == "xray" and transport == "ws":
        ws = record(proxy.get("ws-opts"))
        if (
            ws.get("max-early-data")
            and ws.get("early-data-header-name", "").lower() != "sec-websocket-protocol"
        ):
            return "Xray WebSocket early data requires Sec-WebSocket-Protocol"
    if target not in {"clash", "surge"} and proxy.get("plugin"):
        return "Shadowsocks plugin conversion is not supported"
    if target in {"uri-list", "base64"}:
        if transport == "httpupgrade":
            return "Mihomo v1.19.30 URI import requires native export for HTTPUpgrade"
        if kind in {"socks", "http", "shadowsocks"} and proxy.get("tls"):
            return "TLS for this protocol requires native client export"
        options = record(proxy.get("ws-opts")) if transport == "ws" else upgrade_options(proxy)
        if transport in {"ws", "httpupgrade"} and (
            any(key.lower() != "host" for key in record(options.get("headers")))
            or options.get("max-early-data")
        ):
            return "Custom transport headers or early data require native client export"
    if (
        target == "xray"
        and kind == "hysteria2"
        and any(proxy.get(key) for key in ("obfs", "ports", "up", "down", "hop-interval"))
    ):
        return "Hysteria2 obfuscation, bandwidth or port hopping requires another format"
    if kind == "hysteria2" and target == "sing-box":
        if proxy.get("obfs") and (proxy["obfs"] != "salamander" or not proxy.get("obfs-password")):
            return "Hysteria2 obfuscation requires salamander and a password"
        for key in ("up", "down"):
            if key in proxy and (type(proxy[key]) is not int or proxy[key] < 0):
                return "Hysteria2 bandwidth conversion requires integer Mbps"
    if proxy.get("reality-opts"):
        if kind != "vless" or not record(proxy["reality-opts"]).get("public-key"):
            return "REALITY requires VLESS and a public key"
    return surge_unsupported_reason(proxy) if target == "surge" else None


def surge_unsupported_reason(proxy):
    from open_node.services.template_rendering import surge_proxy

    kind, transport = protocol(proxy), network(proxy)
    tls = sing_box_tls(proxy)
    if kind == "trojan" and (
        proxy.get("tls") is False or record(proxy.get("tls")).get("enabled") is False
    ):
        return "Surge Trojan requires TLS"
    if (
        proxy.get("reality-opts")
        or proxy.get("client-fingerprint")
        or record(record(tls).get("utls")).get("enabled")
    ):
        return "Surge cannot represent REALITY or custom uTLS fingerprints"
    if any(proxy.get(key) for key in ("certificate", "private-key", "ca", "ca-str")):
        return "Custom certificate material requires another client format"
    if kind == "vmess" and (proxy.get("cipher") or "auto") not in {
        "auto",
        "aes-128-gcm",
        "chacha20-ietf-poly1305",
    }:
        return "Surge VMess does not support this cipher"
    if kind == "snell":
        obfs_mode = record(proxy.get("obfs-opts")).get("mode", "none")
        if proxy.get("version", 4) == 6 and obfs_mode not in {"", "none"}:
            return "Surge Snell v6 does not support obfuscation"
        if proxy.get("version", 4) != 6 and obfs_mode not in {"", "none", "http"}:
            return "Surge Snell v4/v5 only supports HTTP obfuscation"
    if proxy.get("plugin") and (
        kind != "shadowsocks"
        or proxy["plugin"] != "obfs"
        or record(proxy.get("plugin-opts")).get("mode") not in {"http", "tls"}
    ):
        return "Surge supports only Shadowsocks simple-obfs plugins"
    if transport == "ws":
        ws = record(proxy.get("ws-opts"))
        headers = record(ws.get("headers"))
        if ws.get("max-early-data") or any(
            not isinstance(value, str) or "|" in key or ":" in key or "|" in value
            for key, value in headers.items()
        ):
            return "Surge cannot represent these WebSocket headers or early data"
    if kind == "hysteria2":
        if proxy.get("up"):
            return "Surge does not expose an upload bandwidth option"
        if proxy.get("obfs") and (
            proxy["obfs"] not in {"salamander", "gecko"} or not proxy.get("obfs-password")
        ):
            return "Surge Hysteria2 requires a supported obfuscator and password"
    try:
        surge_proxy(proxy)
    except (ValueError, TypeError, KeyError):
        return "Proxy parameters cannot be safely represented in Surge"
    return None


def sing_box_hysteria_options(proxy: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if proxy.get("obfs"):
        result["obfs"] = {"type": proxy["obfs"], "password": proxy["obfs-password"]}
    if proxy.get("ports"):
        ports = proxy["ports"]
        values = ports.split(",") if isinstance(ports, str) else ports
        result["server_ports"] = [str(value).strip().replace("-", ":") for value in values]
    for source, target in (("up", "up_mbps"), ("down", "down_mbps")):
        if source in proxy:
            result[target] = proxy[source]
    if proxy.get("hop-interval"):
        value = proxy["hop-interval"]
        result["hop_interval"] = f"{value}s" if isinstance(value, int | float) else value
    return result


def clash_proxy(proxy: dict[str, Any]) -> dict[str, Any]:
    result = public_proxy(proxy)
    kind = protocol(proxy)
    result["type"] = {"shadowsocks": "ss", "socks": "socks5"}.get(kind, kind)
    if kind not in {"http", "mieru"}:
        result.setdefault("udp", True)
    if kind in {"vmess", "vless"}:
        result["uuid"] = proxy.get("uuid") or proxy.get("id")
    if kind == "vmess":
        result.setdefault("cipher", proxy.get("security") or "auto")
        result.setdefault("alterId", 0)
    if kind == "shadowsocks":
        result.setdefault("cipher", proxy.get("method") or "aes-128-gcm")
    if kind == "hysteria2":
        result["password"] = proxy.get("password") or proxy.get("auth")
    tls = sing_box_tls(proxy)
    if tls:
        result["tls"] = True
        name = tls.get("server_name")
        if name:
            result["servername" if kind in {"vless", "vmess"} else "sni"] = name
        if "insecure" in tls:
            result["skip-cert-verify"] = tls["insecure"]
        if "alpn" in tls:
            result["alpn"] = deepcopy(tls["alpn"])
        utls = record(tls.get("utls"))
        if utls.get("enabled") and utls.get("fingerprint"):
            result["client-fingerprint"] = utls["fingerprint"]
    elif isinstance(result.get("tls"), dict):
        result["tls"] = False
    if "network" in result:
        result["network"] = network(proxy)
    if network(proxy) == "httpupgrade":
        options = upgrade_options(proxy)
        headers = deepcopy(record(options.get("headers")))
        if options.get("host"):
            headers["Host"] = options["host"]
        result["network"] = "ws"
        result["ws-opts"] = {
            **record(proxy.get("ws-opts")),
            "path": options.get("path", "/"),
            "headers": headers,
            "v2ray-http-upgrade": True,
        }
        result.pop("http-upgrade-opts", None)
    if protocol(proxy) == "snell" and record(result.get("obfs-opts")).get("mode") in ("none", ""):
        result.pop("obfs-opts", None)
    if kind == "snell":
        result.setdefault("version", 4)
    if protocol(proxy) == "mieru":
        result["transport"] = str(result.get("transport") or "TCP").upper()
    return result


def sing_box_transport(proxy: dict[str, Any]) -> dict[str, Any] | None:
    transport = network(proxy)
    if transport == "tcp":
        return None
    if transport == "grpc":
        options = record(proxy.get("grpc-opts"))
        return {"type": "grpc", "service_name": options.get("grpc-service-name", "")}
    if transport == "h2":
        options = record(proxy.get("h2-opts"))
        return {
            "type": "http",
            **{
                key: deepcopy(options[key]) for key in ("host", "path", "headers") if key in options
            },
        }
    options = record(proxy.get("ws-opts")) if transport == "ws" else upgrade_options(proxy)
    result: dict[str, Any] = {"type": transport, "path": options.get("path", "/")}
    if transport == "ws":
        for source, target in (
            ("headers", "headers"),
            ("max-early-data", "max_early_data"),
            ("early-data-header-name", "early_data_header_name"),
        ):
            if source in options:
                result[target] = deepcopy(options[source])
    else:
        for key in ("host", "headers"):
            if options.get(key):
                result[key] = deepcopy(options[key])
    return result


def sing_box_tls(proxy: dict[str, Any]) -> dict[str, Any] | None:
    required = protocol(proxy) in {"anytls", "trojan", "hysteria2"}
    if (
        proxy.get("tls") is False
        or record(proxy.get("tls")).get("enabled") is False
        or not (proxy.get("tls") or proxy.get("reality-opts") or required)
    ):
        return None
    result: dict[str, Any] = {"enabled": True}
    result.update(deepcopy(record(proxy.get("tls"))))
    name = proxy.get("servername") or proxy.get("sni")
    if name:
        result["server_name"] = name
    if "skip-cert-verify" in proxy:
        result["insecure"] = bool(proxy["skip-cert-verify"])
    if proxy.get("alpn"):
        result["alpn"] = deepcopy(proxy["alpn"])
    reality = record(proxy.get("reality-opts"))
    fingerprint = proxy.get("client-fingerprint") or reality.get("fingerprint")
    if fingerprint:
        result["utls"] = {"enabled": True, "fingerprint": fingerprint}
    if reality:
        result["reality"] = {
            "enabled": True,
            "public_key": reality["public-key"],
            "short_id": reality.get("short-id", ""),
        }
        result.setdefault("utls", {"enabled": True, "fingerprint": "chrome"})
    return result


def uri_server(server: str) -> str:
    return f"[{server}]" if ":" in server and not server.startswith("[") else server


def uri_options(proxy: dict[str, Any]) -> dict[str, Any]:
    tls = sing_box_tls(proxy) or {}
    reality = record(proxy.get("reality-opts"))
    transport = network(proxy)
    result: dict[str, Any] = {
        "type": transport,
        "security": "reality" if reality else "tls" if tls else "none",
        "sni": tls.get("server_name") or proxy.get("servername") or proxy.get("sni"),
        "alpn": ",".join(tls.get("alpn") or []),
        "allowInsecure": "1" if tls.get("insecure") else None,
        "fp": record(tls.get("utls")).get("fingerprint"),
    }
    if reality:
        result.update(
            {
                "pbk": reality["public-key"],
                "sid": reality.get("short-id"),
                "spx": reality.get("spider-x"),
            }
        )
    if transport == "grpc":
        result["serviceName"] = record(proxy.get("grpc-opts")).get("grpc-service-name", "")
    elif transport in {"ws", "httpupgrade"}:
        options = record(proxy.get("ws-opts")) if transport == "ws" else upgrade_options(proxy)
        result["path"] = options.get("path", "/")
        result["host"] = options.get("host") or next(
            (
                value
                for key, value in record(options.get("headers")).items()
                if key.lower() == "host"
            ),
            "",
        )
    return result


def xray_outbound(proxy: dict[str, Any]) -> dict[str, Any]:
    kind = protocol(proxy)
    endpoint = {"address": proxy["server"], "port": int(proxy["port"])}
    result: dict[str, Any] = {"tag": proxy["name"], "protocol": kind}
    settings: dict[str, Any]
    if kind in {"vless", "vmess"}:
        user = {"id": proxy.get("uuid") or proxy.get("id")}
        if kind == "vless":
            user["encryption"] = proxy.get("encryption") or "none"
            if proxy.get("flow"):
                user["flow"] = proxy["flow"]
        else:
            user.update(
                {"security": proxy.get("cipher") or "auto", "alterId": proxy.get("alterId") or 0}
            )
        settings = {"vnext": [{**endpoint, "users": [user]}]}
    elif kind in {"trojan", "shadowsocks"}:
        server = {**endpoint, "password": proxy["password"]}
        if kind == "shadowsocks":
            server["method"] = proxy.get("cipher") or proxy.get("method") or "aes-128-gcm"
        settings = {"servers": [server]}
    elif kind in {"socks", "http"}:
        server = dict(endpoint)
        if proxy.get("username"):
            server["users"] = [{"user": proxy["username"], "pass": proxy.get("password", "")}]
        settings = {"servers": [server]}
    elif kind == "anytls":
        settings = {**endpoint, "password": proxy["password"]}
        for source, target in (
            ("idle-session-check-interval", "idleSessionCheckInterval"),
            ("idle-session-timeout", "idleSessionTimeout"),
            ("min-idle-session", "minIdleSession"),
        ):
            if source in proxy:
                settings[target] = proxy[source]
    elif kind == "snell":
        settings = {**endpoint, "psk": proxy["psk"], "version": proxy.get("version") or 4}
        if settings["version"] == 6:
            settings["v6Mode"] = proxy.get("mode") or "default"
        else:
            options = record(proxy.get("obfs-opts"))
            settings.update(
                {"obfsMode": options.get("mode") or "none", "obfsHost": options.get("host") or ""}
            )
    elif kind == "hysteria2":
        result["protocol"] = "hysteria"
        settings = {**endpoint, "version": 2}
    else:
        raise ValueError("Unsupported Xray outbound protocol")
    result["settings"] = settings
    if proxy.get("dialer-proxy"):
        result["proxySettings"] = {"tag": str(proxy["dialer-proxy"])}
    stream: dict[str, Any] = {"network": network(proxy)}
    tls = sing_box_tls(proxy)
    if tls:
        reality = record(proxy.get("reality-opts"))
        options = {"serverName": tls.get("server_name") or proxy["server"]}
        if reality:
            stream["security"] = "reality"
            options.update(
                {
                    "publicKey": reality["public-key"],
                    "shortId": reality.get("short-id", ""),
                    "fingerprint": record(tls.get("utls")).get("fingerprint") or "chrome",
                }
            )
            if reality.get("spider-x"):
                options["spiderX"] = reality["spider-x"]
            stream["realitySettings"] = options
        else:
            stream["security"] = "tls"
            for source, target in (("insecure", "allowInsecure"), ("alpn", "alpn")):
                if source in tls:
                    options[target] = tls[source]
            if record(tls.get("utls")).get("enabled"):
                options["fingerprint"] = tls["utls"].get("fingerprint", "chrome")
            stream["tlsSettings"] = options
    transport = network(proxy)
    if transport == "ws":
        options = record(proxy.get("ws-opts"))
        stream["wsSettings"] = {
            key: deepcopy(options[key]) for key in ("path", "headers") if key in options
        }
        if options.get("max-early-data"):
            path = urlsplit(options.get("path", "/"))
            query = dict(parse_qsl(path.query, keep_blank_values=True))
            query["ed"] = str(options["max-early-data"])
            stream["wsSettings"]["path"] = urlunsplit(path._replace(query=urlencode(query)))
    elif transport == "grpc":
        stream["grpcSettings"] = {
            "serviceName": record(proxy.get("grpc-opts")).get("grpc-service-name", "")
        }
    elif transport == "httpupgrade":
        stream["httpupgradeSettings"] = upgrade_options(proxy)
    elif transport == "xhttp":
        stream["xhttpSettings"] = deepcopy(record(proxy.get("xhttp-opts")))
    if kind == "hysteria2":
        stream["network"] = "hysteria"
        stream["hysteriaSettings"] = {
            "version": 2,
            "auth": proxy.get("password") or proxy.get("auth"),
        }
    result["streamSettings"] = stream
    return result
