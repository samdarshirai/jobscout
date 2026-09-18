from jobscout.tracing import configure_tracing


def test_configure_tracing_installs_a_client_that_scrubs_inputs(monkeypatch):
    monkeypatch.setenv("CANDIDATE_NAME", "Jane Doe")
    monkeypatch.setenv("CANDIDATE_EMPLOYER", "Foo GmbH")
    captured = {}

    class _FakeClient:
        def __init__(self, hide_inputs=None, hide_outputs=None):
            captured["hide_inputs"] = hide_inputs
            captured["hide_outputs"] = hide_outputs

    monkeypatch.setattr("jobscout.tracing.Client", _FakeClient)
    monkeypatch.setattr("jobscout.tracing.ls.configure", lambda client: captured.setdefault("client", client))

    configure_tracing()

    assert isinstance(captured["client"], _FakeClient)
    assert captured["hide_inputs"]("Jane Doe works at Foo GmbH") == "[CANDIDATE] works at [EMPLOYER]"
    assert captured["hide_outputs"] is captured["hide_inputs"]
