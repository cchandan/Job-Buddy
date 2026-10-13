"""Chandan's name appears on the public homepage, and only there."""
from conftest import signed_in


def test_the_homepage_says_whose_it_is(browser):
    html = browser.get("/").text
    assert "Job Buddy is part of <strong>Chandan’s portfolio of solutions</strong>" in html
    assert "© 2026 Chandan" in html  # the year follows the calendar (fixed to 2026 by the tests)
    assert "Chandan’s Job Buddy</title>" in html and '<meta name="author" content="Chandan">' in html


def test_it_is_only_on_the_homepage(people, browser):
    pages = {"login": browser.get("/login").text, "signup": browser.get("/signup").text,
             "user home": signed_in("wife").get("/").text, "admin home": signed_in("dad").get("/admin").text}
    for name, html in pages.items():
        assert "portfolio of solutions" not in html and "Chandan’s Job Buddy" not in html, name


def test_a_signed_in_person_is_not_shown_the_homepage_credit(people):
    assert "portfolio of solutions" not in signed_in("wife").get("/").text
