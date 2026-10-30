"""The API's generated documentation: docs/openapi.json kept current, and
every endpoint described. No database needed."""

from fastapi.testclient import TestClient
from pydantic import BaseModel

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


# FastAPI's own models for a 422: not ours to describe.
_FASTAPI_MODELS = {"HTTPValidationError", "ValidationError"}


def test_every_parameter_is_described() -> None:
    for path, operations in openapi.spec()["paths"].items():
        for method, operation in operations.items():
            for parameter in operation.get("parameters", []):
                where = f"{method.upper()} {path}: {parameter['in']} {parameter['name']}"
                assert parameter.get("description"), f"{where} has no description"


def test_every_model_and_field_is_described() -> None:
    schemas = openapi.spec()["components"]["schemas"]
    for name, schema in schemas.items():
        if name in _FASTAPI_MODELS:
            continue
        assert schema.get("description"), f"{name} has no docstring"
        for field, details in schema.get("properties", {}).items():
            assert details.get("description"), f"{name}.{field} has no description"


def test_every_request_body_has_an_example() -> None:
    schemas = openapi.spec()["components"]["schemas"]
    for path, operations in openapi.spec()["paths"].items():
        for method, operation in operations.items():
            body = operation.get("requestBody")
            if body:
                ref = body["content"]["application/json"]["schema"]["$ref"]
                name = ref.rsplit("/", 1)[-1]
                assert schemas[name].get("examples"), f"{method.upper()} {path}: {name}"


def test_the_examples_are_valid_bodies() -> None:
    import blt.rubrik.schemas
    import blt.schemas

    for module in (blt.schemas, blt.rubrik.schemas):
        for model in vars(module).values():
            if isinstance(model, type) and issubclass(model, BaseModel) and model is not BaseModel:
                for example in model.model_json_schema().get("examples", []):
                    model.model_validate(example)
