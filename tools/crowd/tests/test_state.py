from crowd.state import paths_for


def test_a_run_on_the_canonical_host_takes_over_the_files_of_its_alias(tmp_path):
    config = tmp_path / "crowd.yaml"
    (tmp_path / ".crowd-state-srv2160986.hstgr.cloud.json").write_text("{}")
    (tmp_path / "crowd-srv2160986.hstgr.cloud.log").write_text("line\n")
    for api in ("https://konklaavi.tech/api", "https://srv2160986.hstgr.cloud/api"):
        assert paths_for(config, api) == (
            tmp_path / ".crowd-state-konklaavi.tech.json",
            tmp_path / "crowd-konklaavi.tech.log",
        )
    assert (tmp_path / ".crowd-state-konklaavi.tech.json").read_text() == "{}"
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        ".crowd-state-konklaavi.tech.json",
        "crowd-konklaavi.tech.log",
    ]
