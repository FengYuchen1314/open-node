from uuid import uuid4

import pytest
from open_node.domain.plan_management import PlanUpdate
from open_node.domain.subscriptions import SubscriptionPlanCreate, SubscriptionPlanRead
from pydantic import ValidationError


def create_payload(node_ids):
    return {
        "name": "Multi-node plan",
        "traffic_limit_gb": 1,
        "node_ids": node_ids,
    }


@pytest.mark.parametrize("model", [SubscriptionPlanCreate, PlanUpdate])
def test_public_plan_writes_require_at_least_one_distinct_node(model):
    payload = create_payload([])
    if model is PlanUpdate:
        payload.update(
            expected_revision="a" * 64,
            acknowledge_runtime_restart=True,
        )
    with pytest.raises(ValidationError):
        model.model_validate(payload)

    node_id = uuid4()
    payload["node_ids"] = [node_id, node_id]
    with pytest.raises(ValidationError, match="distinct"):
        model.model_validate(payload)


def test_plan_read_can_surface_an_imported_empty_plan_for_repair():
    plan = SubscriptionPlanRead.model_validate(
        {
            **create_payload([]),
            "id": uuid4(),
            "traffic_limit_bytes": 1024**3,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )
    assert plan.node_ids == []
