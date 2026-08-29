"""Guarded import of identities from the active MMWX main-line database."""

import binascii
import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

import pyotp
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from open_node.domain.legacy_mmwx import LegacyMMWXImportPreview, LegacyMMWXImportResponse
from open_node.services.inventory import (
    ProductUserModel,
    ProductUserSubscriptionTokenModel,
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
                [hidden(token.token), hidden(token.short_code), hidden(token.custom_short_code)]
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
                        token.updated_at = now
                session.flush()
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise LegacyMMWXMigrationError(
                    "Migration collided with current user or subscription-link state"
                ) from exc
        return LegacyMMWXImportResponse(preview=preview)
