"""Durable subscription rules and snapshot-backed Clash proxy providers."""

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import UTC, datetime
from urllib.parse import quote
from uuid import uuid4

import yaml
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from open_node.domain.subscription_customizations import (
    CustomRuleCreate,
    CustomRuleRead,
    CustomRuleUpdate,
    ProxyProviderCreate,
    ProxyProviderRead,
    ProxyProviderUpdate,
)
from open_node.services.inventory import Base, ProductUserModel

MAX_PARSED_ITEMS = 20_000
MAX_RENDERED_BYTES = 8 * 1024 * 1024
BUILTIN_POLICIES = {
    "DIRECT", "REJECT", "REJECT-DROP", "PASS", "GLOBAL", "COMPATIBLE", "PROXY"
}
HEADER_NAME = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+\Z")


class CustomizationLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        keys = set()
        for key_node, _value_node in node.value:
            key = self.construct_object(key_node, deep=False)
            if not isinstance(key, str) or key in keys:
                raise SubscriptionCustomizationError(
                    "YAML mapping keys must be unique strings"
                )
            keys.add(key)
        return super().construct_mapping(node, deep=deep)


class SubscriptionCustomizationError(ValueError):
    status_code = 422


class SubscriptionCustomizationNotFound(SubscriptionCustomizationError):
    status_code = 404


class SubscriptionCustomizationConflict(SubscriptionCustomizationError):
    status_code = 409


class CustomRuleModel(Base):
    __tablename__ = "subscription_custom_rules"
    __table_args__ = (
        UniqueConstraint("owner_username", "name_key", name="uq_custom_rule_owner_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_username: Mapped[str] = mapped_column(
        ForeignKey("product_users.username", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    name_key: Mapped[str] = mapped_column(String(120))
    type: Mapped[str] = mapped_column(String(24), index=True)
    mode: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProxyProviderModel(Base):
    __tablename__ = "subscription_proxy_providers"
    __table_args__ = (
        UniqueConstraint("owner_username", "name_key", name="uq_proxy_provider_owner_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_username: Mapped[str] = mapped_column(
        ForeignKey("product_users.username", ondelete="CASCADE"), index=True
    )
    external_source_id: Mapped[str] = mapped_column(
        ForeignKey("external_subscription_sources.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    name_key: Mapped[str] = mapped_column(String(120))
    type: Mapped[str] = mapped_column(String(16), default="http")
    interval: Mapped[int] = mapped_column(Integer, default=3600)
    proxy: Mapped[str] = mapped_column(String(120), default="DIRECT")
    size_limit: Mapped[int] = mapped_column(Integer, default=0)
    header: Mapped[dict] = mapped_column(JSON, default=dict)
    health_check_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    health_check_url: Mapped[str] = mapped_column(String(2048))
    health_check_interval: Mapped[int] = mapped_column(Integer, default=300)
    health_check_timeout: Mapped[int] = mapped_column(Integer, default=5000)
    health_check_lazy: Mapped[bool] = mapped_column(Boolean, default=True)
    health_check_expected_status: Mapped[int] = mapped_column(Integer, default=204)
    filter: Mapped[str] = mapped_column(Text, default="")
    exclude_filter: Mapped[str] = mapped_column(Text, default="")
    exclude_type: Mapped[str] = mapped_column(Text, default="")
    geo_ip_filter: Mapped[str] = mapped_column(Text, default="")
    override: Mapped[dict] = mapped_column(JSON, default=dict)
    process_mode: Mapped[str] = mapped_column(String(16), default="client")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def _name(value: str) -> str:
    result = value.strip()
    if not result or any(ord(char) < 32 for char in result):
        raise SubscriptionCustomizationError("Name is required")
    return result


def _bounded_yaml(content: str):
    loader = CustomizationLoader(content)
    try:
        root = loader.get_single_node()
        if root is None:
            raise SubscriptionCustomizationError("Rule content is empty")
        count = 0
        seen = set()

        def walk_node(node, depth=0):
            nonlocal count
            count += 1
            if count > MAX_PARSED_ITEMS or depth > 50 or id(node) in seen:
                raise SubscriptionCustomizationError(
                    "YAML aliases or nesting exceed the supported limit"
                )
            seen.add(id(node))
            children = []
            if isinstance(node, yaml.MappingNode):
                children = [child for pair in node.value for child in pair]
            elif isinstance(node, yaml.SequenceNode):
                children = list(node.value)
            for child in children:
                walk_node(child, depth + 1)

        walk_node(root)
        value = loader.construct_document(root)
    except SubscriptionCustomizationError:
        raise
    except (yaml.YAMLError, RecursionError):
        raise SubscriptionCustomizationError("Rule content is not valid YAML") from None
    finally:
        loader.dispose()
    count = 0

    def visit(item, depth=0):
        nonlocal count
        count += 1
        if count > MAX_PARSED_ITEMS or depth > 50:
            raise SubscriptionCustomizationError("Rule content is too complex")
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise SubscriptionCustomizationError("YAML mapping keys must be strings")
            for key, child in item.items():
                visit(key, depth + 1)
                visit(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                visit(child, depth + 1)
        elif item is not None and not isinstance(item, (str, int, float, bool)):
            raise SubscriptionCustomizationError("Rule content contains an unsupported value")

    visit(value)
    return value


def _rule_value(rule_type: str, content: str):
    value = _bounded_yaml(content)
    if rule_type == "dns":
        if isinstance(value, dict) and "dns" in value:
            value = value["dns"]
        if not isinstance(value, dict):
            raise SubscriptionCustomizationError("DNS rule content must be a mapping")
    elif rule_type == "rules":
        if isinstance(value, dict) and "rules" in value:
            value = value["rules"]
        if not isinstance(value, list) or not value or any(
            not isinstance(item, str) or not item.strip() or len(item) > 4096 for item in value
        ):
            raise SubscriptionCustomizationError("Rules content must be a non-empty string list")
    else:
        if isinstance(value, dict) and "rule-providers" in value:
            value = value["rule-providers"]
        if not isinstance(value, dict) or not value or any(
            not isinstance(item, dict) for item in value.values()
        ):
            raise SubscriptionCustomizationError(
                "Rule-provider content must be a non-empty mapping"
            )
    return value


def _regex(value: str, label: str):
    if not value:
        return
    try:
        re.compile(value)
    except re.error:
        raise SubscriptionCustomizationError(f"{label} is not a valid regular expression") from None


def _provider_values(payload):
    _regex(payload.filter, "Provider filter")
    _regex(payload.exclude_filter, "Provider exclude filter")
    if not isinstance(payload.header, dict) or len(payload.header) > 32:
        raise SubscriptionCustomizationError("Provider headers are invalid")
    header_bytes = 0
    for name, values in payload.header.items():
        if (
            not isinstance(name, str)
            or HEADER_NAME.fullmatch(name) is None
            or not isinstance(values, list)
            or not values
            or len(values) > 8
        ):
            raise SubscriptionCustomizationError("Provider headers are invalid")
        for item in values:
            if (
                not isinstance(item, str)
                or not item
                or len(item) > 2048
                or any(ord(char) < 32 or ord(char) == 127 for char in item)
            ):
                raise SubscriptionCustomizationError("Provider headers are invalid")
            header_bytes += len(name.encode()) + len(item.encode())
    if header_bytes > 16_384:
        raise SubscriptionCustomizationError("Provider headers are invalid")
    if any(ord(char) < 32 for char in payload.proxy):
        raise SubscriptionCustomizationError("Provider proxy contains control characters")
    _bounded_yaml(yaml.safe_dump(payload.override, allow_unicode=True, sort_keys=False))
    return payload.model_dump(exclude={"owner_username", "expected_revision"})


class SubscriptionCustomizations:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def _owner(session, username):
        owner = session.get(ProductUserModel, username)
        if owner is None or owner.removal_id:
            raise SubscriptionCustomizationNotFound("Subscriber not found")
        return owner

    @staticmethod
    def _rule_read(row):
        return CustomRuleRead(
            id=row.id, owner_username=row.owner_username, name=row.name, type=row.type,
            mode=row.mode, content=row.content, enabled=row.enabled, revision=row.revision,
            created_at=row.created_at, updated_at=row.updated_at,
        )

    @staticmethod
    def _provider_read(row):
        return ProxyProviderRead(
            id=row.id, owner_username=row.owner_username,
            external_source_id=row.external_source_id, name=row.name, type=row.type,
            interval=row.interval, proxy=row.proxy, size_limit=row.size_limit,
            header=row.header or {},
            health_check_enabled=row.health_check_enabled,
            health_check_url=row.health_check_url,
            health_check_interval=row.health_check_interval,
            health_check_timeout=row.health_check_timeout,
            health_check_lazy=row.health_check_lazy,
            health_check_expected_status=row.health_check_expected_status,
            filter=row.filter, exclude_filter=row.exclude_filter,
            exclude_type=row.exclude_type, geo_ip_filter=row.geo_ip_filter,
            override=row.override or {},
            process_mode=row.process_mode, enabled=row.enabled, revision=row.revision,
            created_at=row.created_at, updated_at=row.updated_at,
        )

    def list_rules(self, *, owner_username=None):
        with self.store._session() as session:
            query = select(CustomRuleModel)
            if owner_username is not None:
                query = query.where(CustomRuleModel.owner_username == owner_username)
            rows = session.scalars(
                query.order_by(CustomRuleModel.created_at.desc(), CustomRuleModel.id)
            ).all()
            return [self._rule_read(row) for row in rows]

    def create_rule(self, payload: CustomRuleCreate, *, owner_username=None):
        owner = owner_username or payload.owner_username
        if owner_username is not None and payload.owner_username != owner_username:
            raise SubscriptionCustomizationNotFound("Subscriber not found")
        now = datetime.now(UTC)
        _rule_value(payload.type, payload.content)
        row = CustomRuleModel(
            id=str(uuid4()), owner_username=owner, name=_name(payload.name),
            name_key=_name(payload.name).casefold(), type=payload.type, mode=payload.mode,
            content=payload.content.strip(), enabled=payload.enabled, revision=1,
            created_at=now, updated_at=now,
        )
        with self.store._coordinated_session() as session:
            self._owner(session, owner)
            session.add(row)
            try:
                session.flush()
            except IntegrityError:
                raise SubscriptionCustomizationConflict(
                    "A custom rule with this name already exists for the subscriber"
                ) from None
            result = self._rule_read(row)
            session.commit()
            return result

    def update_rule(self, identifier, payload: CustomRuleUpdate, *, owner_username=None):
        _rule_value(payload.type, payload.content)
        with self.store._coordinated_session() as session:
            row = session.get(CustomRuleModel, str(identifier))
            if row is None or owner_username is not None and row.owner_username != owner_username:
                raise SubscriptionCustomizationNotFound("Custom rule not found")
            if row.revision != payload.expected_revision:
                raise SubscriptionCustomizationConflict("Custom rule changed; reload before saving")
            row.name = _name(payload.name)
            row.name_key = row.name.casefold()
            row.type, row.mode = payload.type, payload.mode
            row.content, row.enabled = payload.content.strip(), payload.enabled
            row.revision += 1
            row.updated_at = datetime.now(UTC)
            try:
                session.flush()
            except IntegrityError:
                raise SubscriptionCustomizationConflict(
                    "A custom rule with this name already exists for the subscriber"
                ) from None
            result = self._rule_read(row)
            session.commit()
            return result

    def delete_rule(self, identifier, expected_revision, *, owner_username=None):
        with self.store._coordinated_session() as session:
            row = session.get(CustomRuleModel, str(identifier))
            if row is None or owner_username is not None and row.owner_username != owner_username:
                raise SubscriptionCustomizationNotFound("Custom rule not found")
            if row.revision != expected_revision:
                raise SubscriptionCustomizationConflict(
                    "Custom rule changed; reload before deleting"
                )
            self._remove_profile_reference(
                session, "selected_custom_rule_ids", row.id
            )
            session.delete(row)
            session.commit()

    def list_providers(self, *, owner_username=None):
        with self.store._session() as session:
            query = select(ProxyProviderModel)
            if owner_username is not None:
                query = query.where(ProxyProviderModel.owner_username == owner_username)
            rows = session.scalars(
                query.order_by(ProxyProviderModel.created_at, ProxyProviderModel.id)
            ).all()
            return [self._provider_read(row) for row in rows]

    @staticmethod
    def _source(session, identifier, owner_username):
        from open_node.services.external_subscriptions import ExternalSourceModel

        source = session.get(ExternalSourceModel, str(identifier))
        if source is None or source.owner_username != owner_username:
            raise SubscriptionCustomizationNotFound("External source not found for subscriber")
        return source

    def create_provider(self, payload: ProxyProviderCreate, *, owner_username=None):
        owner = owner_username or payload.owner_username
        if owner_username is not None and payload.owner_username != owner_username:
            raise SubscriptionCustomizationNotFound("Subscriber not found")
        now = datetime.now(UTC)
        values = _provider_values(payload)
        self._require_geoip(payload.geo_ip_filter)
        values["external_source_id"] = str(payload.external_source_id)
        values["name"] = _name(payload.name)
        row = ProxyProviderModel(
            id=str(uuid4()), owner_username=owner, name_key=values["name"].casefold(),
            revision=1, created_at=now, updated_at=now, **values,
        )
        with self.store._coordinated_session() as session:
            self._owner(session, owner)
            self._source(session, payload.external_source_id, owner)
            session.add(row)
            try:
                session.flush()
            except IntegrityError:
                raise SubscriptionCustomizationConflict(
                    "A proxy provider with this name already exists for the subscriber"
                ) from None
            result = self._provider_read(row)
            session.commit()
            return result

    def update_provider(self, identifier, payload: ProxyProviderUpdate, *, owner_username=None):
        values = _provider_values(payload)
        self._require_geoip(payload.geo_ip_filter)
        values["external_source_id"] = str(payload.external_source_id)
        values["name"] = _name(payload.name)
        with self.store._coordinated_session() as session:
            row = session.get(ProxyProviderModel, str(identifier))
            if row is None or owner_username is not None and row.owner_username != owner_username:
                raise SubscriptionCustomizationNotFound("Proxy provider not found")
            if row.revision != payload.expected_revision:
                raise SubscriptionCustomizationConflict(
                    "Proxy provider changed; reload before saving"
                )
            self._source(session, payload.external_source_id, row.owner_username)
            for field, value in values.items():
                setattr(row, field, value)
            row.name_key = row.name.casefold()
            row.revision += 1
            row.updated_at = datetime.now(UTC)
            try:
                session.flush()
            except IntegrityError:
                raise SubscriptionCustomizationConflict(
                    "A proxy provider with this name already exists for the subscriber"
                ) from None
            result = self._provider_read(row)
            session.commit()
            return result

    def _require_geoip(self, value):
        lookup = self.store.geoip_country_lookup
        if value and (lookup is None or not lookup.configured):
            raise SubscriptionCustomizationError(
                "GeoIP filtering requires OPEN_NODE_GEOIP_IPINFO_TOKEN"
            )

    def delete_provider(self, identifier, expected_revision, *, owner_username=None):
        with self.store._coordinated_session() as session:
            row = session.get(ProxyProviderModel, str(identifier))
            if row is None or owner_username is not None and row.owner_username != owner_username:
                raise SubscriptionCustomizationNotFound("Proxy provider not found")
            if row.revision != expected_revision:
                raise SubscriptionCustomizationConflict(
                    "Proxy provider changed; reload before deleting"
                )
            self._remove_profile_reference(
                session, "selected_proxy_provider_ids", row.id
            )
            session.delete(row)
            session.commit()

    @staticmethod
    def _remove_profile_reference(session, field, identifier):
        from open_node.services.inventory import SubscriptionProfileModel

        now = datetime.now(UTC)
        for profile in session.scalars(select(SubscriptionProfileModel)).all():
            values = list(getattr(profile, field) or [])
            filtered = [value for value in values if value != identifier]
            if filtered != values:
                setattr(profile, field, filtered)
                profile.updated_at = now

    @staticmethod
    def _selected_rows(session, model, owner_username, identifiers, *, enabled=True):
        query = select(model).where(model.owner_username == owner_username)
        if enabled:
            query = query.where(model.enabled.is_(True))
        rows = session.scalars(query.order_by(model.created_at.desc(), model.id)).all()
        if identifiers:
            selected = set(identifiers)
            rows = [row for row in rows if row.id in selected]
        return rows

    def prepare_clash_template(
        self, session, content, owner_username, provider_ids, base_url, subscription_code
    ):
        providers = self._selected_rows(
            session, ProxyProviderModel, owner_username, provider_ids
        )
        if not providers:
            return content
        value = _bounded_yaml(content)
        if not isinstance(value, dict):
            raise SubscriptionCustomizationError("Clash template must be a mapping")
        target = value.setdefault("proxy-providers", {})
        if not isinstance(target, dict):
            raise SubscriptionCustomizationError("proxy-providers must be a mapping")
        encoded_code = quote(subscription_code, safe="")
        provider_names = []
        for provider in providers:
            if provider.process_mode != "client":
                continue
            if provider.name in target:
                raise SubscriptionCustomizationConflict(
                    f"Proxy provider name conflicts with the selected template: {provider.name}"
                )
            entry = {
                "type": "http",
                "url": (
                    f"{base_url.rstrip('/')}/proxy-provider/"
                    f"{encoded_code}/{provider.id}"
                ),
                "path": f"./proxy-providers/{provider.id}.yaml",
                "interval": provider.interval,
                "proxy": provider.proxy,
            }
            if provider.size_limit:
                entry["size-limit"] = provider.size_limit
            if provider.header:
                entry["header"] = deepcopy(provider.header)
            if provider.health_check_enabled:
                entry["health-check"] = {
                    "enable": True,
                    "url": provider.health_check_url,
                    "interval": provider.health_check_interval,
                    "timeout": provider.health_check_timeout,
                    "lazy": provider.health_check_lazy,
                    "expected-status": provider.health_check_expected_status,
                }
            for source, destination in (
                (provider.filter, "filter"),
                (provider.exclude_filter, "exclude-filter"),
                (provider.exclude_type, "exclude-type"),
            ):
                if source:
                    entry[destination] = source
            if provider.override:
                entry["override"] = deepcopy(provider.override)
            target[provider.name] = entry
            provider_names.append(provider.name)
        groups = value.get("proxy-groups", [])
        referenced = any(
            isinstance(group, dict)
            and (
                "__PROXY_PROVIDERS__" in group.get("proxies", [])
                or any(name in group.get("use", []) for name in provider_names)
            )
            for group in groups
            if isinstance(groups, list)
        )
        if not referenced and isinstance(groups, list):
            target_group = next(
                (group for group in groups if isinstance(group, dict) and group.get("type")),
                None,
            )
            if target_group is not None:
                target_group["use"] = list(
                    dict.fromkeys([*target_group.get("use", []), *provider_names])
                )
        rendered = yaml.safe_dump(value, allow_unicode=True, sort_keys=False)
        if len(rendered.encode()) > MAX_RENDERED_BYTES:
            raise SubscriptionCustomizationError("Customized template exceeds 8 MiB")
        return rendered

    def apply_rules(self, session, content, owner_username, rule_ids):
        rules = self._selected_rows(session, CustomRuleModel, owner_username, rule_ids)
        if not rules:
            return content
        value = _bounded_yaml(content)
        if not isinstance(value, dict):
            raise SubscriptionCustomizationError("Rendered Clash subscription must be a mapping")
        for rule in rules:
            custom = _rule_value(rule.type, rule.content)
            if rule.type == "dns":
                value["dns"] = deepcopy(custom)
            elif rule.type == "rules":
                self._apply_rule_list(value, custom, rule.mode)
            else:
                self._apply_rule_providers(value, custom, rule.mode)
        self._add_missing_groups(value)
        rendered = yaml.safe_dump(value, allow_unicode=True, sort_keys=False)
        if len(rendered.encode()) > MAX_RENDERED_BYTES:
            raise SubscriptionCustomizationError("Customized subscription exceeds 8 MiB")
        from open_node.services.template_rendering import parse_template

        parse_template(rendered, "clash")
        return rendered

    @staticmethod
    def _rule_key(value: str):
        parts = [part.strip() for part in value.split(",")]
        return ",".join(parts[:2]).casefold()

    @staticmethod
    def _is_match(value: str):
        return value.strip().upper().startswith("MATCH,")

    @classmethod
    def _apply_rule_list(cls, config, custom, mode):
        current = config.get("rules", [])
        if not isinstance(current, list) or any(not isinstance(item, str) for item in current):
            current = []
        incoming = list(dict.fromkeys(item.strip() for item in custom))
        if mode == "replace":
            preserved = [item for item in current if item.strip().upper().startswith("RULE-SET,")]
            config["rules"] = list(dict.fromkeys([*incoming, *preserved]))
            return
        incoming_keys = {cls._rule_key(item) for item in incoming}
        has_match = any(cls._is_match(item) for item in incoming)
        filtered = [
            item for item in current
            if item.strip().upper().startswith("RULE-SET,")
            or cls._rule_key(item) not in incoming_keys and not (has_match and cls._is_match(item))
        ]
        config["rules"] = [*incoming, *filtered] if mode == "prepend" else [*filtered, *incoming]

    @staticmethod
    def _apply_rule_providers(config, custom, mode):
        current = config.get("rule-providers", {})
        if not isinstance(current, dict):
            current = {}
        if mode == "replace":
            config["rule-providers"] = deepcopy(custom)
        elif mode == "prepend":
            config["rule-providers"] = {**current, **deepcopy(custom)}
        else:
            config["rule-providers"] = {**deepcopy(custom), **current}

    @staticmethod
    def _rule_policy(rule):
        parts = [part.strip() for part in rule.split(",")]
        if len(parts) < 2:
            return None
        return parts[-2] if parts[-1].lower() == "no-resolve" and len(parts) >= 3 else parts[-1]

    @classmethod
    def _add_missing_groups(cls, config):
        groups = config.get("proxy-groups", [])
        if not isinstance(groups, list):
            return
        names = {
            group.get("name") for group in groups
            if isinstance(group, dict) and isinstance(group.get("name"), str)
        }
        proxies = [
            proxy.get("name") for proxy in config.get("proxies", [])
            if isinstance(proxy, dict) and isinstance(proxy.get("name"), str)
        ]
        fallback = next(iter(names), proxies[0] if proxies else "DIRECT")
        for rule in config.get("rules", []):
            if not isinstance(rule, str):
                continue
            policy = cls._rule_policy(rule)
            if not policy or policy in BUILTIN_POLICIES or policy in names:
                continue
            groups.append({
                "name": policy, "type": "select",
                "proxies": list(dict.fromkeys([fallback, "DIRECT"])),
            })
            names.add(policy)

    def _provider_proxies(self, session, provider):
        candidates, _unavailable, _warnings = (
            self.store.external_subscriptions().source_candidates(
                session, provider.owner_username, provider.external_source_id
            )
        )
        include = re.compile(provider.filter) if provider.filter else None
        exclude = re.compile(provider.exclude_filter) if provider.exclude_filter else None
        excluded_types = {
            item.strip().casefold() for item in provider.exclude_type.split("|") if item.strip()
        }
        country_codes = {
            item.strip().upper() for item in provider.geo_ip_filter.split(",") if item.strip()
        }
        pending_servers = []
        if country_codes:
            for _identifier, original in candidates:
                name = str(original.get("name") or "Node")
                if exclude and exclude.search(name):
                    continue
                if str(original.get("type") or "").casefold() in excluded_types:
                    continue
                if include and include.search(name):
                    continue
                server = original.get("server")
                if isinstance(server, str) and server.strip():
                    pending_servers.append(server.strip())
        pending_servers = list(dict.fromkeys(pending_servers))
        if len(pending_servers) > 64:
            raise SubscriptionCustomizationError(
                "GeoIP filtering supports at most 64 unique node servers per Provider"
            )
        countries = {}
        lookup = self.store.geoip_country_lookup
        if pending_servers and (lookup is None or not lookup.configured):
            raise SubscriptionCustomizationError("GeoIP filtering is not configured")
        if pending_servers:
            from open_node.services.geoip import GeoIPLookupError

            with ThreadPoolExecutor(max_workers=min(4, len(pending_servers))) as executor:
                tasks = {
                    executor.submit(lookup.lookup, server): server
                    for server in pending_servers
                }
                for task in as_completed(tasks):
                    try:
                        countries[tasks[task]] = task.result()
                    except GeoIPLookupError:
                        countries[tasks[task]] = ""
        proxies = []
        used_names = set()
        for _identifier, original in candidates:
            proxy = deepcopy(original)
            name = str(proxy.get("name") or "Node")
            if exclude and exclude.search(name):
                continue
            if str(proxy.get("type") or "").casefold() in excluded_types:
                continue
            name_matches = bool(include and include.search(name))
            geo_matches = bool(
                country_codes
                and isinstance(proxy.get("server"), str)
                and countries.get(proxy["server"].strip()) in country_codes
            )
            if (include or country_codes) and not (name_matches or geo_matches):
                continue
            proxy.update(deepcopy(provider.override or {}))
            base, suffix = name, 2
            while name in used_names:
                name = f"{base} ({suffix})"
                suffix += 1
            proxy["name"] = name
            used_names.add(name)
            proxies.append(proxy)
        return proxies

    def provider_payload(self, session, provider):
        proxies = self._provider_proxies(session, provider)
        rendered = yaml.safe_dump({"proxies": proxies}, allow_unicode=True, sort_keys=False)
        if len(rendered.encode()) > MAX_RENDERED_BYTES:
            raise SubscriptionCustomizationError("Proxy provider output exceeds 8 MiB")
        return rendered, len(proxies)

    def apply_server_providers(
        self, session, content, owner_username, provider_ids, client_format
    ):
        providers = [
            provider for provider in self._selected_rows(
                session, ProxyProviderModel, owner_username, provider_ids
            )
            if provider.process_mode == "mmw"
        ]
        if not providers:
            return content, None
        value = _bounded_yaml(content)
        if not isinstance(value, dict):
            raise SubscriptionCustomizationError("Clash subscription must be a mapping")
        proxies = value.get("proxies", [])
        groups = value.get("proxy-groups", [])
        if not isinstance(proxies, list) or not isinstance(groups, list):
            raise SubscriptionCustomizationError("Clash proxies and groups must be lists")
        from open_node.services import subscription_clients
        from open_node.services.subscription_extra_clients import yaml_proxy

        group_names = {
            group.get("name") for group in groups
            if isinstance(group, dict) and isinstance(group.get("name"), str)
        }
        used_names = {
            proxy.get("name") for proxy in proxies
            if isinstance(proxy, dict) and isinstance(proxy.get("name"), str)
        }
        reusable_names = set(used_names)
        used_names.update(BUILTIN_POLICIES)
        for provider in providers:
            if provider.name in BUILTIN_POLICIES or provider.name in used_names:
                raise SubscriptionCustomizationConflict(
                    "Server Provider name conflicts with a proxy or built-in policy: "
                    + provider.name
                )
            group = next(
                (
                    item for item in groups
                    if isinstance(item, dict) and item.get("name") == provider.name
                ),
                None,
            )
            if group is None:
                group = {"name": provider.name, "type": "select", "proxies": []}
                groups.append(group)
                group_names.add(provider.name)
            elif not isinstance(group.get("type"), str):
                raise SubscriptionCustomizationConflict(
                    f"Server Provider group is invalid: {provider.name}"
                )
            names = []
            for original in self._provider_proxies(session, provider):
                original_name = str(original.get("name") or "Node")
                if not provider.override and original_name in reusable_names:
                    names.append(original_name)
                    continue
                proxy = (
                    yaml_proxy(original, "stash")
                    if client_format == "stash"
                    else subscription_clients.clash_proxy(original)
                )
                base = str(proxy.get("name") or "Node")
                name, suffix = base, 2
                while name in used_names or name in group_names:
                    name = f"{base} ({suffix})"
                    suffix += 1
                proxy["name"] = name
                proxies.append(proxy)
                names.append(name)
                used_names.add(name)
            group["proxies"] = names or ["REJECT"]
            group.pop("use", None)
        value["proxies"], value["proxy-groups"] = proxies, groups
        rendered = yaml.safe_dump(value, allow_unicode=True, sort_keys=False)
        if len(rendered.encode()) > MAX_RENDERED_BYTES:
            raise SubscriptionCustomizationError("Server Provider output exceeds 8 MiB")
        from open_node.services.template_rendering import parse_template

        parse_template(rendered, "clash")
        return rendered, len(proxies)
