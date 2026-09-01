"""Named subscription outputs and active-main MMWX short-link compatibility."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, or_, select

from open_node.domain.subscription_profiles import (
    SubscriberSubscriptionProfileRead,
    SubscriptionProfileRead,
    SubscriptionProfileUpdate,
)
from open_node.domain.subscriptions import SubscriptionClientFormat
from open_node.services.inventory import (
    LegacySubscriptionPlanCodeModel,
    ProductUserModel,
    ProductUserSubscriptionTokenModel,
    RenderedSubscription,
    SubscriptionPlanModel,
    SubscriptionProfileAssignmentModel,
    SubscriptionProfileModel,
    SubscriptionTokenNotFoundError,
    SubscriptionUnavailableError,
)
from open_node.services.subscription_access import revision
from open_node.services.subscription_customizations import CustomRuleModel, ProxyProviderModel
from open_node.services.subscription_scripts import OverrideScriptModel
from open_node.services.subscription_templates import TemplateRecord

LEGACY_FORMATS = {
    "": SubscriptionClientFormat.CLASH,
    "clash": SubscriptionClientFormat.CLASH,
    "clashmeta": SubscriptionClientFormat.CLASH,
    "surge": SubscriptionClientFormat.SURGE,
    "surgemac": SubscriptionClientFormat.SURGE,
    "sing-box": SubscriptionClientFormat.SING_BOX,
    "v2ray": SubscriptionClientFormat.BASE64,
    "uri": SubscriptionClientFormat.URI_LIST,
    "xray": SubscriptionClientFormat.XRAY,
    "loon": SubscriptionClientFormat.LOON,
    "qx": SubscriptionClientFormat.QUANTUMULT_X,
    "quantumult-x": SubscriptionClientFormat.QUANTUMULT_X,
    "shadowrocket": SubscriptionClientFormat.SHADOWROCKET,
    "stash": SubscriptionClientFormat.STASH,
    "surfboard": SubscriptionClientFormat.SURFBOARD,
    "egern": SubscriptionClientFormat.EGERN,
}


class SubscriptionProfileNotFoundError(ValueError):
    pass


class SubscriptionProfileConflict(ValueError):
    pass


class SubscriptionProfiles:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def legacy_format(value: str | None, fallback: SubscriptionClientFormat):
        if value is None:
            return fallback
        return LEGACY_FORMATS.get(value.strip().lower(), fallback)

    @staticmethod
    def _assigned_usernames(session, profile_id):
        return list(
            session.scalars(
                select(SubscriptionProfileAssignmentModel.username)
                .where(SubscriptionProfileAssignmentModel.profile_id == profile_id)
                .order_by(SubscriptionProfileAssignmentModel.username)
            )
        )

    def read(self, session, profile):
        assigned_usernames = self._assigned_usernames(session, profile.id)
        state = {
            "id": profile.id,
            "owner_username": profile.owner_username,
            "name": profile.name,
            "description": profile.description,
            "node_ids": profile.node_ids or [],
            "clash_template_id": profile.clash_template_id,
            "surge_template_id": profile.surge_template_id,
            "custom_rules_enabled": profile.custom_rules_enabled,
            "selected_custom_rule_ids": profile.selected_custom_rule_ids or [],
            "proxy_providers_enabled": profile.proxy_providers_enabled,
            "selected_proxy_provider_ids": profile.selected_proxy_provider_ids or [],
            "override_scripts_enabled": profile.override_scripts_enabled,
            "selected_override_script_ids": profile.selected_override_script_ids or [],
            "enabled": profile.enabled,
            "assigned_usernames": assigned_usernames,
            "updated_at": self.store._aware_datetime(profile.updated_at).isoformat(),
        }
        return SubscriptionProfileRead(
            id=profile.id,
            owner_username=profile.owner_username,
            assigned_usernames=assigned_usernames,
            revision=revision(state),
            name=profile.name,
            description=profile.description,
            node_ids=profile.node_ids or [],
            clash_template_id=profile.clash_template_id,
            surge_template_id=profile.surge_template_id,
            custom_rules_enabled=profile.custom_rules_enabled,
            selected_custom_rule_ids=profile.selected_custom_rule_ids or [],
            proxy_providers_enabled=profile.proxy_providers_enabled,
            selected_proxy_provider_ids=profile.selected_proxy_provider_ids or [],
            override_scripts_enabled=profile.override_scripts_enabled,
            selected_override_script_ids=profile.selected_override_script_ids or [],
            enabled=profile.enabled,
            sort_order=profile.sort_order,
            source_type=profile.source_type,
            source_filename=profile.source_filename,
            source_template_filename=profile.source_template_filename,
            legacy_source_id=profile.legacy_source_id,
            legacy_file_short_code=profile.legacy_file_short_code,
            legacy_custom_short_code=profile.legacy_custom_short_code,
            migration_warnings=profile.migration_warnings or [],
            expires_at=profile.expires_at,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
        )

    def list(self):
        with self.store._session() as session:
            rows = session.scalars(
                select(SubscriptionProfileModel).order_by(
                    SubscriptionProfileModel.sort_order,
                    SubscriptionProfileModel.created_at,
                    SubscriptionProfileModel.id,
                )
            ).all()
            return [self.read(session, row) for row in rows]

    def update(self, identifier, payload: SubscriptionProfileUpdate):
        now = datetime.now(UTC)
        with self.store._coordinated_session() as session:
            profile = session.get(SubscriptionProfileModel, str(identifier))
            if profile is None:
                raise SubscriptionProfileNotFoundError("subscription profile not found")
            current = self.read(session, profile)
            if current.revision != payload.expected_revision:
                raise SubscriptionProfileConflict(
                    "Subscription profile changed; reload before saving"
                )
            usernames = list(
                dict.fromkeys(username.strip() for username in payload.assigned_usernames)
            )
            if any(not username for username in usernames):
                raise SubscriptionProfileConflict("Assigned usernames cannot be empty")
            users = {
                user.username: user
                for user in session.scalars(
                    select(ProductUserModel).where(ProductUserModel.username.in_(usernames))
                ).all()
            }
            if set(users) != set(usernames) or any(user.removal_id for user in users.values()):
                raise SubscriptionProfileConflict(
                    "An assigned subscriber is missing or being removed"
                )
            node_ids = [str(node_id) for node_id in payload.node_ids]
            self.store._ensure_managed_nodes_exist(session, payload.node_ids)
            for user in users.values():
                if not user.current_plan_id or not node_ids:
                    continue
                plan = session.get(SubscriptionPlanModel, user.current_plan_id)
                if plan is None or not set(node_ids).issubset(plan.node_ids or []):
                    raise SubscriptionProfileConflict(
                        f"{user.username}: selected nodes are outside the current plan"
                    )
            for field, expected_format in (
                ("clash_template_id", "clash"),
                ("surge_template_id", "surge"),
            ):
                template_id = getattr(payload, field)
                template = session.get(TemplateRecord, str(template_id)) if template_id else None
                if template_id and (template is None or template.format != expected_format):
                    raise SubscriptionProfileConflict(
                        f"Selected {expected_format} template is missing or incompatible"
                    )
                setattr(profile, field, str(template_id) if template_id else None)
            custom_rule_ids = list(
                dict.fromkeys(str(value) for value in payload.selected_custom_rule_ids)
            )
            provider_ids = list(
                dict.fromkeys(str(value) for value in payload.selected_proxy_provider_ids)
            )
            script_ids = list(
                dict.fromkeys(str(value) for value in payload.selected_override_script_ids)
            )
            self._owned_customizations(
                session, CustomRuleModel, custom_rule_ids, profile.owner_username, "custom rule"
            )
            self._owned_customizations(
                session, ProxyProviderModel, provider_ids, profile.owner_username, "proxy provider"
            )
            self._owned_customizations(
                session, OverrideScriptModel, script_ids, profile.owner_username, "override script"
            )
            profile.custom_rules_enabled = payload.custom_rules_enabled
            profile.selected_custom_rule_ids = custom_rule_ids
            profile.proxy_providers_enabled = payload.proxy_providers_enabled
            profile.selected_proxy_provider_ids = provider_ids
            profile.override_scripts_enabled = payload.override_scripts_enabled
            profile.selected_override_script_ids = script_ids
            profile.name = payload.name.strip()
            if not profile.name:
                raise SubscriptionProfileConflict("Subscription profile name is required")
            profile.description = payload.description.strip()
            profile.node_ids = node_ids
            profile.enabled = payload.enabled
            if profile.enabled and profile.source_type == "upload":
                profile.migration_warnings = [
                    "Legacy raw content was replaced by this managed Open Node profile"
                ]
            profile.updated_at = now
            session.execute(
                delete(SubscriptionProfileAssignmentModel).where(
                    SubscriptionProfileAssignmentModel.profile_id == profile.id
                )
            )
            for username in usernames:
                session.add(
                    SubscriptionProfileAssignmentModel(
                        profile_id=profile.id, username=username, created_at=now
                    )
                )
            session.flush()
            result = self.read(session, profile)
            session.commit()
            return result

    @staticmethod
    def _owned_customizations(session, model, identifiers, owner_username, label):
        if not identifiers:
            return
        rows = session.scalars(select(model).where(model.id.in_(identifiers))).all()
        if len(rows) != len(identifiers) or any(
            row.owner_username != owner_username for row in rows
        ):
            raise SubscriptionProfileConflict(
                f"Selected {label} is missing or belongs to another subscriber"
            )

    def subscriber_profiles(self, username, url_for):
        with self.store._session() as session:
            token = session.get(ProductUserSubscriptionTokenModel, username)
            if token is None:
                return []
            rows = session.scalars(
                select(SubscriptionProfileModel)
                .join(
                    SubscriptionProfileAssignmentModel,
                    SubscriptionProfileAssignmentModel.profile_id == SubscriptionProfileModel.id,
                )
                .where(SubscriptionProfileAssignmentModel.username == username)
                .order_by(
                    SubscriptionProfileModel.sort_order,
                    SubscriptionProfileModel.created_at,
                )
            ).all()
            user_code = token.custom_short_code or token.short_code
            result = []
            for profile in rows:
                profile_code = profile.legacy_file_short_code
                if not profile_code:
                    continue
                combined = profile_code + user_code
                result.append(
                    SubscriberSubscriptionProfileRead(
                        id=profile.id,
                        name=profile.name,
                        description=profile.description,
                        subscription_url=str(url_for("legacy_mmwx_subscription", code=combined)),
                        short_code=combined,
                        enabled=profile.enabled,
                        expires_at=profile.expires_at,
                        warnings=profile.migration_warnings or [],
                    )
                )
            return result

    def resolve(
        self,
        code: str,
        client_format: SubscriptionClientFormat,
        node_id: UUID | None = None,
        public_base_url: str | None = None,
    ) -> RenderedSubscription:
        code = code.strip()
        if not code:
            raise SubscriptionTokenNotFoundError("subscription not found")
        with self.store._session() as session:
            username, profile = self._resolve_code(session, code)
            return self._render(
                session,
                username,
                client_format,
                node_id,
                profile,
                public_base_url=public_base_url,
                subscription_code=code,
            )

    def _resolve_code(self, session, code):
        profile = session.scalar(
            select(SubscriptionProfileModel).where(
                or_(
                    SubscriptionProfileModel.legacy_custom_short_code == code,
                    SubscriptionProfileModel.legacy_file_short_code == code,
                )
            )
        )
        if profile is not None:
            return profile.owner_username, profile

        user_codes = {}
        for token in session.scalars(select(ProductUserSubscriptionTokenModel)).all():
            if token.custom_short_code:
                user_codes[token.custom_short_code] = token.username
            if token.short_code:
                user_codes[token.short_code] = token.username
        profiles = session.scalars(select(SubscriptionProfileModel)).all()
        profile_codes = {
            value: profile
            for profile in profiles
            for value in (
                profile.legacy_file_short_code,
                profile.legacy_custom_short_code,
            )
            if value
        }
        plan_codes = set(session.scalars(select(LegacySubscriptionPlanCodeModel.code)))
        for position in range(len(code) - 1, 0, -1):
            username = user_codes.get(code[position:])
            if username is None:
                continue
            left = code[:position]
            if left in profile_codes:
                return username, profile_codes[left]
            if left in plan_codes:
                return username, None
        raise SubscriptionTokenNotFoundError("subscription not found")

    def provider(self, code, identifier):
        code = code.strip()
        if not code:
            raise SubscriptionTokenNotFoundError("subscription not found")
        from open_node.services.subscription_customizations import ProxyProviderModel

        with self.store._session() as session:
            username, profile = self._resolve_code(session, code)
            if profile is None:
                raise SubscriptionTokenNotFoundError("proxy provider not found")
            self._available_profile(profile)
            provider = session.get(ProxyProviderModel, str(identifier))
            selected = set(profile.selected_proxy_provider_ids or [])
            if (
                not profile.proxy_providers_enabled
                or provider is None
                or not provider.enabled
                or provider.owner_username != profile.owner_username
                or selected and provider.id not in selected
            ):
                raise SubscriptionTokenNotFoundError("proxy provider not found")
            content, count = self.store.subscription_customizations().provider_payload(
                session, provider
            )
            return username, provider.name, content, count

    def _available_profile(self, profile):
        if not profile.enabled:
            raise SubscriptionUnavailableError("subscription profile requires configuration")
        if profile.expires_at and datetime.now(UTC) > self.store._aware_datetime(
            profile.expires_at
        ):
            raise SubscriptionUnavailableError("subscription profile has expired")

    def _render(
        self,
        session,
        username,
        client_format,
        node_id,
        profile,
        *,
        public_base_url=None,
        subscription_code=None,
    ):
        user = session.get(ProductUserModel, username)
        plan = self.store._available_subscription_plan(session, user)
        if profile is not None:
            self._available_profile(profile)
        return self.store._render_user_subscription(
            session,
            user,
            plan,
            client_format,
            node_id=node_id,
            selected_node_ids=(set(profile.node_ids) if profile and profile.node_ids else None),
            template_override=(
                self._template(session, profile, client_format) if profile else None
            ),
            title=profile.name if profile else plan.name,
            extra_warnings=profile.migration_warnings if profile else None,
            customization_owner=profile.owner_username if profile else None,
            custom_rules_enabled=bool(profile and profile.custom_rules_enabled),
            selected_custom_rule_ids=(profile.selected_custom_rule_ids or []) if profile else [],
            proxy_providers_enabled=bool(profile and profile.proxy_providers_enabled),
            selected_proxy_provider_ids=(
                (profile.selected_proxy_provider_ids or []) if profile else []
            ),
            override_scripts_enabled=bool(profile and profile.override_scripts_enabled),
            selected_override_script_ids=(
                (profile.selected_override_script_ids or []) if profile else []
            ),
            public_base_url=public_base_url,
            subscription_code=subscription_code,
        )

    @staticmethod
    def _template(session, profile, client_format):
        format = "clash" if client_format == SubscriptionClientFormat.STASH else client_format.value
        if profile is None or format not in {"clash", "surge"}:
            return None
        identifier = getattr(profile, format + "_template_id")
        row = session.get(TemplateRecord, identifier) if identifier else None
        if row is not None and row.format == format:
            return row
        return None
