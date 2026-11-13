"""Builds examples/suites/saucedemo.seed.json (a hand-written fixture, not agent output).

Kept as a script because the login steps repeat in every case; editing them here once is less
error-prone than editing 13 copies in JSON.
"""

import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOGIN = [
    {"action": "goto", "value": "/"},
    {"action": "fill", "selector": "#user-name", "value": "standard_user"},
    {"action": "fill", "selector": "#password", "value": "secret_sauce"},
    {"action": "click", "selector": "#login-button"},
    {"action": "expect_url", "value": "/inventory.html"},
]
LOGIN_HUMAN = ["Open the shop", "Log in as standard_user with password secret_sauce"]


def login(user: str = "standard_user", pw: str = "secret_sauce") -> list[dict]:
    steps = copy.deepcopy(LOGIN)
    steps[1]["value"], steps[2]["value"] = user, pw
    return steps


def dt(name: str) -> str:
    return f'[data-test="{name}"]'


def case(title, ref, priority, steps, expected, executable):
    return {
        "title": title,
        "requirement_ref": ref,
        "priority": priority,
        "preconditions": [],
        "steps": steps,
        "expected_result": expected,
        "executable": {"kind": "ui", "steps": executable},
    }


ADD_BACKPACK = {"action": "click", "selector": dt("add-to-cart-sauce-labs-backpack")}
OPEN_CART = {"action": "click", "selector": dt("shopping-cart-link")}

CASES = [
    case(
        "Valid customer login lands on the Products page",
        "AC-1.1",
        "high",
        ["Open the shop", "Enter username standard_user and password secret_sauce", "Press Login"],
        "The products page opens and shows the heading 'Products'.",
        [*login(), {"action": "expect_text", "selector": dt("title"), "value": "Products"}],
    ),
    case(
        "Locked-out account cannot log in and sees a locked-out message",
        "AC-1.2",
        "high",
        [
            "Open the shop",
            "Enter username locked_out_user and password secret_sauce",
            "Press Login",
        ],
        "The customer stays on the login page and sees a 'locked out' error.",
        [
            *login("locked_out_user")[:4],
            {"action": "expect_text", "selector": dt("error"), "value": "locked out"},
            {"action": "expect_visible", "selector": "#login-button"},
        ],
    ),
    case(
        "Wrong password shows a credentials-do-not-match error",
        "AC-1.3",
        "high",
        ["Open the shop", "Enter username standard_user and a wrong password", "Press Login"],
        "An error says the username and password do not match; still on the login page.",
        [
            *login(pw="wrong_password")[:4],
            {"action": "expect_text", "selector": dt("error"), "value": "do not match"},
            {"action": "expect_visible", "selector": "#login-button"},
        ],
    ),
    case(
        "Login with an empty username asks for the username",
        "AC-1.4",
        "medium",
        ["Open the shop", "Leave the username empty", "Press Login"],
        "An error says the username is required.",
        [
            {"action": "goto", "value": "/"},
            {"action": "click", "selector": "#login-button"},
            {"action": "expect_text", "selector": dt("error"), "value": "Username is required"},
        ],
    ),
    case(
        "Products page lists exactly six products",
        "AC-2.1",
        "medium",
        [*LOGIN_HUMAN, "Count the products shown"],
        "Exactly 6 products are listed.",
        [*login(), {"action": "expect_count", "selector": dt("inventory-item"), "value": "6"}],
    ),
    case(
        "Sorting by price low to high shows the $7.99 product first",
        "AC-2.2",
        "medium",
        [*LOGIN_HUMAN, "Choose sort 'Price (low to high)'"],
        "The first product costs $7.99.",
        [
            *login(),
            {"action": "select", "selector": dt("product-sort-container"), "value": "lohi"},
            {"action": "expect_text", "selector": dt("inventory-item-price"), "value": "$7.99"},
        ],
    ),
    case(
        "Sorting by name Z to A shows the red T-shirt first",
        "AC-2.3",
        "low",
        [*LOGIN_HUMAN, "Choose sort 'Name (Z to A)'"],
        "'Test.allTheThings() T-Shirt (Red)' is first.",
        [
            *login(),
            {"action": "select", "selector": dt("product-sort-container"), "value": "za"},
            {
                "action": "expect_text",
                "selector": dt("inventory-item-name"),
                "value": "Test.allTheThings() T-Shirt (Red)",
            },
        ],
    ),
    case(
        "Adding the backpack shows a cart badge of 1",
        "AC-3.1",
        "high",
        [*LOGIN_HUMAN, "Press 'Add to cart' on Sauce Labs Backpack"],
        "The cart badge shows 1.",
        [
            *login(),
            ADD_BACKPACK,
            {"action": "expect_text", "selector": dt("shopping-cart-badge"), "value": "1"},
        ],
    ),
    case(
        "Adding two products shows a badge of 2 and both in the cart",
        "AC-3.2",
        "high",
        [*LOGIN_HUMAN, "Add Sauce Labs Backpack", "Add Sauce Labs Bike Light", "Open the cart"],
        "The badge shows 2 and the cart lists both products.",
        [
            *login(),
            ADD_BACKPACK,
            {"action": "click", "selector": dt("add-to-cart-sauce-labs-bike-light")},
            {"action": "expect_text", "selector": dt("shopping-cart-badge"), "value": "2"},
            OPEN_CART,
            {"action": "expect_count", "selector": dt("inventory-item"), "value": "2"},
            {
                "action": "expect_text",
                "selector": dt("cart-list"),
                "value": "Sauce Labs Bike Light",
            },
        ],
    ),
    case(
        "Removing the only cart item empties the cart and clears the badge",
        "AC-3.3",
        "medium",
        [*LOGIN_HUMAN, "Add Sauce Labs Backpack", "Open the cart", "Press Remove"],
        "The cart is empty and no badge is shown.",
        [
            *login(),
            ADD_BACKPACK,
            OPEN_CART,
            {"action": "click", "selector": dt("remove-sauce-labs-backpack")},
            {"action": "expect_count", "selector": dt("inventory-item"), "value": "0"},
            {"action": "expect_count", "selector": dt("shopping-cart-badge"), "value": "0"},
        ],
    ),
    case(
        "A customer can complete checkout and sees the thank-you message",
        "AC-4.1",
        "high",
        [
            *LOGIN_HUMAN,
            "Add Sauce Labs Backpack",
            "Open the cart and press Checkout",
            "Enter first name Ada, last name Lovelace, postal code 50450",
            "Press Continue",
            "Press Finish",
        ],
        "The confirmation 'Thank you for your order!' is shown.",
        [
            *login(),
            ADD_BACKPACK,
            OPEN_CART,
            {"action": "click", "selector": dt("checkout")},
            {"action": "fill", "selector": dt("firstName"), "value": "Ada"},
            {"action": "fill", "selector": dt("lastName"), "value": "Lovelace"},
            {"action": "fill", "selector": dt("postalCode"), "value": "50450"},
            {"action": "click", "selector": dt("continue")},
            {"action": "expect_url", "value": "checkout-step-two"},
            {"action": "click", "selector": dt("finish")},
            {
                "action": "expect_text",
                "selector": dt("complete-header"),
                "value": "Thank you for your order!",
            },
        ],
    ),
    case(
        "Checkout with an empty first name asks for the first name",
        "AC-4.2",
        "medium",
        [
            *LOGIN_HUMAN,
            "Add Sauce Labs Backpack",
            "Open the cart and press Checkout",
            "Leave first name empty, enter last name and postal code",
            "Press Continue",
        ],
        "An error says the first name is required.",
        [
            *login(),
            ADD_BACKPACK,
            OPEN_CART,
            {"action": "click", "selector": dt("checkout")},
            {"action": "fill", "selector": dt("lastName"), "value": "Lovelace"},
            {"action": "fill", "selector": dt("postalCode"), "value": "50450"},
            {"action": "click", "selector": dt("continue")},
            {"action": "expect_text", "selector": dt("error"), "value": "First Name is required"},
        ],
    ),
    case(
        "Logout from the menu returns to the login page",
        "AC-5.1",
        "medium",
        [*LOGIN_HUMAN, "Open the menu", "Choose Logout"],
        "The login page is shown again.",
        [
            *login(),
            {"action": "click", "selector": "#react-burger-menu-btn"},
            {"action": "click", "selector": "#logout_sidebar_link"},
            {"action": "expect_visible", "selector": "#login-button"},
        ],
    ),
]

if __name__ == "__main__":
    doc = {
        "requirements": "../requirements/saucedemo.md",
        "target_kind": "ui",
        "base_url": "https://www.saucedemo.com",
        "note": "Hand-written by a human as a fixture for the runner, triage and dashboard. "
        "NOT generated by the agent.",
        "test_cases": CASES,
    }
    out = ROOT / "examples" / "suites" / "saucedemo.seed.json"
    out.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(CASES)} cases)")
