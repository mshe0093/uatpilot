from uatpilot.evals.mutations import CATALOGUE, TRIAGE_SCENARIOS
from uatpilot.evals.proxy import Exchange, Reply

BY_NAME = {m.name: m for m in CATALOGUE}


def ex(method, path, query=None, headers=None, body=None):
    return Exchange(method, path, query or {}, headers or {}, body)


def apply(name, e, upstream):
    mode = BY_NAME[name].mode
    if mode.pre and (r := mode.pre(e)) is not None:
        return r
    return mode.post(e, upstream) if mode.post else upstream


def test_catalogue_is_well_formed():
    assert len(CATALOGUE) >= 20
    assert len({m.name for m in CATALOGUE}) == len(CATALOGUE)
    assert all(m.breaks and m.description for m in CATALOGUE)


def test_create_mutations_rewrite_only_the_target_field():
    booking = {
        "bookingid": 1,
        "booking": {
            "firstname": "a",
            "lastname": "b",
            "totalprice": 5,
            "depositpaid": True,
            "additionalneeds": "x",
        },
    }
    r = apply("create_price_off_by_one", ex("POST", "/booking"), Reply(200, booking))
    assert r.body["booking"]["totalprice"] == 6 and booking["booking"]["totalprice"] == 5
    r = apply("create_drops_bookingid", ex("POST", "/booking"), Reply(200, booking))
    assert "bookingid" not in r.body and r.body["booking"]["lastname"] == "b"
    # other endpoints untouched
    r = apply("create_price_off_by_one", ex("GET", "/booking/1"), Reply(200, booking))
    assert r.body is booking


def test_auth_guard_mutations_depend_on_token():
    m = "put_without_token_allowed"
    assert apply(m, ex("PUT", "/booking/1", body={"a": 1}), Reply(403, "Forbidden")).status == 200
    with_token = ex("PUT", "/booking/1", headers={"cookie": "token=abc"})
    assert apply(m, with_token, Reply(200, {})).status == 200  # forwarded unchanged


def test_missing_booking_200_only_rewrites_404():
    m = "missing_booking_200"
    assert apply(m, ex("GET", "/booking/9"), Reply(404, "Not Found")).status == 200
    assert apply(m, ex("GET", "/booking/9"), Reply(200, {"x": 1})).body == {"x": 1}


def test_triage_scenarios_have_valid_labels_and_fresh_modes():
    for sc in TRIAGE_SCENARIOS:
        assert sc.expected in ("real_regression", "flaky", "environment_issue")
        assert sc.mode() is not sc.mode()  # flaky budgets must not leak between cases
    flaky = next(s for s in TRIAGE_SCENARIOS if s.name == "flaky_once").mode()
    assert flaky.budget == 1
