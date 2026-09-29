import json
from unittest.mock import MagicMock, patch

from django.db.utils import OperationalError
from django.test import Client

from infrastructure.common.services import HealthService, database_is_ready


def test_liveness() -> None:
    response = Client().get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json()["data"] == {"status": "ok", "checks": {}}


def test_readiness(db: None) -> None:
    response = Client().get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json()["data"] == {"status": "ok", "checks": {"database": "ok"}}


def test_versioned_swagger_lists_registered_openapi_specs() -> None:
    from infrastructure.common.registry import load_api_registry

    response = Client().get("/api/docs")
    page = response.content.decode()

    assert response.status_code == 200
    assert '"urls"' in page
    for version in load_api_registry():
        assert f"/api/{version}/openapi.json" in page


def test_the_swagger_page_can_actually_render_that_selector() -> None:
    """The `urls` list is read by the topbar, which two other settings supply.

    Without them the page still answers 200, still carries the list, and still
    shows "No API definition provided" -- it never fetches a document at all.
    So this asserts the parts that make the list mean something: the standalone
    preset script, and the layout that renders the topbar reading it.
    """
    page = Client().get("/api/docs").content.decode()

    assert "swagger-ui-standalone-preset.js" in page
    assert '"layout": "StandaloneLayout"' in page
    assert "SwaggerUIStandalonePreset" in page
    # A member of the bundle rather than the standalone script's own global is
    # undefined at runtime, which is the shape the original bug took.
    assert "SwaggerUIBundle.SwaggerUIStandalonePreset" not in page


def test_the_page_says_so_when_nobody_is_signed_into_the_admin() -> None:
    """A reader the server cannot identify is told, rather than left guessing.

    The session-to-token bridge can only answer 401 for this reader, so the page
    must neither promise an Authorize it will not fill in nor fire the request:
    it says what is missing, and offers the admin login that would fix it.
    """
    page = Client().get("/api/docs").content.decode()

    assert 'data-state="anonymous"' in page
    assert "Not signed in to the admin" in page
    assert "/admin/login/?next=" in page
    # The one flag the script reads before deciding whether to ask at all.
    assert "const signedInAsStaff = false;" in page


def test_the_page_greets_a_staff_session_and_goes_looking_for_a_token(db: None) -> None:
    from django.contrib.auth import get_user_model

    staff = get_user_model()._default_manager.create_user(
        username="docsreader", email="docsreader@example.test", password="irrelevant"
    )
    staff.is_staff = True
    staff.save(update_fields=["is_staff"])
    client = Client()
    client.force_login(staff)

    page = client.get("/api/docs").content.decode()

    assert 'data-state="pending"' in page
    assert "Signed in as docsreader" in page
    assert "const signedInAsStaff = true;" in page


def test_a_signed_in_reader_who_is_not_staff_is_told_which_refusal_it_is(db: None) -> None:
    """403 is not 401, and the difference is the whole of what to do next.

    An ordinary user holding a session gets no token from the bridge either, but
    for a reason signing in again cannot fix -- so the page names that reason and
    spends no request discovering it.
    """
    from django.contrib.auth import get_user_model

    user = get_user_model()._default_manager.create_user(
        username="reader", email="reader@example.test", password="irrelevant"
    )
    client = Client()
    client.force_login(user)

    page = client.get("/api/docs").content.decode()

    assert 'data-state="denied"' in page
    assert "not a staff account" in page
    assert "const signedInAsStaff = false;" in page


def test_redoc_renders_the_same_schema_the_default_swagger_page_opens_on() -> None:
    """The reading view of the pair, on the document Swagger opens on.

    Two pages over one schema is only a feature if they agree about which schema
    it is, so this pins ReDoc to the same version `/api/docs` starts at rather
    than to whichever one happened to be registered last.
    """
    from infrastructure.common.registry import load_api_registry

    default_version = next(iter(load_api_registry()))

    response = Client().get("/api/redoc")
    page = response.content.decode()

    assert response.status_code == 200
    assert "redoc.standalone.js" in page
    assert f"/api/{default_version}/openapi.json" in page


def test_redoc_publishes_a_page_for_each_registered_version() -> None:
    """ReDoc has no selector, so each version is a page of its own.

    It renders the one document it is handed. A project with two registered
    versions reaches the second through the path, not through a topbar.
    """
    from infrastructure.common.registry import load_api_registry

    for version in load_api_registry():
        page = Client().get(f"/api/{version}/redoc")

        assert page.status_code == 200
        assert f"/api/{version}/openapi.json" in page.content.decode()


def test_redoc_refuses_a_version_nobody_registered() -> None:
    """A version in the path that is not in the registry is a 404, not a page.

    The route sits ahead of the version includes so that it can answer for any
    `/api/<something>/redoc`, which means it is the one that has to say no.
    """
    assert Client().get("/api/v99/redoc").status_code == 404


def test_the_swagger_page_offers_redoc_and_can_follow_the_selector_there() -> None:
    """The link is beside Swagger, and means whichever version is on screen.

    The topbar switches documents without reloading, so a link resolved at
    render time would send a reader who is looking at v2 to v1's reference. The
    page carries the addressable form of the URL for the script to fill in.
    """
    page = Client().get("/api/docs").content.decode()

    assert 'href="/api/redoc"' in page
    assert 'data-version-url="/api/__version__/redoc"' in page
    # And substitutes it, rather than carrying a placeholder nothing replaces.
    assert 'redocLink.dataset.versionUrl.replace("__version__"' in page


def test_every_operation_is_grouped_under_a_described_tag() -> None:
    """No operation may fall outside the document's own tag list.

    Swagger renders a group per tag whether or not the document declares one,
    so an undeclared tag is not a visibly broken page -- it is a group with no
    description, sorted wherever the paths happened to land. This is the check
    that a new router cannot arrive that way: it has to say which group it joins
    and what that group is for.
    """
    schema = Client().get("/api/v1/openapi.json").json()
    declared = {tag["name"]: tag.get("description", "") for tag in schema["tags"]}
    used = {
        tag
        for operations in schema["paths"].values()
        for operation in operations.values()
        for tag in operation.get("tags", [])
    }

    assert used, "the document published no tagged operations at all"
    assert used <= set(declared), f"operations tagged but not described: {used - set(declared)}"
    assert not set(declared) - used, f"tags described but unused: {set(declared) - used}"
    assert all(declared.values()), f"tags with no description: {sorted(declared)}"


def test_tag_names_match_the_names_their_apps_carry_in_the_admin() -> None:
    """The same app, spelled the same way in both places a reader meets it.

    Deriving a tag by title-casing an app label produces "Auth - Sms Code" and
    "OAuth - Github", next to an admin whose sidebar spells both correctly. A
    tag that differs from an app's verbose name only in case is that bug.
    """
    from django.apps import apps

    schema = Client().get("/api/v1/openapi.json").json()
    verbose_names = {str(config.verbose_name) for config in apps.get_app_configs()}
    folded = {name.casefold(): name for name in verbose_names}

    for tag in schema["tags"]:
        spelled = folded.get(tag["name"].casefold())
        # A tag naming no app at all -- Users, Auth - Token, Health -- is
        # deliberate: it is named for what a client reads, not for the app that
        # happens to answer it.
        assert spelled is None or spelled == tag["name"], (
            f"the document says {tag['name']!r} and the admin says {spelled!r}"
        )


def test_the_published_document_declares_no_key_twice() -> None:
    """A JSON object may not carry the same key twice, and Swagger enforces it.

    Swagger UI parses the document as YAML -- JSON being a subset of it -- and
    a duplicated mapping key is fatal there, not a warning: the page renders
    "Parser error" and nothing else, whatever the rest of the document says.

    Python lets a dict hold both `200` and `"200"`, and `json.dumps` writes them
    out as the same key. Django Ninja keys its own responses map by `int` and
    deep-merges `openapi_extra` into it, so one route documenting a status by
    string is enough to break the whole page. Parsed from the raw bytes, because
    `response.json()` silently keeps the last of the two and sees nothing wrong.
    """
    response = Client().get("/api/v1/openapi.json")
    duplicated: list[str] = []

    def keep_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        seen: dict[str, object] = {}
        for key, value in pairs:
            if key in seen:
                duplicated.append(key)
            seen[key] = value
        return seen

    json.loads(response.content, object_pairs_hook=keep_duplicates)

    assert not duplicated, f"the document declares these keys twice: {duplicated}"


def test_versioned_openapi_schema_contains_health_routes() -> None:
    response = Client().get("/api/v1/openapi.json")

    assert response.status_code == 200
    assert "/api/v1/health/live" in response.json()["paths"]
    assert "/api/v1/health/ready" in response.json()["paths"]


@patch.object(HealthService, "database_is_ready", return_value=False)
def test_readiness_returns_503_when_database_is_unavailable(
    database_ready: object,
) -> None:
    response = Client().get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json()["data"] == {
        "status": "unavailable",
        "checks": {"database": "unavailable"},
    }


@patch("infrastructure.common.services.connections")
def test_database_readiness_handles_operational_error(connections: MagicMock) -> None:
    connections.__getitem__.side_effect = OperationalError

    assert database_is_ready() is False
