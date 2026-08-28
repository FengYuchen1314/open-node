"""Resolve subscriber overrides identically for quotas, previews and runtime access."""

from collections import Counter

from open_node.domain.user_limits import (
    CatalogUserLimitOverrides,
    UserEffectiveLimits,
    UserLimitOverrides,
    UserNodeLimitsRead,
)


def overrides(user):
    quota = user.traffic_limit_override_bytes
    return UserLimitOverrides(
        traffic_limit_gb=quota / 1024**3 if quota is not None else None,
        speed_limit_mbps=user.speed_limit_override_mbps,
        device_limit=user.device_limit_override,
        node_speed_limits=user.node_speed_limit_overrides or {},
        node_device_limits=user.node_device_limit_overrides or {},
    )


def apply_overrides(user, values):
    user.traffic_limit_override_bytes = (
        int(values.traffic_limit_gb * 1024**3) if values.traffic_limit_gb is not None else None
    )
    user.speed_limit_override_mbps = values.speed_limit_mbps
    user.device_limit_override = values.device_limit
    user.node_speed_limit_overrides = {
        str(identifier): value for identifier, value in values.node_speed_limits.items()
    }
    user.node_device_limit_overrides = {
        str(identifier): value for identifier, value in values.node_device_limits.items()
    }


def traffic_limit(user, plan):
    if user.traffic_limit_override_bytes is not None:
        return user.traffic_limit_override_bytes
    return plan.traffic_limit_bytes if plan else 0


def _resolve(user_nodes, user_default, plan_nodes, plan_default, node, has_plan):
    keys = [(node.id, "node")] if node else []
    if node and node.parent_id:
        keys.append((node.parent_id, "parent"))
    for key, source in keys:
        if key in (user_nodes or {}):
            return user_nodes[key], "user_" + source
    if user_default is not None:
        return user_default, "user"
    for key, source in keys:
        if key in (plan_nodes or {}):
            return plan_nodes[key], "plan_" + source
    return (plan_default, "plan") if has_plan else (0, "unlimited")


def effective_limits(user, plan, node=None):
    speed, speed_source = _resolve(
        user.node_speed_limit_overrides,
        user.speed_limit_override_mbps,
        plan.node_speed_limits if plan else {},
        plan.speed_limit_mbps if plan else 0,
        node,
        plan is not None,
    )
    devices, device_source = _resolve(
        user.node_device_limit_overrides,
        user.device_limit_override,
        plan.node_device_limits if plan else {},
        plan.device_limit if plan else 0,
        node,
        plan is not None,
    )
    return UserEffectiveLimits(
        speed_limit_mbps=speed,
        device_limit=devices,
        speed_source=speed_source,
        device_source=device_source,
    )


def node_limits(user, plan, nodes, credentials=()):
    by_id = {node.id: node for node in nodes}
    result = [
        UserNodeLimitsRead(
            node_id=node.id,
            name=node.name,
            enabled=node.enabled and not node.removal_id,
            **effective_limits(user, plan, node).model_dump(),
        )
        for identifier in (plan.node_ids if plan else [])
        if (node := by_id.get(identifier)) is not None
    ]
    by_id = {str(item.node_id): item for item in result if item.enabled}
    speed_groups, connection_groups = {}, {}
    for credential in credentials:
        if credential.node_id in by_id and credential.inbound_tag:
            key = (credential.server_id, credential.inbound_tag, credential.email)
            speed_groups.setdefault(key, []).append(by_id[credential.node_id])
            connection_groups.setdefault(key[:2], []).append(by_id[credential.node_id])
    for field, source, groups in (
        ("speed_limit_mbps", "speed_source", speed_groups),
        ("device_limit", "device_source", connection_groups),
    ):
        for members in groups.values():
            value = min(
                (getattr(item, field) for item in members if getattr(item, field)), default=0
            )
            for item in members:
                if getattr(item, field) != value:
                    setattr(item, field, value)
                    setattr(item, source, "shared")
    return result


def prune_node_overrides(user, identifiers):
    changed = False
    for field in ("node_speed_limit_overrides", "node_device_limit_overrides"):
        previous = getattr(user, field) or {}
        remaining = {key: value for key, value in previous.items() if key not in identifiers}
        if remaining != previous:
            setattr(user, field, remaining)
            changed = True
    return changed


def catalog_overrides(user, names):
    from open_node.services.inventory import ProductUserConflict

    values = overrides(user).model_dump(mode="json")
    counts = Counter(names.values())
    for field in ("node_speed_limits", "node_device_limits"):
        mapped = {}
        for identifier, value in values[field].items():
            if identifier not in names or counts[names[identifier]] != 1:
                raise ProductUserConflict(
                    "User limit overrides require unique, existing node names for catalog export"
                )
            mapped[names[identifier]] = value
        values[field] = mapped
    return CatalogUserLimitOverrides.model_validate(values)


def import_overrides(values, names, ambiguous):
    from open_node.services.inventory import ProductUserConflict

    result = values.model_dump(mode="json")
    for field in ("node_speed_limits", "node_device_limits"):
        mapped = {}
        for name, value in result[field].items():
            if name not in names or name in ambiguous:
                raise ProductUserConflict(
                    f"User limit node is missing or ambiguous in catalog: {name}"
                )
            mapped[names[name]] = value
        result[field] = mapped
    return UserLimitOverrides.model_validate(result)
