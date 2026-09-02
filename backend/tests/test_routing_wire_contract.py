from open_node.domain.inventory import AgentRoutingManageOperationRequest


def test_routing_request_uses_official_camel_case_and_keeps_legacy_input_compatible():
    burst = {"subjectSelector": ["proxy"], "pingConfig": {"interval": "5s"}}
    official = AgentRoutingManageOperationRequest.model_validate(
        {"action": "set", "routing": {"rules": []}, "burstObservatory": burst}
    )
    legacy = AgentRoutingManageOperationRequest.model_validate(
        {"action": "set", "routing": {"rules": []}, "burst_observatory": burst}
    )

    assert official.burst_observatory == burst
    assert "burst_observatory" in official.model_fields_set
    assert official.model_dump(mode="json", by_alias=True)["burstObservatory"] == burst
    assert legacy.burst_observatory == burst
