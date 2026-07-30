"""End-to-end API tests for CSV import across profiles."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SAMPLE_CSV = """\
transaction_date,settlement_date,account_id,account_type,activity_type,activity_sub_type,direction,symbol,underlying_symbol,name,currency,quantity,unit_price,commission,net_cash_amount
2024-01-15,2024-01-17,WS-AAA,TFSA,Trade,BUY,LONG,AAPL,,Apple Inc,USD,10,150.0,0,-1500
2024-02-01,2024-02-03,WS-AAA,TFSA,Trade,SELL,LONG,AAPL,,Apple Inc,USD,5,160.0,0,800
"""


@pytest.fixture
def import_client(tmp_path, monkeypatch):
    """TestClient with auth disabled and isolated profile storage."""
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    (profiles_dir / "alice").mkdir()
    (profiles_dir / "bob").mkdir()

    monkeypatch.setattr("src.accounts_manager._PROFILES_DIR", profiles_dir)
    monkeypatch.setattr("src.transactions_loader._PROFILES_DIR", profiles_dir)
    monkeypatch.setattr("src.auth.middleware.AUTH_ENABLED", False)

    # Import app after patches so paths are used; re-bind module attributes used by server
    import src.api.server as server
    import src.accounts_manager as am
    import src.transactions_loader as tl

    monkeypatch.setattr(am, "_PROFILES_DIR", profiles_dir)
    monkeypatch.setattr(tl, "_PROFILES_DIR", profiles_dir)
    # server imports functions by name — they close over module paths, so patching
    # the manager modules is enough as long as get_*_path reads _PROFILES_DIR at call time.

    client = TestClient(server.app)
    return client, profiles_dir


def _preview(client, profile: str, csv_text: str = SAMPLE_CSV):
    return client.post(
        f"/api/import/preview?profile={profile}",
        files={"file": ("trades.csv", csv_text.encode(), "text/csv")},
        data={"profile": profile},
    )


def _confirm(client, profile: str, mapping: dict | None = None, csv_text: str = SAMPLE_CSV):
    data = {"profile": profile}
    if mapping is not None:
        import json
        data["mapping"] = json.dumps(mapping)
    return client.post(
        f"/api/import/confirm?profile={profile}",
        files={"file": ("trades.csv", csv_text.encode(), "text/csv")},
        data=data,
    )


class TestImportAcrossProfiles:
    def test_same_csv_imports_into_two_profiles(self, import_client):
        client, profiles_dir = import_client

        # Preview on empty alice — all new
        prev_a = _preview(client, "alice")
        assert prev_a.status_code == 200, prev_a.text
        body_a = prev_a.json()
        assert body_a["total_new"] == 2
        assert body_a["total_duplicates"] == 0
        assert body_a["profile"] == "alice"

        # Confirm into alice
        conf_a = _confirm(
            client,
            "alice",
            mapping={"WS-AAA": {"target": "new", "name": "TFSA"}},
        )
        assert conf_a.status_code == 200, conf_a.text
        assert conf_a.json()["transactions_imported"] == 2

        # Alice re-preview — all duplicates within alice
        prev_a2 = _preview(client, "alice")
        assert prev_a2.json()["total_new"] == 0
        assert prev_a2.json()["total_duplicates"] == 2

        # Bob is empty — same CSV must still be fully importable
        prev_b = _preview(client, "bob")
        assert prev_b.status_code == 200, prev_b.text
        body_b = prev_b.json()
        assert body_b["total_new"] == 2
        assert body_b["total_duplicates"] == 0
        assert body_b["profile"] == "bob"

        conf_b = _confirm(
            client,
            "bob",
            mapping={"WS-AAA": {"target": "new", "name": "TFSA"}},
        )
        assert conf_b.status_code == 200, conf_b.text
        assert conf_b.json()["transactions_imported"] == 2

        # Ledgers are isolated on disk
        alice_tx_files = list((profiles_dir / "alice").rglob("transactions.yaml"))
        bob_tx_files = list((profiles_dir / "bob").rglob("transactions.yaml"))
        assert alice_tx_files
        assert bob_tx_files
        assert alice_tx_files[0] != bob_tx_files[0]

    def test_foreign_account_id_rejected(self, import_client):
        client, _ = import_client

        # Seed alice with an account via import
        conf_a = _confirm(
            client,
            "alice",
            mapping={"WS-AAA": {"target": "new", "name": "TFSA"}},
        )
        assert conf_a.status_code == 200
        alice_accts = client.get("/api/accounts?profile=alice").json()["accounts"]
        alice_acct_id = alice_accts[0]["id"]

        # Bob must not accept alice's account id as a mapping target
        conf_b = _confirm(
            client,
            "bob",
            mapping={"WS-AAA": {"target": "existing", "account_id": alice_acct_id}},
        )
        assert conf_b.status_code == 400
        assert "not found in profile" in conf_b.json()["detail"]

    def test_profile_form_field_alone_works(self, import_client):
        client, _ = import_client
        # No query string — profile only in multipart form
        res = client.post(
            "/api/import/preview",
            files={"file": ("trades.csv", SAMPLE_CSV.encode(), "text/csv")},
            data={"profile": "bob"},
        )
        assert res.status_code == 200
        assert res.json()["total_new"] == 2
        assert res.json()["profile"] == "bob"
