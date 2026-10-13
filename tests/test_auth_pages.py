"""The sign-in and sign-up pages: the sign-up route is easy to see, and the basics for phones and screen readers are in place."""
import re


def test_the_sign_in_page_has_a_clear_request_an_account_button(browser):
    html = browser.get("/login").text
    assert re.search(r'<a class="btn btn--ghost" href="/signup">\s*Request an account', html)  # a real button, not a small link
    assert "An admin approves every new account" in html


def test_the_sign_up_page_has_a_clear_way_back(browser):
    html = browser.get("/signup").text
    assert re.search(r'<a class="btn btn--ghost" href="/login">Sign in instead</a>', html)


def test_the_forms_work_well_on_phones_and_password_managers(browser):
    login, signup = browser.get("/login").text, browser.get("/signup").text
    for html in (login, signup):
        assert 'name="viewport"' in html and 'rel="stylesheet" href="/static/auth.css' in html
        assert 'id="show"' in html and 'aria-pressed="false"' in html  # show / hide the password
        assert "<h1" in html  # one page heading
    assert 'autocomplete="username"' in login and 'autocomplete="current-password"' in login
    assert 'autocomplete="new-password"' in signup and 'autocapitalize="none"' in signup
    for field in ('for="username"', 'for="password"'):
        assert field in login and field in signup  # every field has a label


def test_errors_are_announced_to_screen_readers(browser):
    bad = browser.login("nobody", "nope")
    assert bad.status_code == 401 and 'role="alert"' in bad.text
    short = browser.post("/signup", {"name": "A B", "username": "ab", "password": "x" * 12, "again": "x" * 12})
    assert short.status_code == 400 and 'role="alert"' in short.text


def test_the_stylesheet_is_served(browser):
    r = browser.get("/static/auth.css")
    assert r.status_code == 200 and "auth-shell" in r.text


def test_the_sign_up_page_guides_the_password(browser):
    html = browser.get("/signup").text
    assert 'id="meter"' in html and 'aria-live="polite"' in html and "At least 10 characters" in html
    assert 'name="website"' in html  # the bot trap is still there
