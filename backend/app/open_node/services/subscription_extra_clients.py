"""Client-specific, data-only node exports.

Wire fields follow MMWX's pinned proxyparser/substore v0.1.7 producers. These are
independent serializers, not aliases to Clash or URI output. Unsupported
extensions fail the existing per-node preview; they are never silently stripped.
Loon/QX/Surfboard and Shadowrocket/Egern are node subscriptions, not translations
of an arbitrary Clash rule configuration. Stash template handling lives alongside
the existing bounded template renderer.
"""

import json
from copy import deepcopy

import yaml

from open_node.services import subscription_clients as clients

OPTION_ERROR = "Client format cannot represent these proxy options"
TLS_ERROR = "Client format cannot represent these TLS options"
VALUE_ERROR = "Client format cannot represent these credentials safely"
SS_ERROR = "Client format cannot represent this Shadowsocks cipher"
VMESS_ERROR = "Client format cannot represent this VMess cipher or legacy mode"
SNELL_ERROR = "Client format does not support this Snell version"
YAML_CLIENTS = {"stash", "shadowrocket"}
SS_CIPHERS = {
    "aes-128-gcm",
    "aes-256-gcm",
    "chacha20-ietf-poly1305",
    "2022-blake3-aes-128-gcm",
    "2022-blake3-aes-256-gcm",
}
FIELDS = {
    "name",
    "type",
    "server",
    "port",
    "uuid",
    "id",
    "password",
    "auth",
    "username",
    "cipher",
    "method",
    "security",
    "alterId",
    "encryption",
    "network",
    "tls",
    "servername",
    "sni",
    "skip-cert-verify",
    "alpn",
    "client-fingerprint",
    "fingerprint",
    "reality-opts",
    "flow",
    "ws-opts",
    "grpc-opts",
    "tfo",
    "udp",
    "plugin",
    "plugin-opts",
    "psk",
    "version",
    "obfs-opts",
    "obfs",
    "obfs-password",
    "up",
    "down",
    "ports",
    "hop-interval",
    "idle-session-check-interval",
    "idle-session-timeout",
    "min-idle-session",
}


def unsupported_reason(proxy, target):
    kind, transport = clients.protocol(proxy), clients.network(proxy)
    supported = {"vless", "vmess", "trojan", "shadowsocks", "hysteria2", "anytls", "http", "socks"}
    if target in {"shadowrocket", "surfboard", "egern"}:
        supported.add("snell")
    if target == "surfboard":
        supported.discard("vless")
    if target == "quantumult-x":
        supported.discard("hysteria2")
    if kind not in supported:
        return "Protocol is not supported by this client format"
    if proxy.keys() - FIELDS:
        return OPTION_ERROR
    transports = {"tcp", "ws"}
    if target in YAML_CLIENTS or target == "egern" and kind in {"vless", "vmess"}:
        transports.add("grpc")
    if transport not in transports:
        return "Transport is not supported by this client format"
    tls = clients.sing_box_tls(proxy)
    if any(key in proxy and type(proxy[key]) is not bool for key in ("udp", "tfo")):
        return OPTION_ERROR
    if proxy.get("encryption") not in (None, "", "none"):
        return OPTION_ERROR
    if proxy.get("flow") not in (None, "", "xtls-rprx-vision"):
        return OPTION_ERROR
    if proxy.get("flow") and (kind != "vless" or transport != "tcp" or not tls):
        return OPTION_ERROR
    reality = clients.record(proxy.get("reality-opts"))
    if reality and (
        target == "surfboard" or transport != "tcp" or reality.keys() - {"public-key", "short-id"}
    ):
        return TLS_ERROR
    if target not in YAML_CLIENTS and (
        proxy.get("fingerprint")
        or proxy.get("client-fingerprint")
        # Only reject explicit customization, not the sing-box helper's own
        # default browser fingerprint synthesized for a REALITY connection.
        or clients.record(clients.record(proxy.get("tls")).get("utls")).get("enabled")
    ):
        return TLS_ERROR
    alpn = clients.record(tls).get("alpn")
    if alpn and (
        not isinstance(alpn, list)
        or any(not isinstance(item, str) for item in alpn)
        or target in {"egern", "surfboard"}
        or target == "quantumult-x"
        and len(alpn) != 1
    ):
        return TLS_ERROR
    if tls and kind in {"http", "socks"} and target == "egern":
        return TLS_ERROR
    if tls and kind == "shadowsocks" and target != "shadowrocket":
        return TLS_ERROR
    if transport == "ws":
        ws = clients.record(proxy.get("ws-opts"))
        if ws.keys() - {"path", "headers"}:
            return "Custom transport headers or early data require native client export"
        headers = clients.record(ws.get("headers"))
        if any(
            not isinstance(key, str) or not isinstance(value, str) for key, value in headers.items()
        ):
            return OPTION_ERROR
        if target not in YAML_CLIENTS and any(key.lower() != "host" for key in headers):
            return "Custom transport headers or early data require native client export"
    if transport == "grpc":
        grpc = clients.record(proxy.get("grpc-opts"))
        if grpc.keys() - {"grpc-service-name"} or target == "egern" and not tls:
            return OPTION_ERROR
    if kind == "shadowsocks":
        if (proxy.get("cipher") or proxy.get("method") or "aes-128-gcm") not in SS_CIPHERS:
            return SS_ERROR
    if proxy.get("plugin"):
        options = clients.record(proxy.get("plugin-opts"))
        if (
            kind != "shadowsocks"
            or proxy["plugin"] != "obfs"
            or options.get("mode") not in {"http", "tls"}
            or options.keys() - {"mode", "host", "path"}
            or target == "loon"
            and str(proxy.get("cipher", "")).startswith("2022-")
        ):
            return OPTION_ERROR
    if kind == "snell":
        if proxy.get("version", 4) not in (4, 5):
            return SNELL_ERROR
        if clients.record(proxy.get("obfs-opts")).keys() - {"mode", "host"}:
            return OPTION_ERROR
    if kind == "vmess":
        cipher = proxy.get("cipher") or proxy.get("security") or "auto"
        if cipher not in {
            "auto",
            "aes-128-gcm",
            "chacha20-ietf-poly1305",
            "chacha20-poly1305",
            "none",
        }:
            return VMESS_ERROR
        if target == "surfboard" and cipher != "auto":
            return VMESS_ERROR
        if target not in YAML_CLIENTS and proxy.get("alterId", 0) != 0:
            return VMESS_ERROR
    if kind == "hysteria2" and target not in YAML_CLIENTS:
        if proxy.get("ports") or proxy.get("hop-interval"):
            return OPTION_ERROR
        if target == "egern" and proxy.get("down") or target != "egern" and proxy.get("up"):
            return OPTION_ERROR
        if proxy.get("obfs") and (
            target == "surfboard" or proxy["obfs"] != "salamander" or not proxy.get("obfs-password")
        ):
            return OPTION_ERROR
    if any(
        key in proxy and (type(proxy[key]) is not int or proxy[key] < 0) for key in ("up", "down")
    ):
        return OPTION_ERROR
    if target not in YAML_CLIENTS and any(
        key in proxy
        for key in ("idle-session-check-interval", "idle-session-timeout", "min-idle-session")
    ):
        return OPTION_ERROR
    try:
        converted = convert_proxy(proxy, target)
        if target in clients.TEXT_NODE_FORMATS:
            if not isinstance(converted, str):
                return VALUE_ERROR
        else:
            json.dumps(converted, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (KeyError, TypeError, ValueError):
        return VALUE_ERROR
    return None


def atom(value):
    """Only unambiguous native comma-delimited values; never interpolate a new option."""
    if type(value) is bool:
        return "true" if value else "false"
    if not isinstance(value, str | int) or isinstance(value, bool):
        raise ValueError(VALUE_ERROR)
    value = str(value)
    if value != value.strip() or any(
        char in ",\"'\\#;"
        or ord(char) < 32
        or 127 <= ord(char) <= 159
        or 0xD800 <= ord(char) <= 0xDFFF
        for char in value
    ):
        raise ValueError(VALUE_ERROR)
    return value


def _quoted(value):
    return json.dumps(atom(value), ensure_ascii=False)


def _ws(proxy):
    options = clients.record(proxy.get("ws-opts"))
    headers = clients.record(options.get("headers"))
    host = next((value for key, value in headers.items() if key.lower() == "host"), None)
    return options.get("path") or "/", host


def _native_tls(proxy, target):
    tls = clients.sing_box_tls(proxy)
    if not tls:
        return {}
    result = {}
    sni_key = "tls-name" if target == "loon" else "tls-host" if target == "quantumult-x" else "sni"
    if target == "loon" and (
        proxy.get("reality-opts") or clients.protocol(proxy) in {"http", "socks"}
    ):
        sni_key = "sni"
    if tls.get("server_name"):
        result[sni_key] = tls["server_name"]
    if "insecure" in tls:
        key = "tls-verification" if target == "quantumult-x" else "skip-cert-verify"
        result[key] = not tls["insecure"] if target == "quantumult-x" else tls["insecure"]
    if tls.get("alpn") and target == "quantumult-x":
        result["tls-alpn"] = tls["alpn"][0]
    return result


def _native(proxy, target):
    kind, transport = clients.protocol(proxy), clients.network(proxy)
    tls = clients.sing_box_tls(proxy)
    cipher = proxy.get("cipher") or proxy.get("method") or "aes-128-gcm"
    identifier = proxy.get("uuid") or proxy.get("id")
    password = proxy.get("password") or proxy.get("auth")
    protocol = {"shadowsocks": "ss", "socks": "socks5"}.get(kind, kind)
    params = {}
    if target == "quantumult-x":
        protocol = "shadowsocks" if kind == "shadowsocks" else protocol
        server = atom(proxy["server"])
        endpoint = f"[{server}]" if ":" in server and not server.startswith("[") else server
        fields = [f"{protocol}={endpoint}:{int(proxy['port'])}"]
        if kind in {"vmess", "vless"}:
            method = proxy.get("cipher") or proxy.get("security") or "auto"
            params["method"] = (
                "none"
                if kind == "vless"
                else (
                    "chacha20-ietf-poly1305" if method in {"auto", "chacha20-poly1305"} else method
                )
            )
            params["password"] = identifier
            if kind == "vmess":
                params["aead"] = True
        elif kind == "shadowsocks":
            params |= {"method": cipher, "password": password}
        elif kind in {"http", "socks"}:
            params.update({key: proxy[key] for key in ("username", "password") if key in proxy})
        else:
            params["password"] = password
        if transport == "ws":
            params["obfs"] = "wss" if tls else "ws"
            path, host = _ws(proxy)
            params["obfs-uri"] = path
            if host:
                params["obfs-host"] = host
        elif tls:
            params["obfs" if kind in {"vless", "vmess"} else "over-tls"] = (
                "over-tls" if kind in {"vless", "vmess"} else True
            )
        if proxy.get("flow"):
            params["vless-flow"] = proxy["flow"]
        if proxy.get("reality-opts"):
            params["reality-base64-pubkey"] = proxy["reality-opts"]["public-key"]
            if proxy["reality-opts"].get("short-id"):
                params["reality-hex-shortid"] = proxy["reality-opts"]["short-id"]
    else:
        if target == "loon":
            protocol = {"shadowsocks": "shadowsocks", "hysteria2": "Hysteria2"}.get(kind, protocol)
        if tls and kind in {"http", "socks"}:
            if kind == "http":
                protocol = "https"
            elif target == "surfboard":
                protocol = "socks5-tls"
        fields = [
            f"{atom(proxy['name'])}={protocol}",
            atom(proxy["server"]),
            str(int(proxy["port"])),
        ]
        if kind in {"http", "socks"}:
            if "username" in proxy or "password" in proxy:
                fields.extend([atom(proxy.get("username", "")), _quoted(proxy.get("password", ""))])
        elif target == "loon":
            if kind in {"vmess", "shadowsocks"}:
                fields.append(
                    atom(
                        (proxy.get("cipher") or proxy.get("security") or "auto")
                        if kind == "vmess"
                        else cipher
                    )
                )
            fields.append(_quoted(identifier if kind in {"vless", "vmess"} else password))
        elif kind == "shadowsocks":
            params |= {"encrypt-method": cipher, "password": password}
        elif kind == "vmess":
            params |= {"username": identifier, "vmess-aead": True}
        elif kind == "snell":
            params |= {"psk": proxy["psk"], "version": proxy.get("version", 4)}
        else:
            params["password"] = password
        if kind in {"vless", "vmess", "trojan"}:
            if target == "loon":
                params["transport"] = transport
            if transport == "ws":
                path, host = _ws(proxy)
                params["path" if target == "loon" else "ws-path"] = path
                if target == "surfboard":
                    params["ws"] = True
                if host:
                    params["host" if target == "loon" else "ws-headers"] = (
                        host if target == "loon" else "Host:" + host
                    )
        if kind in {"vmess", "vless"} or kind == "socks" and target == "loon":
            params["over-tls" if target == "loon" else "tls"] = bool(tls)
        if kind == "vmess" and target == "loon":
            params["alterId"] = proxy.get("alterId", 0)
        if proxy.get("flow"):
            params["flow"] = proxy["flow"]
        if proxy.get("reality-opts"):
            params["public-key"] = proxy["reality-opts"]["public-key"]
            if proxy["reality-opts"].get("short-id"):
                params["short-id"] = proxy["reality-opts"]["short-id"]
    params.update(_native_tls(proxy, target))
    for source, key in (
        (
            "tfo",
            "fast-open"
            if target == "quantumult-x" or target == "loon" and kind not in {"http", "socks"}
            else "tfo",
        ),
        ("udp", "udp" if target == "loon" else "udp-relay"),
    ):
        if source in proxy:
            params[key] = proxy[source]
    obfs = clients.record(proxy.get("plugin-opts") or proxy.get("obfs-opts"))
    if obfs.get("mode") and obfs["mode"] != "none":
        params["obfs-name" if target == "loon" else "obfs"] = obfs["mode"]
        for source, key in (("host", "obfs-host"), ("path", "obfs-uri")):
            if source in obfs:
                params[key] = obfs[source]
    if kind == "hysteria2":
        if "down" in proxy:
            params["download-bandwidth"] = proxy["down"]
        if proxy.get("obfs"):
            params["salamander-password"] = proxy["obfs-password"]
    if target == "quantumult-x":
        params["tag"] = proxy["name"]
    fields.extend(key + "=" + atom(value) for key, value in params.items())
    if target == "loon" and clients.record(tls).get("alpn"):
        values = [atom(item) for item in tls["alpn"]]
        fields.append("alpn=" + json.dumps(",".join(values), ensure_ascii=False))
    return ",".join(fields)


def yaml_proxy(proxy, target):
    value = clients.clash_proxy(proxy)
    kind = clients.protocol(proxy)
    for key in ("id", "method", "security"):
        value.pop(key, None)
    if kind == "vmess":
        cipher = proxy.get("cipher") or proxy.get("security") or "auto"
        value["cipher"] = "chacha20-poly1305" if cipher == "chacha20-ietf-poly1305" else cipher
    if kind in {"trojan", "hysteria2", "anytls"}:
        value.pop("tls", None)
    if kind == "hysteria2":
        value.pop("auth", None)
        if target == "stash":
            value["auth"] = value.pop("password")
            for source, key in (("up", "up-speed"), ("down", "down-speed"), ("tfo", "fast-open")):
                if source in value:
                    value[key] = (
                        str(value.pop(source)) if source in {"up", "down"} else value.pop(source)
                    )
    return value


def _egern(proxy):
    kind, transport = clients.protocol(proxy), clients.network(proxy)
    tls = clients.sing_box_tls(proxy)
    result = {key: deepcopy(proxy[key]) for key in ("name", "server", "port")}
    result["port"] = int(result["port"])
    if kind in {"vless", "vmess"}:
        result["user_id"] = proxy.get("uuid") or proxy.get("id")
        if kind == "vmess":
            cipher = proxy.get("cipher") or proxy.get("security") or "auto"
            result["security"] = (
                "chacha20-poly1305" if cipher == "chacha20-ietf-poly1305" else cipher
            )
        elif proxy.get("flow"):
            result["flow"] = proxy["flow"]
    elif kind in {"http", "socks"}:
        result.update({key: proxy[key] for key in ("username", "password") if key in proxy})
    elif kind == "snell":
        result |= {"psk": proxy["psk"], "version": proxy.get("version", 4)}
    else:
        result["auth" if kind == "hysteria2" else "password"] = proxy.get("password") or proxy.get(
            "auth"
        )
    if kind == "shadowsocks":
        cipher = proxy.get("cipher") or proxy.get("method") or "aes-128-gcm"
        result["method"] = "chacha20-poly1305" if cipher == "chacha20-ietf-poly1305" else cipher
    tls_options = {}
    if tls:
        if tls.get("server_name"):
            tls_options["sni"] = tls["server_name"]
        if "insecure" in tls:
            tls_options["skip_tls_verify"] = tls["insecure"]
        if proxy.get("reality-opts"):
            tls_options["reality"] = {
                key.replace("-", "_"): value for key, value in proxy["reality-opts"].items()
            }
    if kind in {"vless", "vmess"}:
        if transport == "ws":
            path, host = _ws(proxy)
            options = {"path": path, **tls_options}
            if host:
                options["headers"] = {"Host": host}
            result["transport"] = {"wss" if tls else "ws": options}
        elif transport == "grpc":
            result["transport"] = {
                "grpc": {
                    "service_name": clients.record(proxy.get("grpc-opts")).get(
                        "grpc-service-name", ""
                    ),
                    **tls_options,
                }
            }
        elif tls:
            result["transport"] = {"tls": tls_options}
        elif kind == "vless":
            result["transport"] = {"tcp": {}}
    elif tls:
        result.update(tls_options)
    if kind == "trojan" and transport == "ws":
        path, host = _ws(proxy)
        result["websocket"] = {"path": path, **({"host": host} if host else {})}
    for source, key in (("udp", "udp_relay"), ("tfo", "tfo")):
        if source in proxy:
            result[key] = proxy[source]
    obfs = clients.record(proxy.get("plugin-opts") or proxy.get("obfs-opts"))
    if obfs.get("mode") and obfs["mode"] != "none":
        result["obfs"] = obfs["mode"]
        for source, key in (("host", "obfs_host"), ("path", "obfs_uri")):
            if source in obfs:
                result[key] = obfs[source]
    if kind == "hysteria2":
        if "up" in proxy:
            result["bandwidth"] = proxy["up"]
        if proxy.get("obfs"):
            result |= {"obfs": "salamander", "obfs_password": proxy["obfs-password"]}
    return {"socks5" if kind == "socks" else kind: result}


def convert_proxy(proxy, target):
    if target in clients.TEXT_NODE_FORMATS:
        return _native(proxy, target)
    if target in YAML_CLIENTS:
        return yaml_proxy(proxy, target)
    if target == "egern":
        return _egern(proxy)
    raise ValueError(OPTION_ERROR)


def render_nodes(proxies, target):
    converted = [convert_proxy(proxy, target) for proxy in proxies]
    if target in clients.TEXT_NODE_FORMATS:
        return "\n".join(converted) + "\n", "text/plain; charset=utf-8", "conf"
    return (
        yaml.safe_dump({"proxies": converted}, allow_unicode=True, sort_keys=False),
        "text/yaml; charset=utf-8",
        "yaml",
    )
