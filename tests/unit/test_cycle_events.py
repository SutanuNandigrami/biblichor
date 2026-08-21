from types import SimpleNamespace

from endless_library import pipeline


def test_poll_sources_records_progress_for_each_enabled_account(monkeypatch):
    accounts = [
        SimpleNamespace(id=11, source="goodreads"),
        SimpleNamespace(id=12, source="kindlebangla"),
    ]
    event_calls = []
    deps = SimpleNamespace(
        sources=SimpleNamespace(list_enabled=lambda: accounts),
        events=SimpleNamespace(append=lambda **kwargs: event_calls.append(kwargs)),
    )
    added_by_id = {11: 2, 12: 0}
    monkeypatch.setattr(
        pipeline,
        "poll_source_account",
        lambda _deps, account_id: added_by_id[account_id],
    )

    assert pipeline.poll_sources(deps) == 2
    assert event_calls == [
        {
            "book_id": None,
            "kind": "cycle",
            "message": "source 1/2 finished: goodreads; added 2",
            "meta": {
                "account_id": 11,
                "source": "goodreads",
                "position": 1,
                "total": 2,
                "added": 2,
            },
        },
        {
            "book_id": None,
            "kind": "cycle",
            "message": "source 2/2 finished: kindlebangla; added 0",
            "meta": {
                "account_id": 12,
                "source": "kindlebangla",
                "position": 2,
                "total": 2,
                "added": 0,
            },
        },
    ]
