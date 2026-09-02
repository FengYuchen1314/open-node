import hashlib
import io
import json
import os
import stat
import urllib.error

import pytest
from open_node.services import agent_bootstrap_release as releases


class Response(io.BytesIO):
    def __init__(self, data=b"", *, status=200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = headers or {}


class Client:
    def __init__(self, *actions):
        self.actions = list(actions)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        action = self.actions.pop(0)
        if isinstance(action, Exception):
            raise action
        return action


def descriptor(data=b"verified panel proxy artifact"):
    return releases.AgentArtifact(
        filename="fixture.whl",
        path=releases.ARTIFACT_PATH + "fixture.whl",
        sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),
        upstream=(
            releases.PROJECT_RELEASE_BASE + "/agent-v0.0.1/fixture.whl"
        ),
    )


def store(tmp_path, monkeypatch, client, artifact):
    monkeypatch.setattr(releases, "release_artifacts", lambda: {artifact.filename: artifact})
    return releases.AgentArtifactStore(tmp_path / "cache", opener=client)


def test_panel_proxy_pins_redirect_size_hash_and_private_cache(tmp_path, monkeypatch):
    data = b"verified panel proxy artifact"
    artifact = descriptor(data)
    location = "https://release-assets.githubusercontent.com/object?signature=opaque"
    client = Client(
        urllib.error.HTTPError(
            artifact.upstream, 302, "redirect", {"Location": location}, None
        ),
        Response(data, headers={"Content-Length": str(len(data))}),
    )
    proxy = store(tmp_path, monkeypatch, client, artifact)
    path, returned = proxy.get(artifact.filename)
    assert returned == artifact
    assert path.read_bytes() == data
    assert [request.full_url for request in client.requests] == [artifact.upstream, location]
    if os.name != "nt":
        assert stat.S_IMODE(proxy.directory.stat().st_mode) == 0o700
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert proxy.get(artifact.filename) == (path, artifact)
    assert len(client.requests) == 2


@pytest.mark.parametrize(
    "location",
    [
        "http://release-assets.githubusercontent.com/file",
        "https://evil.example/file",
        "https://release-assets.githubusercontent.com.evil.test/file",
        "https://user:password@release-assets.githubusercontent.com/file",
        "https://release-assets.githubusercontent.com:444/file",
        "https://objects.githubusercontent.com/file#fragment",
        "https://release-assets.githubusercontent.com/file\nheader: value",
        "https://release-assets.githubusercontent.com\\@evil.test/file",
    ],
)
def test_panel_proxy_rejects_redirects_outside_fixed_https_hosts(
    tmp_path, monkeypatch, location,
):
    artifact = descriptor()
    client = Client(
        urllib.error.HTTPError(
            artifact.upstream, 302, "redirect", {"Location": location}, None
        )
    )
    proxy = store(tmp_path, monkeypatch, client, artifact)
    with pytest.raises(releases.AgentBootstrapArtifactUnavailable):
        proxy.get(artifact.filename)
    assert len(client.requests) == 1
    assert not (proxy.directory / artifact.filename).exists()


@pytest.mark.parametrize(
    ("body", "headers"),
    [
        (b"tampered panel proxy artifact", None),
        (b"verified panel proxy artifact", {"Content-Length": "999"}),
        (
            b"verified panel proxy artifact",
            {"Content-Length": "29", "Content-Encoding": "gzip"},
        ),
    ],
)
def test_panel_proxy_never_publishes_unverified_bytes(
    tmp_path, monkeypatch, body, headers,
):
    artifact = descriptor()
    response_headers = headers or {"Content-Length": str(artifact.size)}
    proxy = store(
        tmp_path, monkeypatch, Client(Response(body, headers=response_headers)), artifact
    )
    with pytest.raises(releases.AgentBootstrapArtifactUnavailable):
        proxy.get(artifact.filename)
    assert not (proxy.directory / artifact.filename).exists()
    assert not list(proxy.directory.glob(".download-*"))


def test_panel_proxy_fails_closed_on_modified_cache_without_refetching(tmp_path, monkeypatch):
    data = b"verified panel proxy artifact"
    artifact = descriptor(data)
    client = Client(Response(data, headers={"Content-Length": str(len(data))}))
    proxy = store(tmp_path, monkeypatch, client, artifact)
    path, _ = proxy.get(artifact.filename)
    path.write_bytes(b"x" * len(data))
    with pytest.raises(
        releases.AgentBootstrapArtifactUnavailable, match="failed verification"
    ):
        proxy.get(artifact.filename)
    assert len(client.requests) == 1


def test_public_manifest_contains_only_panel_paths_and_pinned_integrity():
    manifest = releases.release_manifest()
    artifacts = [
        manifest["agent"][key] for key in ("wheel", "bootstrap", "build")
    ] + [manifest["xray"]["archive"], *manifest["mihomo"]["assets"].values()]
    assert manifest["schema_version"] == 2
    assert {artifact["filename"] for artifact in artifacts} == set(
        releases.release_artifacts()
    )
    for artifact in artifacts:
        assert set(artifact) == {"filename", "path", "sha256", "bytes"}
        assert artifact["path"] == releases.ARTIFACT_PATH + artifact["filename"]
        assert artifact["bytes"] > 0
        assert len(artifact["sha256"]) == 64
        assert "github" not in artifact["path"].lower()


def test_public_manifest_mihomo_hashes_match_the_canonical_release_resource():
    manifest = releases.release_manifest()
    distributed = releases.release_artifacts()
    pinned = releases.files("open_node.resources").joinpath("mihomo-release.json")
    source = json.loads(pinned.read_text())
    assert manifest["mihomo"]["version"] == source["version"] == "v1.19.30"
    for platform_name, artifact in manifest["mihomo"]["assets"].items():
        assert artifact["sha256"] == source["assets"][platform_name]["sha256"]
        assert artifact["bytes"] == source["assets"][platform_name]["compressed_bytes"]
        assert distributed[artifact["filename"]].upstream == (
            f"{releases.PROJECT_RELEASE_BASE}/{manifest['agent']['tag']}/"
            f"{artifact['filename']}"
        )
