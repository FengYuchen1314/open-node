"""Bounded configuration parsing and client-only subscription template expansion."""

import configparser
import json
import shlex
from copy import deepcopy
from functools import lru_cache

import yaml

from open_node.domain.subscription_templates import MAX_TEMPLATE_BYTES
from open_node.services import subscription_clients as clients

BUILTINS = {
    "DIRECT",
    "REJECT",
    "REJECT-DROP",
    "REJECT-NO-DROP",
    "REJECT-TINYGIF",
    "PASS",
    "GLOBAL",
    "COMPATIBLE",
}
NODE_TOKEN, PROVIDER_TOKEN = "__PROXY_NODES__", "__PROXY_PROVIDERS__"
DEFAULT_CLASH = """mixed-port: 7890
allow-lan: false
mode: rule
log-level: info
proxies: []
proxy-groups:
  - name: Proxy
    type: select
    proxies: [__PROXY_NODES__]
rules:
  - MATCH,Proxy
"""
DEFAULT_SURGE = """[General]
loglevel = notify
allow-wifi-access = false
udp-policy-not-supported-behaviour = reject

[Proxy]

[Proxy Group]
Proxy = select, include-all-proxies=true

[Rule]
FINAL,Proxy
"""


class TemplateError(ValueError):
    pass


def checked_text(value):
    try:
        raw = value.encode("utf-8")
    except UnicodeError as exc:
        raise TemplateError("Template must contain valid UTF-8 text") from exc
    if not value.strip() or len(raw) > MAX_TEMPLATE_BYTES:
        raise TemplateError("Template must contain 1 byte to 2 MiB of text")
    if any(ord(c) < 32 and c not in "\t\r\n" for c in value):
        raise TemplateError("Template contains control characters")


class TemplateLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        explicit = set()
        for key, _ in node.value:
            if key.tag == "tag:yaml.org,2002:merge":
                continue
            value = self.construct_object(key)
            if not isinstance(value, str) or value in explicit:
                raise TemplateError("Template mapping keys must be unique strings")
            explicit.add(value)
        return super().construct_mapping(node, deep=deep)


def checked_yaml(content):
    loader = TemplateLoader(content)
    try:
        root = loader.get_single_node()
        if root is None:
            raise TemplateError("Clash template must contain a YAML mapping")
        remaining = 40000

        def walk(node, ancestors):
            nonlocal remaining
            remaining -= 1
            if remaining < 0 or len(ancestors) > 48 or id(node) in ancestors:
                raise TemplateError("Template aliases or nesting exceed the supported limit")
            children = []
            if isinstance(node, yaml.MappingNode):
                children = [child for pair in node.value for child in pair]
            elif isinstance(node, yaml.SequenceNode):
                children = node.value
            for child in children:
                walk(child, ancestors | {id(node)})

        walk(root, set())
        value = loader.construct_document(root)
        if not isinstance(value, dict):
            raise TemplateError("Clash template must be a YAML mapping")
        return value
    except (yaml.YAMLError, RecursionError, TypeError) as exc:
        raise TemplateError("Invalid or excessively nested YAML template") from exc
    finally:
        loader.dispose()


def surge_sections(content):
    parser = configparser.ConfigParser(
        interpolation=None,
        allow_no_value=True,
        strict=False,
        delimiters=("=",),
        comment_prefixes=("#", ";", "//"),
        empty_lines_in_values=False,
    )
    parser.optionxform = str
    try:
        parser.read_string(content)
        seen, section, chunks = set(), None, []
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                section = stripped[1:-1].strip().casefold()
                if section in seen:
                    raise TemplateError("Surge sections must be distinct")
                seen.add(section)
            chunks.append((section, line))
        if not seen.intersection({"general", "proxy", "proxy group", "rule"}):
            raise TemplateError("Surge template requires a profile section")
        groups = {}
        for name in parser.sections():
            if name.casefold() != "proxy group":
                continue
            strict = configparser.ConfigParser(interpolation=None, strict=True, delimiters=("=",))
            strict.optionxform = str
            strict.read_string(
                "[Groups]\n"
                + "\n".join(
                    line
                    for section, line in chunks
                    if section == "proxy group" and not line.strip().startswith("[")
                )
            )
            for key, value in strict.items("Groups"):
                if not safe_policy_name(key) or key in BUILTINS:
                    raise TemplateError("Surge group name contains reserved syntax")
                parts = surge_parts(value)
                if not parts:
                    raise TemplateError("Surge policy group type is missing")
                groups[key] = parts
        check_cycles(
            {key: [part for part in parts[1:] if part in groups] for key, parts in groups.items()}
        )
        return {"chunks": chunks, "groups": groups}
    except (configparser.Error, ValueError) as exc:
        if isinstance(exc, TemplateError):
            raise
        raise TemplateError("Invalid Surge profile structure") from exc


def surge_parts(value):
    lexer = shlex.shlex(value, posix=True)
    lexer.whitespace = ","
    lexer.whitespace_split = True
    lexer.commenters = ""
    return [part.strip() for part in lexer]


def safe_policy_name(name):
    return bool(name.strip()) and not any(c in name for c in ",=\r\n\"'\\#;[]")


def surge_name(name):
    return "".join("_" if c in ",=\r\n\"'\\#;[]" else c for c in name).strip() or "Node"


def check_cycles(graph):
    visiting, complete = set(), set()

    def visit(name):
        if name in visiting or len(visiting) > 48:
            raise TemplateError("Proxy group references contain a cycle or excessive nesting")
        if name in complete:
            return
        visiting.add(name)
        for other in graph.get(name, []):
            visit(other)
        visiting.remove(name)
        complete.add(name)

    for name in graph:
        visit(name)


@lru_cache(maxsize=32)
def parse_template(content, format):
    checked_text(content)
    if format == "surge":
        return surge_sections(content)
    value = checked_yaml(content)
    groups = value.get("proxy-groups", [])
    if not isinstance(groups, list) or len(groups) > 1000:
        raise TemplateError("proxy-groups must be a list of at most 1000 groups")
    names = set()
    for group in groups:
        if (
            not isinstance(group, dict)
            or not isinstance(group.get("name"), str)
            or not group["name"].strip()
        ):
            raise TemplateError("Every proxy group needs a name")
        if group["name"] in names or group["name"] in BUILTINS:
            raise TemplateError("Proxy group names must be distinct and not reserved")
        names.add(group["name"])
        if not isinstance(group.get("type"), str):
            raise TemplateError("Every proxy group needs a type")
        for key in ("proxies", "use"):
            if key in group and (
                not isinstance(group[key], list)
                or any(not isinstance(item, str) for item in group[key])
            ):
                raise TemplateError("Proxy group members and providers must be string lists")
    if not isinstance(value.get("proxy-providers", {}), dict):
        raise TemplateError("proxy-providers must be a mapping")
    if not isinstance(value.get("rules", []), list) or any(
        not isinstance(rule, str) for rule in value.get("rules", [])
    ):
        raise TemplateError("Rules must be a string list")
    check_cycles(
        {
            group["name"]: [name for name in group.get("proxies", []) if name in names]
            for group in groups
        }
    )
    return value


def reserved_names(content, format):
    parsed = parse_template(content, format)
    return (
        set(parsed["groups"])
        if format == "surge"
        else {group["name"] for group in parsed.get("proxy-groups", [])}
    )


def render_clash(content, proxies):
    value = deepcopy(parse_template(content, "clash"))
    groups = value.get("proxy-groups", [])
    group_names = {group["name"] for group in groups}
    generated_groups = [
        deepcopy(group)
        for proxy in proxies
        for group in proxy.get("_open_node_proxy_groups", [])
    ]
    generated_names = [str(group.get("name") or "") for group in generated_groups]
    if (
        any(not name for name in generated_names)
        or len(set(generated_names)) != len(generated_names)
        or set(generated_names).intersection(BUILTINS | group_names)
    ):
        raise TemplateError("Generated topology group names must be distinct")
    groups.extend(generated_groups)
    group_names.update(generated_names)
    if any(
        not isinstance(proxy, dict)
        or not isinstance(proxy.get("name"), str)
        or not proxy["name"].strip()
        for proxy in proxies
    ):
        raise TemplateError("Rendered proxies require names")
    names = [proxy["name"] for proxy in proxies]
    visible_names = [
        proxy["name"] for proxy in proxies if not proxy.get("_open_node_hidden")
    ]
    has_hidden = len(visible_names) != len(names)
    if len(set(names)) != len(names) or set(names).intersection(BUILTINS | group_names):
        raise TemplateError("Rendered proxy names must be distinct from policy groups")
    value["proxies"] = [clients.clash_proxy(proxy) for proxy in proxies]
    known = set(names) | {group["name"] for group in groups} | BUILTINS
    providers = list(value.get("proxy-providers", {}))
    warnings = []
    for group in groups:
        members, use = [], list(group.get("use", []))
        for member in group.get("proxies", []):
            if member == NODE_TOKEN:
                if (
                    not has_hidden
                    and (
                        group.get("filter")
                        or group.get("exclude-filter")
                        or group.get("exclude-type")
                    )
                ):
                    group["include-all-proxies"] = True
                else:
                    members.extend(visible_names)
                    if has_hidden and (
                        group.get("filter")
                        or group.get("exclude-filter")
                        or group.get("exclude-type")
                    ):
                        warnings.append(
                            "Topology helper proxies are hidden; template proxy filters "
                            "were not applied to the node placeholder"
                        )
            elif member == PROVIDER_TOKEN:
                use.extend(providers)
            else:
                if member not in known:
                    raise TemplateError("Template references a missing proxy or group: " + member)
                members.append(member)
        if any(name not in providers for name in use):
            raise TemplateError("Template references an unknown proxy provider")
        if (
            not members
            and not use
            and not any(
                group.get(key)
                for key in ("include-all", "include-all-proxies", "include-all-providers")
            )
        ):
            members = ["REJECT"]
            warnings.append("Empty group uses REJECT: " + group["name"])
        group["proxies"] = list(dict.fromkeys(members))
        if use:
            group["use"] = list(dict.fromkeys(use))
    result = yaml.safe_dump(value, allow_unicode=True, sort_keys=False)
    if len(result.encode()) > 8 * 1024 * 1024:
        raise TemplateError("Rendered subscription exceeds 8 MiB")
    return result, warnings


def validate_stash_template(content):
    value = parse_template(content, "clash")
    error = "Selected Clash template is not compatible with Stash"
    if value.keys() - {
        "proxies", "proxy-groups", "proxy-providers", "rules", "rule-providers", "dns",
        "hosts", "mode", "log-level", "ipv6", "mixed-port", "port", "socks-port", "allow-lan",
    }:
        raise TemplateError(error)
    providers = value.get("rule-providers", {})
    if not isinstance(providers, dict):
        raise TemplateError(error)
    for provider in providers.values():
        if not isinstance(provider, dict) or provider.get("format") == "mrs":
            raise TemplateError(error)
        if any(str(provider.get(key, "")).endswith(".mrs") for key in ("url", "path")):
            # A different extension is not evidence that a matching YAML resource exists.
            raise TemplateError(error)
    dns = value.get("dns", {})
    if not isinstance(dns, dict) or dns.keys() - {
        "default-nameserver", "nameserver", "nameserver-policy", "direct-nameserver",
        "proxy-server-nameserver", "fake-ip-filter", "skip-cert-verify",
    }:
        raise TemplateError(error)
    if "skip-cert-verify" in dns and type(dns["skip-cert-verify"]) is not bool:
        raise TemplateError(error)
    for key in ("default-nameserver", "nameserver", "direct-nameserver", "proxy-server-nameserver"):
        if key in dns and (
            not isinstance(dns[key], list) or any(not isinstance(item, str) for item in dns[key])
        ):
            raise TemplateError(error)
    policy = dns.get("nameserver-policy", {})
    if not isinstance(policy, dict):
        raise TemplateError(error)
    for key, servers in policy.items():
        if key.startswith(("geosite:", "rule-set:")) or not (
            isinstance(servers, str)
            or isinstance(servers, list) and len(servers) == 1 and isinstance(servers[0], str)
        ):
            raise TemplateError(error)


def render_stash(content, proxies):
    from open_node.services.subscription_extra_clients import yaml_proxy

    validate_stash_template(content)
    rendered, warnings = render_clash(content, proxies)
    value = checked_yaml(rendered)
    value["proxies"] = [
        yaml_proxy(clients.public_proxy(proxy), "stash") for proxy in proxies
    ]
    if "dns" in value:
        dns = value["dns"]
        nameservers = list(dns.get("nameserver", []))
        for key in ("direct-nameserver", "proxy-server-nameserver"):
            nameservers.extend(dns.pop(key, []))
        if nameservers:
            dns["nameserver"] = list(dict.fromkeys(nameservers))
        if "nameserver-policy" in dns:
            dns["nameserver-policy"] = {
                key: servers[0] if isinstance(servers, list) else servers
                for key, servers in dns["nameserver-policy"].items()
            }
    # Unlike the reference's default, never turn off DNS TLS verification implicitly.
    result = yaml.safe_dump(value, allow_unicode=True, sort_keys=False)
    if len(result.encode()) > 8 * 1024 * 1024:
        raise TemplateError("Rendered subscription exceeds 8 MiB")
    return result, warnings


def surge_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    value = str(value)
    if any(ord(c) < 32 or 127 <= ord(c) <= 159 or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise TemplateError("Proxy parameter contains control characters")
    return (
        json.dumps(value, ensure_ascii=False)
        if any(c in value for c in ",\"'\\#;") or value != value.strip()
        else value
    )


def surge_proxy(proxy):
    kind, network = clients.protocol(proxy), clients.network(proxy)
    if not safe_policy_name(str(proxy.get("name") or "")):
        raise TemplateError("Surge proxy name contains reserved syntax")
    if not isinstance(proxy.get("server"), str) or not proxy["server"].strip():
        raise TemplateError("Surge proxy server is missing")
    port = proxy.get("port")
    if not (type(port) is int or isinstance(port, str) and port.isascii() and port.isdigit()):
        raise TemplateError("Surge proxy port is invalid")
    if not 1 <= int(port) <= 65535:
        raise TemplateError("Surge proxy port is invalid")
    tls = clients.sing_box_tls(proxy)
    type_name = {"shadowsocks": "ss", "socks": "socks5"}.get(kind, kind)
    if tls and kind in {"http", "socks"}:
        type_name = "https" if kind == "http" else "socks5-tls"
    parameters = {}
    if kind == "snell":
        parameters |= {"psk": proxy["psk"], "version": proxy.get("version", 4)}
        if parameters["version"] == 6:
            parameters["mode"] = proxy.get("mode") or "default"
        obfs = clients.record(proxy.get("obfs-opts"))
        if obfs.get("mode") not in (None, "", "none"):
            parameters["obfs"] = obfs["mode"]
            if obfs.get("host"):
                parameters["obfs-host"] = obfs["host"]
    elif kind == "vmess":
        parameters |= {
            "username": proxy.get("uuid") or proxy.get("id"),
            "vmess-aead": proxy.get("alterId", 0) == 0,
            "encrypt-method": "aes-128-gcm"
            if (proxy.get("cipher") or "auto") == "auto"
            else proxy["cipher"],
        }
        if tls:
            parameters["tls"] = True
    elif kind == "shadowsocks":
        parameters |= {
            "encrypt-method": proxy.get("cipher") or proxy.get("method") or "aes-128-gcm",
            "password": proxy["password"],
            "udp-relay": proxy.get("udp", True),
        }
        if proxy.get("plugin"):
            obfs = clients.record(proxy.get("plugin-opts"))
            parameters["obfs"] = obfs["mode"]
            if obfs.get("host"):
                parameters["obfs-host"] = obfs["host"]
    elif kind in {"http", "socks"}:
        for key in ("username", "password"):
            if proxy.get(key) is not None:
                parameters[key] = proxy[key]
        if kind == "socks":
            parameters["udp-relay"] = proxy.get("udp", True)
    else:
        parameters["password"] = proxy.get("password") or proxy.get("auth")
    if kind == "hysteria2":
        if proxy.get("down"):
            parameters["download-bandwidth"] = proxy["down"]
        if proxy.get("ports"):
            ports = proxy["ports"]
            parameters["port-hopping"] = ";".join(
                ports.split(",") if isinstance(ports, str) else map(str, ports)
            )
        if proxy.get("hop-interval"):
            parameters["port-hopping-interval"] = proxy["hop-interval"]
        if proxy.get("obfs"):
            parameters[proxy["obfs"] + "-password"] = proxy["obfs-password"]
    if network == "ws":
        ws = clients.record(proxy.get("ws-opts"))
        parameters |= {"ws": True, "ws-path": ws.get("path") or "/"}
        if ws.get("headers"):
            parameters["ws-headers"] = "|".join(
                f"{key}:{value}" for key, value in ws["headers"].items()
            )
    if tls:
        for key, target in (("server_name", "sni"), ("insecure", "skip-cert-verify")):
            if key in tls:
                parameters[target] = tls[key]
        if tls.get("alpn"):
            parameters["alpn"] = ",".join(tls["alpn"])
    for key, target in (
        ("fingerprint", "server-cert-fingerprint-sha256"),
        ("name-cert-verify", "server-cert-verify-name"),
        ("dialer-proxy", "underlying-proxy"),
        ("tfo", "tfo"),
    ):
        if key in proxy:
            parameters[target] = proxy[key]
    fields = [type_name, surge_value(proxy["server"]), str(proxy["port"])]
    fields.extend(key + "=" + surge_value(value) for key, value in parameters.items())
    return proxy["name"] + " = " + ", ".join(fields)


def render_surge(content, proxies):
    parsed = parse_template(content, "surge")
    generated = [surge_proxy(proxy) for proxy in proxies]
    names = {proxy["name"] for proxy in proxies}
    if len(names) != len(proxies) or names.intersection(parsed["groups"]):
        raise TemplateError("Rendered proxy names must be distinct from policy groups")
    known = names | set(parsed["groups"]) | BUILTINS
    for group, parts in parsed["groups"].items():
        for member in parts[1:]:
            if "=" not in member and member not in known:
                raise TemplateError(
                    f"Surge group {group} references a missing proxy or group: {member}"
                )
    output, injected = [], False
    for section, line in parsed["chunks"]:
        stripped = line.strip()
        if section == "proxy":
            if stripped.startswith("["):
                output.append(line)
                output.extend(generated)
                injected = True
            elif not stripped or stripped.startswith(("#", ";", "//")):
                output.append(line)
        else:
            output.append(line)
    if not injected:
        output.extend(["", "[Proxy]", *generated])
    result = "\n".join(output).rstrip() + "\n"
    if len(result.encode()) > 8 * 1024 * 1024:
        raise TemplateError("Rendered subscription exceeds 8 MiB")
    return result, []


def render(content, format, proxies):
    return render_surge(content, proxies) if format == "surge" else render_clash(content, proxies)
