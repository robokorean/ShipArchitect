import pytest
@pytest.fixture(autouse=True)
def isolated_app_data(tmp_path,monkeypatch):
    monkeypatch.setenv('SHIP_ARCHITECT_DATA_DIR',str(tmp_path/'appdata'))
