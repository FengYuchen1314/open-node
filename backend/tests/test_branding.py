"""Branding uses only its configured database; it never contacts external services."""

import json
import re
import sqlite3
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from open_node.domain.branding import (
    BRANDING_ERROR_MESSAGES,
    BRANDING_MAX_REVISION,
    BrandingError,
    BrandingPublicRead,
    BrandingSettingsRead,
    BrandingSettingsUpdate,
)
from open_node.services.auth import AuthStore
from open_node.services.branding import BrandingBase, BrandingSettingsModel, BrandingStore
from open_node.services.inventory import (
    Base,
    InventoryStore,
    ProductUserModel,
    SubscriptionPlanModel,
)
from open_node.services.notifications import NotificationStore
from pydantic import ValidationError
from sqlalchemy import event, inspect, text
from sqlalchemy.exc import IntegrityError, OperationalError

MARKER = "private-fixture-detail-must-not-escape"
TABLE = "site_branding_settings"


@pytest.fixture
def inventory(tmp_path):
    value = InventoryStore("sqlite:///" + str(tmp_path / "branding.db"))
    value.create_schema()
    yield value
    value._engine.dispose()


@pytest.fixture
def store(inventory):
    value = BrandingStore(inventory)
    value.create_schema()
    return value


def payload(revision=0, site="公开站点", brand="公开品牌"):
    return BrandingSettingsUpdate(
        expected_revision=revision,
        site_title=site,
        brand_title=brand,
    )


def error(code, call, *, status):
    with pytest.raises(BrandingError) as captured:
        call()
    failure = captured.value
    assert failure.code == code and failure.status_code == status
    assert str(failure) == BRANDING_ERROR_MESSAGES[code]
    assert MARKER not in str(failure) and MARKER not in repr(failure)
    return failure


def business_snapshot(inventory):
    with inventory._engine.connect() as connection:
        result = {}
        for table in inspect(connection).get_table_names():
            if table == TABLE or table.startswith("sqlite_"):
                continue
            quoted = table.replace('"', '""')
            result[table] = sorted(
                [tuple(row) for row in connection.exec_driver_sql(f'SELECT * FROM "{quoted}"')],
                key=repr,
            )
        return result


def business_fixture(inventory):
    auth = AuthStore(str(inventory._engine.url))
    auth.set_administrator("fixture-admin", "fixture-branding-password-only")
    authentication = auth.login("fixture-admin", "fixture-branding-password-only", 3600)
    assert authentication and authentication.token and authentication.identity
    now = datetime.now(UTC)
    plan_id = str(uuid4())
    with inventory._session() as session:
        session.add(
            SubscriptionPlanModel(
                id=plan_id,
                name="Unchanged plan",
                traffic_limit_bytes=100,
                cycle_days=30,
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        session.add(
            ProductUserModel(
                username="unchanged-user",
                display_name="Unchanged user",
                role="user",
                is_active=True,
                current_plan_id=plan_id,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
    NotificationStore(inventory, None).create_schema()
    return auth, authentication


def test_exact_public_and_admin_contracts(store):
    assert store.get_settings().model_dump() == {
        "site_title": "Open Node",
        "brand_title": "Open Node",
        "revision": 0,
        "license_required": False,
    }
    public = store.get_public()
    assert type(public) is BrandingPublicRead
    assert public.model_dump() == {
        "site_title": "Open Node",
        "brand_title": "Open Node",
        "license_required": False,
    }
    assert set(json.loads(public.model_dump_json())) == {
        "site_title",
        "brand_title",
        "license_required",
    }


def test_three_fixed_error_codes_and_unknown_code_fallback():
    assert set(BRANDING_ERROR_MESSAGES) == {
        "branding_invalid_request",
        "branding_revision_conflict",
        "branding_storage_unavailable",
    }
    value = BrandingError(422, MARKER)
    assert value.code == "branding_invalid_request"
    assert str(value) == BRANDING_ERROR_MESSAGES[value.code]
    assert MARKER not in repr(value)


@pytest.mark.parametrize("field", ["site_title", "brand_title"])
@pytest.mark.parametrize("value", [None, True, False, 1, 1.0, [], {}, b"Brand"])
def test_title_types_are_strict_and_validation_does_not_echo(field, value):
    request = {"expected_revision": 0, "site_title": "Site", "brand_title": "Brand", field: value}
    with pytest.raises(ValidationError) as captured:
        BrandingSettingsUpdate.model_validate(request)
    assert "input_value" not in str(captured.value)


@pytest.mark.parametrize("value", [None, True, False, 0.0, "0", -1, BRANDING_MAX_REVISION + 1])
def test_revision_input_is_a_bounded_strict_integer(value):
    with pytest.raises(ValidationError):
        payload(revision=value)


@pytest.mark.parametrize("revision", [0, 1, BRANDING_MAX_REVISION])
def test_safe_revision_boundary_is_accepted_in_contract(revision):
    assert payload(revision=revision).expected_revision == revision
    assert (
        BrandingSettingsRead(
            revision=revision,
            site_title="Site",
            brand_title="Brand",
        ).revision
        == revision
    )


@pytest.mark.parametrize("field", ["site_title", "brand_title", "expected_revision"])
def test_every_update_field_is_required(field):
    request = {"expected_revision": 0, "site_title": "Site", "brand_title": "Brand"}
    del request[field]
    with pytest.raises(ValidationError):
        BrandingSettingsUpdate.model_validate(request)


@pytest.mark.parametrize("extra", ["revision", "logo_url", "license_required", MARKER])
def test_unknown_fields_and_even_their_names_do_not_echo(extra):
    with pytest.raises(ValidationError) as captured:
        BrandingSettingsUpdate.model_validate(
            {
                "expected_revision": 0,
                "site_title": "Site",
                "brand_title": "Brand",
                extra: MARKER,
            }
        )
    assert MARKER not in str(captured.value)


@pytest.mark.parametrize("value", [True, 0, 1, "false", None])
def test_public_license_flag_is_exact_false(value):
    with pytest.raises(ValidationError):
        BrandingPublicRead(site_title="Site", brand_title="Brand", license_required=value)


@pytest.mark.parametrize("field,maximum", [("site_title", 80), ("brand_title", 40)])
def test_lengths_use_trimmed_unicode_codepoints_not_utf16(field, maximum):
    request = {"expected_revision": 0, "site_title": "Site", "brand_title": "Brand"}
    request[field] = "\u2003 \u00a0" + "🚀" * maximum + "\u00a0 \u3000"
    value = BrandingSettingsUpdate.model_validate(request)
    assert getattr(value, field) == "🚀" * maximum
    request[field] = "🚀" * (maximum + 1)
    with pytest.raises(ValidationError):
        BrandingSettingsUpdate.model_validate(request)


def test_unicode_is_preserved_without_nfc_and_html_is_only_text(store):
    decomposed = "Cafe\u0301 👩\u200d💻 فارسی\u200cنام"
    html = '<script>alert("text")</script>'
    assert unicodedata.normalize("NFC", decomposed) != decomposed
    result = store.update_settings(payload(site="  " + decomposed + "\u3000", brand=html))
    assert result.site_title == decomposed and result.brand_title == html
    assert store.get_public().brand_title == html
    assert json.loads(store.get_public().model_dump_json())["site_title"] == decomposed


@pytest.mark.parametrize("field", ["site_title", "brand_title"])
@pytest.mark.parametrize(
    "character",
    [
        "\x00",
        "\x01",
        "\t",
        "\n",
        "\r",
        "\x7f",
        "\x85",
        "\ud800",
        "\udfff",
        "\u2028",
        "\u2029",
        "\u00ad",
        "\u061c",
        "\u200b",
        "\u200e",
        "\u200f",
        "\u202a",
        "\u202b",
        "\u202c",
        "\u202d",
        "\u202e",
        "\u2060",
        "\u2066",
        "\u2067",
        "\u2068",
        "\u2069",
        "\ufeff",
        "\U000e0001",
    ],
)
def test_forbidden_unicode_categories_are_rejected_even_before_trim(field, character):
    request = {"expected_revision": 0, "site_title": "Site", "brand_title": "Brand"}
    for value in (character + "Brand", "Br" + character + "and", "Brand" + character):
        request[field] = value
        with pytest.raises(ValidationError):
            BrandingSettingsUpdate.model_validate(request)


@pytest.mark.parametrize(
    "value",
    ["", " ", "\u00a0\u3000", "\u200c", "\u200d", "\u200c \u200d", "\u0301", "\u200d\ufe0f"],
)
def test_names_need_a_visible_letter_number_punctuation_or_symbol(value):
    with pytest.raises(ValidationError):
        payload(site=value)


@pytest.mark.parametrize(
    "value", ["中\u200d文", "نا\u200cم", "👩\u200d💻", "e\u0301", "1", "!", "©", "A\ue000"]
)
def test_legitimate_unicode_names_and_joiners_are_allowed(value):
    assert payload(site=value, brand=value).site_title == value


def test_schema_is_independent_and_constructor_and_reads_do_not_initialize(inventory):
    assert BrandingBase.metadata is not Base.metadata
    assert set(BrandingBase.metadata.tables) == {TABLE}
    assert TABLE not in Base.metadata.tables
    value = BrandingStore(inventory)
    before = set(inspect(inventory._engine).get_table_names())
    assert TABLE not in before
    error("branding_storage_unavailable", value.get_settings, status=503)
    error("branding_storage_unavailable", value.get_public, status=503)
    error("branding_storage_unavailable", lambda: value.update_settings(payload()), status=503)
    assert set(inspect(inventory._engine).get_table_names()) == before
    value.create_schema()
    assert set(inspect(inventory._engine).get_table_names()) == before | {TABLE}


def test_each_success_updates_both_titles_and_advances_once_even_when_values_match(store):
    for revision in range(1, 5):
        saved = store.update_settings(payload(revision=revision - 1))
        assert saved.revision == revision
        assert (saved.site_title, saved.brand_title) == ("公开站点", "公开品牌")
    assert store.get_settings().revision == 4


def test_stale_cas_never_updates_either_field(store):
    saved = store.update_settings(payload())
    error(
        "branding_revision_conflict",
        lambda: store.update_settings(payload(0, "Wrong site", "Wrong brand")),
        status=409,
    )
    assert store.get_settings() == saved


def test_store_revalidates_constructed_or_copied_models(store):
    invalid = payload().model_copy(update={"brand_title": "\n" + MARKER})
    error("branding_invalid_request", lambda: store.update_settings(invalid), status=422)
    assert store.get_settings().revision == 0


def test_schema_restarts_keep_custom_text_and_revision(store):
    saved = store.update_settings(payload())
    store.create_schema()
    second = InventoryStore(str(store.inventory._engine.url))
    try:
        restarted = BrandingStore(second)
        restarted.create_schema()
        restarted.create_schema()
        assert restarted.get_settings() == saved
    finally:
        second._engine.dispose()


def test_two_simultaneous_schema_initializations_have_one_default_row(inventory):
    other = InventoryStore(str(inventory._engine.url))
    values = [BrandingStore(inventory), BrandingStore(other)]
    ready = threading.Barrier(2)

    def initialize(value):
        ready.wait(timeout=5)
        value.create_schema()
        return value.get_settings()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(initialize, values))
        assert all(value.revision == 0 for value in results)
        with inventory._engine.connect() as connection:
            assert (
                connection.execute(text("SELECT COUNT(*) FROM site_branding_settings")).scalar()
                == 1
            )
    finally:
        other._engine.dispose()


def test_concurrent_cas_across_independent_stores_has_exactly_one_winner(store):
    inventories = [InventoryStore(str(store.inventory._engine.url)) for _ in range(6)]
    ready = threading.Barrier(len(inventories))

    def save(index):
        value = BrandingStore(inventories[index])
        ready.wait(timeout=5)
        try:
            result = value.update_settings(payload(0, f"Site {index}", f"Brand {index}"))
            return "success", index, result
        except BrandingError as failure:
            return failure.code, index, failure.status_code

    try:
        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(save, range(6)))
        winners = [result for result in results if result[0] == "success"]
        losers = [result for result in results if result[0] != "success"]
        assert len(winners) == 1 and len(losers) == 5
        assert all(value[0] == "branding_revision_conflict" and value[2] == 409 for value in losers)
        saved = store.get_settings()
        assert saved.revision == 1 and saved == winners[0][2]
        assert (saved.site_title, saved.brand_title) == (
            f"Site {winners[0][1]}",
            f"Brand {winners[0][1]}",
        )
    finally:
        for inventory in inventories:
            inventory._engine.dispose()


def test_concurrent_public_reads_never_observe_torn_title_pairs(store):
    ready = threading.Barrier(4)

    def writer():
        ready.wait(timeout=5)
        for revision in range(1, 31):
            store.update_settings(payload(revision - 1, f"Site {revision}", f"Brand {revision}"))

    def reader():
        ready.wait(timeout=5)
        for _ in range(70):
            saved = store.get_public()
            if saved.site_title == "Open Node":
                assert saved.brand_title == "Open Node"
            else:
                assert saved.brand_title == "Brand " + saved.site_title.removeprefix("Site ")

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(writer), *(executor.submit(reader) for _ in range(3))]
        for future in futures:
            future.result(timeout=20)
    assert store.get_settings().revision == 30


def test_revision_exhaustion_is_storage_failure_without_overflow(store):
    with store.inventory._engine.begin() as connection:
        connection.execute(
            text("UPDATE site_branding_settings SET revision = :revision"),
            {"revision": BRANDING_MAX_REVISION},
        )
    before = store.get_settings()
    error(
        "branding_storage_unavailable",
        lambda: store.update_settings(payload(BRANDING_MAX_REVISION)),
        status=503,
    )
    assert store.get_settings() == before
    assert "revision" not in store.get_public().model_dump()


@pytest.mark.parametrize("operation", ["get_settings", "get_public", "update_settings"])
def test_unsupported_dialect_is_not_initialized_or_connected_and_methods_fail_closed(operation):
    def no_session():
        raise AssertionError("Unsupported storage must not be contacted")

    inventory = SimpleNamespace(
        _engine=SimpleNamespace(dialect=SimpleNamespace(name="mysql")),
        _session=no_session,
    )
    value = BrandingStore(inventory)
    error("branding_storage_unavailable", value.create_schema, status=503)
    method = getattr(value, operation)
    error(
        "branding_storage_unavailable",
        lambda: method(payload()) if operation == "update_settings" else method(),
        status=503,
    )


def test_real_sqlite_statement_failure_rolls_back_both_titles(store):
    saved = store.update_settings(payload())
    with store.inventory._engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TRIGGER branding_reject_update BEFORE UPDATE ON site_branding_settings "
            f"BEGIN SELECT RAISE(ABORT, '{MARKER}'); END"
        )
    error(
        "branding_storage_unavailable",
        lambda: store.update_settings(payload(1, "New site", "New brand")),
        status=503,
    )
    assert store.get_settings() == saved


def test_actual_sql_cas_rowcount_is_required(store):
    with store.inventory._engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TRIGGER branding_ignore_update BEFORE UPDATE ON site_branding_settings "
            "BEGIN SELECT RAISE(IGNORE); END"
        )
    error("branding_revision_conflict", lambda: store.update_settings(payload()), status=409)
    assert store.get_settings().revision == 0


def test_failure_after_update_before_response_materialization_rolls_back(store):
    saved = store.get_settings()
    reads = 0

    def fail_second_read(_connection, _cursor, statement, _parameters, _context, _many):
        nonlocal reads
        if statement.lstrip().upper().startswith("SELECT") and TABLE in statement:
            reads += 1
            if reads == 2:
                raise OperationalError(MARKER, {}, RuntimeError(MARKER))

    event.listen(store.inventory._engine, "before_cursor_execute", fail_second_read)
    try:
        error("branding_storage_unavailable", lambda: store.update_settings(payload()), status=503)
    finally:
        event.remove(store.inventory._engine, "before_cursor_execute", fail_second_read)
    assert reads == 2 and store.get_settings() == saved


def test_failure_before_dbapi_commit_rolls_back_and_does_not_poison_the_pool(store):
    saved = store.get_settings()

    def fail_commit(_connection):
        raise OperationalError(MARKER, {}, RuntimeError(MARKER))

    event.listen(store.inventory._engine, "commit", fail_commit)
    try:
        error("branding_storage_unavailable", lambda: store.update_settings(payload()), status=503)
    finally:
        event.remove(store.inventory._engine, "commit", fail_commit)
    assert store.get_settings() == saved
    with sqlite3.connect(store.inventory._engine.url.database) as connection:
        assert connection.execute(
            "SELECT revision, site_title, brand_title FROM site_branding_settings"
        ).fetchone() == (saved.revision, saved.site_title, saved.brand_title)
    assert store.update_settings(payload()).revision == 1


def test_failure_after_actual_commit_requires_read_reconciliation_not_false_rollback(store):
    def fail_after_commit(_session):
        raise OperationalError(MARKER, {}, RuntimeError(MARKER))

    event.listen(store.inventory._session_factory, "after_commit", fail_after_commit)
    try:
        error("branding_storage_unavailable", lambda: store.update_settings(payload()), status=503)
    finally:
        event.remove(store.inventory._session_factory, "after_commit", fail_after_commit)
    saved = store.get_settings()
    assert saved.revision == 1
    assert (saved.site_title, saved.brand_title) == (payload().site_title, payload().brand_title)
    error("branding_revision_conflict", lambda: store.update_settings(payload()), status=409)
    assert store.get_settings() == saved


def test_failed_initialization_rolls_back_ddl_and_does_not_leave_a_half_default(inventory):
    value = BrandingStore(inventory)

    def fail_insert(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("INSERT INTO SITE_BRANDING_SETTINGS"):
            raise OperationalError(MARKER, {}, RuntimeError(MARKER))

    event.listen(inventory._engine, "before_cursor_execute", fail_insert)
    try:
        error("branding_storage_unavailable", value.create_schema, status=503)
    finally:
        event.remove(inventory._engine, "before_cursor_execute", fail_insert)
    assert not inspect(inventory._engine).has_table(TABLE)
    value.create_schema()
    assert value.get_settings().revision == 0


@pytest.mark.parametrize("operation", ["get_settings", "get_public", "create_schema"])
def test_read_or_schema_database_failures_are_fixed_not_raw_sql(store, operation):
    def fail_read(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT") and TABLE in statement:
            raise OperationalError(MARKER, {}, RuntimeError(MARKER))

    event.listen(store.inventory._engine, "before_cursor_execute", fail_read)
    try:
        error("branding_storage_unavailable", getattr(store, operation), status=503)
    finally:
        event.remove(store.inventory._engine, "before_cursor_execute", fail_read)


@pytest.mark.parametrize(
    "field,bad",
    [
        ("site_title", ""),
        ("site_title", "x" * 81),
        ("site_title", " Bad padding "),
        ("site_title", "Bad\ntext"),
        ("site_title", b"bytes"),
        ("site_title", 7),
        ("site_title", None),
        ("brand_title", "x" * 41),
        ("brand_title", "\u200d"),
        ("brand_title", "Bad\u202etext"),
        ("brand_title", None),
        ("revision", -1),
        ("revision", 0.0),
        ("revision", "0"),
        ("revision", BRANDING_MAX_REVISION + 1),
        ("revision", None),
    ],
)
def test_corrupt_stored_values_are_not_exposed_or_repaired(inventory, field, bad):
    row = {"id": 1, "revision": 0, "site_title": "Site", "brand_title": "Brand", field: bad}
    with inventory._engine.begin() as connection:
        # A deliberately weak legacy/corrupt schema tests the read boundary even
        # when normal SQLite constraints have been bypassed outside this store.
        connection.exec_driver_sql(
            "CREATE TABLE site_branding_settings "
            "(id PRIMARY KEY, revision, site_title, brand_title)"
        )
        connection.execute(
            text(
                "INSERT INTO site_branding_settings "
                "VALUES (:id, :revision, :site_title, :brand_title)"
            ),
            row,
        )
    value = BrandingStore(inventory)
    for method in (value.get_settings, value.get_public, value.create_schema):
        error("branding_storage_unavailable", method, status=503)
    error("branding_storage_unavailable", lambda: value.update_settings(payload()), status=503)
    with inventory._engine.connect() as connection:
        actual = connection.execute(text("SELECT * FROM site_branding_settings")).mappings().one()
        assert dict(actual) == row


@pytest.mark.parametrize(
    "rows", [
        [], [(None, 0, "Site", "Brand")], [(2, 0, "Site", "Brand")],
        [(1, 0, "Site", "Brand"), (2, 0, "Extra", "Extra")],
    ]
)
def test_missing_or_non_singleton_row_is_unavailable_without_implicit_reset(inventory, rows):
    with inventory._engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE site_branding_settings "
            "(id PRIMARY KEY, revision, site_title, brand_title)"
        )
        for row in rows:
            connection.exec_driver_sql(
                "INSERT INTO site_branding_settings VALUES (?, ?, ?, ?)", row
            )
    value = BrandingStore(inventory)
    error("branding_storage_unavailable", value.get_public, status=503)
    error("branding_storage_unavailable", value.create_schema, status=503)
    with inventory._engine.connect() as connection:
        assert [
            tuple(row) for row in connection.exec_driver_sql("SELECT * FROM site_branding_settings")
        ] == rows


def test_normal_schema_enforces_the_single_row_and_revision_bounds(store):
    with store.inventory._engine.connect() as connection:
        assert {column["name"] for column in inspect(connection).get_columns(TABLE)} == {
            "id",
            "revision",
            "site_title",
            "brand_title",
        }
    assert BrandingSettingsModel.__table__.metadata is BrandingBase.metadata
    before = store.get_settings()
    invalid = [
        ("INSERT INTO site_branding_settings VALUES (2, 0, 'Site', 'Brand')", {}),
        ("UPDATE site_branding_settings SET revision = :bad", {"bad": -1}),
        ("UPDATE site_branding_settings SET revision = :bad", {"bad": BRANDING_MAX_REVISION + 1}),
        ("UPDATE site_branding_settings SET revision = :bad", {"bad": "bad"}),
        ("UPDATE site_branding_settings SET site_title = :bad", {"bad": b"bytes"}),
        ("UPDATE site_branding_settings SET brand_title = :bad", {"bad": b"bytes"}),
    ]
    for statement, parameters in invalid:
        with pytest.raises(IntegrityError), store.inventory._engine.begin() as connection:
            connection.execute(text(statement), parameters)
    assert store.get_settings() == before


def test_branding_never_writes_other_business_tables_or_creates_secret_files(inventory, tmp_path):
    auth, _authentication = business_fixture(inventory)
    before = business_snapshot(inventory)
    files_before = set(path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file())
    statements = []

    def track_writes(_connection, _cursor, statement, _parameters, _context, _many):
        if re.match(r"\s*(INSERT|UPDATE|DELETE|REPLACE)\b", statement, flags=re.IGNORECASE):
            statements.append(statement)
            assert re.match(
                r"\s*(INSERT\s+INTO|UPDATE|DELETE\s+FROM|REPLACE\s+INTO)\s+site_branding_settings\b",
                statement,
                flags=re.IGNORECASE,
            ), "Branding attempted an unrelated business write"

    event.listen(inventory._engine, "before_cursor_execute", track_writes)
    try:
        value = BrandingStore(inventory)
        value.create_schema()
        value.get_public()
        value.get_settings()
        value.update_settings(payload())
        value.create_schema()
        error("branding_revision_conflict", lambda: value.update_settings(payload()), status=409)
    finally:
        event.remove(inventory._engine, "before_cursor_execute", track_writes)
    assert len(statements) == 2
    assert business_snapshot(inventory) == before
    assert (
        set(path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file())
        == files_before
    )
    assert auth.app_name == "Open Node"
    auth.engine.dispose()


def test_independent_sqlite_backup_restore_preserves_branding_business_rows_and_session(
    store, tmp_path
):
    auth, authentication = business_fixture(store.inventory)
    saved = store.update_settings(payload(site="冷恢复站点", brand="冷恢复品牌"))
    before = business_snapshot(store.inventory)
    database = Path(store.inventory._engine.url.database)
    auth.engine.dispose()
    store.inventory._engine.dispose()
    restored_path = tmp_path / "restored-copy.db"
    with sqlite3.connect(database) as source, sqlite3.connect(restored_path) as destination:
        source.backup(destination)
    restored_inventory = InventoryStore("sqlite:///" + str(restored_path))
    restored_auth = AuthStore(str(restored_inventory._engine.url))
    try:
        restored = BrandingStore(restored_inventory)
        restored.create_schema()
        assert restored.get_settings() == saved
        assert restored.get_public().brand_title == "冷恢复品牌"
        assert business_snapshot(restored_inventory) == before
        identity = restored_auth.authenticate(authentication.token, 3600)
        assert identity and identity.username == authentication.identity.username
        assert identity.csrf_token == authentication.identity.csrf_token
        assert restored_auth.app_name == "Open Node"
    finally:
        restored_auth.engine.dispose()
        restored_inventory._engine.dispose()
