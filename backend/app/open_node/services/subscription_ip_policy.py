from datetime import UTC, datetime
from ipaddress import IPv6Address, ip_address, ip_network

from open_node.domain.subscriptions import SubscriptionIpPolicyRead, SubscriptionIpPolicyUpdate
from open_node.services.inventory import (
    ProductUserModel,
    ProductUserNotFoundError,
    SubscriptionIpPolicyModel,
)


class SubscriptionIpPolicy:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def _read(username, row):
        networks = list(row.networks or []) if row else []
        return SubscriptionIpPolicyRead(
            username=username,
            enabled=bool(networks),
            networks=networks,
            updated_at=row.updated_at if row else None,
        )

    @staticmethod
    def _user(session, username):
        user = session.get(ProductUserModel, username)
        if user is None or user.removal_id:
            raise ProductUserNotFoundError(f"user not found: {username}")
        return user

    def read(self, username):
        with self.store._session() as session:
            self._user(session, username)
            return self._read(username, session.get(SubscriptionIpPolicyModel, username))

    def update(self, username, payload: SubscriptionIpPolicyUpdate):
        with self.store._session() as session:
            self._user(session, username)
            row = session.get(SubscriptionIpPolicyModel, username)
            if row is None:
                row = SubscriptionIpPolicyModel(username=username)
                session.add(row)
            row.networks = list(payload.networks)
            row.updated_at = datetime.now(UTC)
            session.commit()
            session.refresh(row)
            return self._read(username, row)

    def allowed(self, username, peer):
        with self.store._session() as session:
            row = session.get(SubscriptionIpPolicyModel, username)
            networks = list(row.networks or []) if row else []
        if not networks:
            return True
        try:
            address = ip_address(peer)
        except ValueError:
            return False
        candidates = [address]
        if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
            candidates.append(address.ipv4_mapped)
        return any(
            candidate.version == network.version and candidate in network
            for value in networks
            for network in [ip_network(value, strict=False)]
            for candidate in candidates
        )
