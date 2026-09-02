"""Guarded import of identities from the active MMWX main-line database."""

import binascii
import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

import pyotp
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from open_node.domain.legacy_mmwx import LegacyMMWXImportPreview, LegacyMMWXImportResponse
from open_node.services.inventory import (
    LEGACY_SUBSCRIPTION_BEARER_GENERATION,
    LegacySubscriptionPlanCodeModel,
    ProductUserModel,
    ProductUserSubscriptionTokenModel,
    SubscriptionPlanModel,
    SubscriptionProfileAssignmentModel,
    SubscriptionProfileModel,
)
from open_node.services.subscriber_auth import (
    SubscriberAccount,
    digest,
    revoke_subscriber_sessions,
)
from open_node.services.subscription_access import revision


class LegacyMMWXMigrationError(ValueError):
    pass


def secret(value):
    return value.get_secret_value() if value is not None else None


class LegacyMMWXMigration:
    def __init__(self, inventory, subscriber_auth):
        self.inventory = inventory
        self.subscriber_auth = subscriber_auth

    @staticmethod
    def source_fingerprint(bundle):
        source = {
            "version": bundle.version,
            "source_revision": bundle.source_revision,
            "users": [
                {
                    **entry.model_dump(
                        exclude={
                            "password_hash",
                            "totp_secret",
                            "recovery_code_hashes",
                            "token",
                            "generated_short_code",
                            "custom_short_code",
                        },
                        mode="json",
                    ),
                    "password_hash": secret(entry.password_hash),
                    "totp_secret": secret(entry.totp_secret),
                    "recovery_code_hashes": [secret(value) for value in entry.recovery_code_hashes],
                    "token": secret(entry.token),
                    "generated_short_code": secret(entry.generated_short_code),
                    "custom_short_code": secret(entry.custom_short_code),
                }
                for entry in bundle.users
            ],
            "packages": [entry.model_dump(mode="json") for entry in bundle.packages],
            "subscription_profiles": [
                entry.model_dump(mode="json") for entry in bundle.subscription_profiles
            ],
        }
        return hashlib.sha256(
            json.dumps(source, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @staticmethod
    def target_fingerprint(user, account, token):
        def hidden(value):
            return hashlib.sha256((value or "").encode()).hexdigest()

        return {
            "username": user.username if user else None,
            "removal_id": user.removal_id if user else None,
            "account": (
                [account.version, hidden(account.password_hash), hidden(account.totp_secret)]
                if account
                else None
            ),
            "token": (
                [
                    hidden(token.token),
                    hidden(token.short_code),
                    hidden(token.custom_short_code),
                    token.bearer_generation,
                ]
                if token
                else None
            ),
        }

    def _analyze(self, session, payload):
        bundle = payload.bundle
        totals = {
            "new_users": 0,
            "existing_users": 0,
            "imported_accounts": 0,
            "replaced_accounts": 0,
            "skipped_accounts": 0,
            "imported_tokens": 0,
            "replaced_tokens": 0,
            "skipped_tokens": 0,
            "imported_totp": 0,
            "mapped_packages": 0,
            "assigned_plans": 0,
            "imported_profiles": 0,
            "replaced_profiles": 0,
            "skipped_profiles": 0,
            "imported_profile_assignments": 0,
        }
        blockers, warnings, target = [], [], []
        incoming_keys = {}
        existing_tokens = {
            row.username: row
            for row in session.scalars(select(ProductUserSubscriptionTokenModel)).all()
        }
        existing_key_owners = {}
        for row in existing_tokens.values():
            for value in (row.token, row.short_code, row.custom_short_code):
                if value:
                    existing_key_owners[value.casefold()] = row.username

        packages = {entry.source_id: entry for entry in bundle.packages}
        unknown_mappings = sorted(set(payload.package_mappings) - set(packages))
        if unknown_mappings:
            blockers.append("Package mappings reference unknown legacy package IDs")
        required_packages = {
            entry.source_package_id for entry in bundle.users if entry.source_package_id is not None
        }
        missing_mappings = sorted(required_packages - set(payload.package_mappings))
        if missing_mappings:
            blockers.append(
                "Map every in-use legacy package before importing: "
                + ", ".join(str(value) for value in missing_mappings)
            )
        mapped_plans = {}
        for source_id, plan_id in payload.package_mappings.items():
            plan = session.get(SubscriptionPlanModel, str(plan_id))
            if plan is None:
                blockers.append(f"Legacy package {source_id}: selected plan no longer exists")
            else:
                mapped_plans[source_id] = plan
        totals["mapped_packages"] = len(mapped_plans)
        existing_plan_aliases = session.scalars(select(LegacySubscriptionPlanCodeModel)).all()
        existing_plan_codes = {
            row.code.casefold(): row.source_package_id for row in existing_plan_aliases
        }
        existing_plan_sources = {row.source_package_id: row for row in existing_plan_aliases}
        incoming_plan_codes = {}
        for package in bundle.packages:
            if package.source_id not in payload.package_mappings or not package.short_code:
                continue
            folded = package.short_code.casefold()
            other = incoming_plan_codes.get(folded)
            if other is not None and other != package.source_id:
                blockers.append(f"{package.name}: legacy package short code collides")
            incoming_plan_codes[folded] = package.source_id
            owner = existing_plan_codes.get(folded)
            if owner is not None and owner != package.source_id:
                blockers.append(f"{package.name}: package short code is already in use")
            target.append(
                {
                    "package": package.source_id,
                    "alias": (
                        [
                            existing_plan_sources[package.source_id].code,
                            existing_plan_sources[package.source_id].plan_id,
                            existing_plan_sources[package.source_id].source_name,
                        ]
                        if package.source_id in existing_plan_sources
                        else None
                    ),
                }
            )

        incoming_profile_codes = {}
        existing_profiles = {
            row.legacy_source_id: row
            for row in session.scalars(select(SubscriptionProfileModel)).all()
            if row.legacy_source_id is not None
        }
        existing_profile_codes = {
            value.casefold(): row.legacy_source_id
            for row in session.scalars(select(SubscriptionProfileModel)).all()
            for value in (row.legacy_file_short_code, row.legacy_custom_short_code)
            if value
        }
        for entry in bundle.subscription_profiles:
            profile = existing_profiles.get(entry.source_id)
            target.append(
                {
                    "profile": entry.source_id,
                    "target": (
                        [
                            profile.id,
                            profile.owner_username,
                            profile.legacy_file_short_code,
                            profile.legacy_custom_short_code,
                            self.inventory._aware_datetime(profile.updated_at).isoformat(),
                        ]
                        if profile
                        else None
                    ),
                }
            )
            if profile is None:
                totals["imported_profiles"] += 1
            elif payload.replace_existing:
                totals["replaced_profiles"] += 1
            else:
                totals["skipped_profiles"] += 1
                warnings.append(f"{entry.name}: existing subscription profile will be preserved")
                continue
            for value in (entry.file_short_code, entry.custom_short_code):
                if not value:
                    continue
                folded = value.casefold()
                other = incoming_profile_codes.get(folded)
                if other is not None and other != entry.source_id:
                    blockers.append(f"{entry.name}: legacy file short code collides")
                incoming_profile_codes[folded] = entry.source_id
                owner = existing_profile_codes.get(folded)
                if owner is not None and owner != entry.source_id:
                    blockers.append(f"{entry.name}: file short code is already in use")
            totals["imported_profile_assignments"] += len(entry.assigned_usernames)
            if entry.raw_output:
                warnings.append(f"{entry.name}: raw output is imported disabled until reconfigured")
            if entry.template_filename:
                warnings.append(
                    f"{entry.name}: legacy template {entry.template_filename} "
                    "must be mapped manually"
                )
            if entry.selected_custom_rule_ids or entry.selected_override_script_ids:
                warnings.append(
                    f"{entry.name}: legacy rules or scripts are not executed by Open Node"
                )

        for entry in bundle.users:
            user = session.get(ProductUserModel, entry.username)
            account = session.get(SubscriberAccount, entry.username)
            token = existing_tokens.get(entry.username)
            target.append(self.target_fingerprint(user, account, token))
            if user:
                totals["existing_users"] += 1
                if user.removal_id:
                    blockers.append(f"{entry.username}: user removal is pending")
            else:
                totals["new_users"] += 1
            mapped_plan = mapped_plans.get(entry.source_package_id)
            if mapped_plan is not None:
                if user and user.current_plan_id and user.current_plan_id != mapped_plan.id:
                    blockers.append(
                        f"{entry.username}: current plan differs from the selected legacy mapping"
                    )
                else:
                    totals["assigned_plans"] += 1
            if entry.source_role == "admin":
                warnings.append(f"{entry.username}: source administrator will import as subscriber")

            if account is None:
                totals["imported_accounts"] += 1
            elif payload.replace_existing:
                totals["replaced_accounts"] += 1
            else:
                totals["skipped_accounts"] += 1
                warnings.append(f"{entry.username}: existing login account will be preserved")

            if entry.totp_enabled and (account is None or payload.replace_existing):
                totals["imported_totp"] += 1
                if not self.subscriber_auth.cipher:
                    blockers.append(
                        f"{entry.username}: configure OPEN_NODE_SUBSCRIBER_TOTP_KEY "
                        "before importing TOTP"
                    )
                else:
                    try:
                        pyotp.TOTP(secret(entry.totp_secret)).at(0)
                    except (binascii.Error, ValueError, TypeError):
                        blockers.append(f"{entry.username}: legacy TOTP secret is invalid")

            if entry.token is None:
                continue
            if token is None:
                totals["imported_tokens"] += 1
            elif payload.replace_existing:
                totals["replaced_tokens"] += 1
            else:
                totals["skipped_tokens"] += 1
                warnings.append(f"{entry.username}: existing subscription links will be preserved")
                continue
            for value in (
                secret(entry.token),
                secret(entry.generated_short_code),
                secret(entry.custom_short_code),
            ):
                if not value:
                    continue
                folded = value.casefold()
                other = incoming_keys.get(folded)
                if other and other != entry.username:
                    blockers.append(
                        f"{entry.username}: a legacy subscription key collides with {other}"
                    )
                incoming_keys[folded] = entry.username
                owner = existing_key_owners.get(folded)
                if owner and owner != entry.username:
                    blockers.append(
                        f"{entry.username}: a legacy subscription key is already in use"
                    )

        fingerprint = revision(
            {
                "source": self.source_fingerprint(bundle),
                "replace_existing": payload.replace_existing,
                "package_mappings": {
                    str(key): str(value) for key, value in payload.package_mappings.items()
                },
                "target": target,
            }
        )
        return LegacyMMWXImportPreview(
            revision=fingerprint,
            ready=not blockers,
            total_users=len(bundle.users),
            blockers=list(dict.fromkeys(blockers)),
            warnings=list(dict.fromkeys(warnings)),
            **totals,
        )

    def preview(self, payload):
        with self.inventory._session() as session:
            return self._analyze(session, payload)

    def apply(self, payload):
        now = datetime.now(UTC)
        with self.inventory._coordinated_session() as session:
            preview = self._analyze(session, payload)
            if preview.revision != payload.expected_revision:
                raise LegacyMMWXMigrationError("Migration source or target changed; preview again")
            if preview.total_users != payload.confirm_user_count:
                raise LegacyMMWXMigrationError("Confirmed user count does not match the migration")
            if not preview.ready:
                raise LegacyMMWXMigrationError("Migration preview contains blockers")
            try:
                for entry in payload.bundle.users:
                    user = session.get(ProductUserModel, entry.username)
                    created_at = entry.created_at or now
                    if user is None:
                        user = ProductUserModel(
                            username=entry.username,
                            email=entry.email,
                            display_name=entry.display_name or entry.username,
                            remark="Imported from MMWX main",
                            role="user",
                            is_active=entry.is_active,
                            current_plan_id=None,
                            plan_started_at=None,
                            plan_expires_at=None,
                            is_reset=False,
                            reset_day=0,
                            last_traffic_reset_at=None,
                            created_at=created_at,
                            updated_at=now,
                        )
                        session.add(user)
                        session.flush()

                    mapped_plan_id = payload.package_mappings.get(entry.source_package_id)
                    if mapped_plan_id is not None:
                        user.current_plan_id = str(mapped_plan_id)
                        user.plan_started_at = entry.package_started_at or now
                        user.plan_expires_at = entry.package_expires_at
                        user.is_reset = entry.is_reset
                        user.reset_day = entry.reset_day if entry.is_reset else 0
                        user.updated_at = now

                    account = session.get(SubscriberAccount, entry.username)
                    if account is None or payload.replace_existing:
                        if account is not None:
                            revoke_subscriber_sessions(session, entry.username)
                        encrypted = None
                        recovery = []
                        if entry.totp_enabled:
                            raw = (
                                digest(entry.username) + "\n" + secret(entry.totp_secret)
                            ).encode()
                            encrypted = self.subscriber_auth.cipher.encrypt(raw).decode()
                            recovery = [
                                "legacy:" + secret(value).lower()
                                for value in entry.recovery_code_hashes
                            ]
                        if account is None:
                            account = SubscriberAccount(username=entry.username)
                            session.add(account)
                        account.password_hash = secret(entry.password_hash)
                        account.version = str(uuid4())
                        account.totp_secret = encrypted
                        account.last_totp_step = -1
                        account.recovery_hashes = recovery
                        account.pending_secret = account.pending_session_id = None
                        account.pending_expires_at = 0

                    if entry.token is None:
                        continue
                    token = session.get(ProductUserSubscriptionTokenModel, entry.username)
                    if token is None or payload.replace_existing:
                        if token is None:
                            token = ProductUserSubscriptionTokenModel(
                                username=entry.username, created_at=created_at
                            )
                            session.add(token)
                        token.token = secret(entry.token)
                        token.short_code = secret(entry.generated_short_code)
                        token.custom_short_code = secret(entry.custom_short_code)
                        token.bearer_generation = LEGACY_SUBSCRIPTION_BEARER_GENERATION
                        token.updated_at = now
                        if not self.inventory.short_links_enabled:
                            self.inventory._rotate_subscription_bearer(session, token, now=now)

                for package in payload.bundle.packages:
                    mapped_plan_id = payload.package_mappings.get(package.source_id)
                    if mapped_plan_id is None or package.short_code is None:
                        continue
                    alias = session.scalar(
                        select(LegacySubscriptionPlanCodeModel).where(
                            LegacySubscriptionPlanCodeModel.source_package_id == package.source_id
                        )
                    )
                    if alias is None:
                        alias = LegacySubscriptionPlanCodeModel(
                            code=package.short_code,
                            source_package_id=package.source_id,
                            created_at=now,
                        )
                        session.add(alias)
                    alias.code = package.short_code
                    alias.plan_id = str(mapped_plan_id)
                    alias.source_name = package.name
                    alias.updated_at = now

                for entry in payload.bundle.subscription_profiles:
                    profile = session.scalar(
                        select(SubscriptionProfileModel).where(
                            SubscriptionProfileModel.legacy_source_id == entry.source_id
                        )
                    )
                    if profile is not None and not payload.replace_existing:
                        continue
                    created_at = entry.created_at or now
                    if profile is None:
                        profile = SubscriptionProfileModel(
                            id=str(uuid4()),
                            legacy_source_id=entry.source_id,
                            created_at=created_at,
                        )
                        session.add(profile)
                    migration_warnings = []
                    if entry.raw_output:
                        migration_warnings.append(
                            "Legacy raw output needs an Open Node managed profile before use"
                        )
                    if entry.template_filename:
                        migration_warnings.append(
                            f"Legacy template not mapped: {entry.template_filename}"
                        )
                    if entry.selected_custom_rule_ids or entry.selected_override_script_ids:
                        migration_warnings.append(
                            "Legacy rules and override scripts were not imported"
                        )
                    profile.owner_username = entry.owner_username
                    profile.name = entry.name
                    profile.description = entry.description
                    profile.node_ids = []
                    profile.clash_template_id = None
                    profile.enabled = not entry.raw_output
                    profile.sort_order = entry.sort_order
                    profile.source_type = entry.source_type
                    profile.source_filename = entry.filename
                    profile.source_template_filename = entry.template_filename
                    profile.legacy_file_short_code = entry.file_short_code
                    profile.legacy_custom_short_code = entry.custom_short_code
                    profile.legacy_selected_node_ids = entry.selected_node_ids
                    profile.legacy_selected_tags = entry.selected_tags
                    profile.migration_warnings = migration_warnings
                    profile.expires_at = entry.expires_at
                    profile.updated_at = entry.updated_at or now
                    session.flush()
                    session.execute(
                        delete(SubscriptionProfileAssignmentModel).where(
                            SubscriptionProfileAssignmentModel.profile_id == profile.id
                        )
                    )
                    for username in entry.assigned_usernames:
                        session.add(
                            SubscriptionProfileAssignmentModel(
                                profile_id=profile.id,
                                username=username,
                                created_at=now,
                            )
                        )
                session.flush()
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise LegacyMMWXMigrationError(
                    "Migration collided with current user or subscription-link state"
                ) from exc
        return LegacyMMWXImportResponse(preview=preview)
