"""The API's generated documentation: docs/openapi.json kept current, and
every endpoint described. No database needed."""

from fastapi.testclient import TestClient

from blt.api import openapi


def test_the_committed_openapi_json_is_current() -> None:
    assert openapi.up_to_date(openapi.SPEC_PATH), "docs/openapi.json is stale: run blt-openapi"


def test_every_endpoint_has_a_summary_a_description_and_a_group() -> None:
    for path, operations in openapi.spec()["paths"].items():
        for method, operation in operations.items():
            where = f"{method.upper()} {path}"
            assert operation.get("summary"), f"{where} has no summary"
            assert operation.get("description"), f"{where} has no docstring"
            assert operation.get("tags"), f"{where} is in no group"


def test_everything_but_the_health_check_asks_for_the_key() -> None:
    for path, operations in openapi.spec()["paths"].items():
        for method, operation in operations.items():
            needs_key = bool(operation.get("security"))
            assert needs_key == (path != "/healthz"), f"{method.upper()} {path}"


def test_the_docs_pages_are_served() -> None:
    from blt.api.app import create_app
    from blt.api.settings import ApiSettings

    settings = ApiSettings(database_url="postgresql+psycopg://x@localhost/x", api_key="k")
    client = TestClient(create_app(settings))

    assert client.get("/docs").status_code == 200
    assert client.get("/redoc").status_code == 200
    assert client.get("/openapi.json").json()["info"]["title"] == "blt API"


def test_each_endpoint_is_in_exactly_one_group() -> None:
    for path, operations in openapi.spec()["paths"].items():
        for method, operation in operations.items():
            assert len(operation["tags"]) == 1, f"{method.upper()} {path}: {operation['tags']}"


def test_every_path_but_the_health_check_is_versioned_and_named_by_source() -> None:
    for path in openapi.spec()["paths"]:
        if path != "/healthz":
            assert path.startswith(("/v1/sources/commvault/", "/v1/sources/rubrik/")), path


def test_the_paths_from_before_v1_are_left_out_of_the_docs() -> None:
    paths = openapi.spec()["paths"]
    assert not [p for p in paths if p.startswith(("/commcells/", "/rubrik/"))]
