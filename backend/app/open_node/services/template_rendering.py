"""Bounded configuration parsing and client-only subscription template expansion."""

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


def portable_name(name):
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
    if format != "clash":
        raise TemplateError("Only Clash/Mihomo YAML templates are supported")
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
    return {group["name"] for group in parsed.get("proxy-groups", [])}


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


def render(content, format, proxies):
    if format != "clash":
        raise TemplateError("Only Clash/Mihomo YAML templates are supported")
    return render_clash(content, proxies)
