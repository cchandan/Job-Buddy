"""Installing Job Buddy to an iPhone or Android Home Screen: the manifest, the icons, and the tags every page carries."""
import json
import struct

from app import notify
from conftest import signed_in


def png_size(data):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def test_the_manifest_describes_an_installable_app(browser):
    r = browser.get("/manifest.webmanifest")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/manifest+json")
    m = json.loads(r.text)
    assert m["name"] == "Job Buddy" and m["start_url"] == "/" and m["scope"] == "/"
    assert m["display"] == "standalone"  # an iPhone only allows notifications from a standalone web app
    assert m["theme_color"] == "#2536e8" and m["background_color"]
    sizes = {(i["sizes"], i["purpose"]) for i in m["icons"]}
    assert ("192x192", "any") in sizes and ("512x512", "any") in sizes and ("512x512", "maskable") in sizes


def test_every_icon_in_the_manifest_exists_at_its_stated_size(browser):
    for icon in json.loads(browser.get("/manifest.webmanifest").text)["icons"]:
        r = browser.get(icon["src"])
        assert r.status_code == 200 and r.headers["content-type"] == "image/png", icon["src"]
        side = int(icon["sizes"].split("x")[0])
        assert png_size(r.content) == (side, side), icon["src"]


def test_the_iphone_home_screen_icon_exists(browser):
    r = browser.get("/static/icons/apple-touch-icon.png")
    assert r.status_code == 200 and png_size(r.content) == (180, 180)


def test_the_manifest_is_public_so_a_phone_can_fetch_it_before_signing_in(browser):
    assert browser.get("/manifest.webmanifest").status_code == 200
    assert browser.get("/static/icons/icon-192.png").status_code == 200


def test_every_kind_of_page_links_the_manifest_and_the_icon(people, browser):
    pages = {"landing": browser.get("/").text, "login": browser.get("/login").text, "signup": browser.get("/signup").text,
             "user": signed_in("wife").get("/").text, "admin": signed_in("dad").get("/admin").text}
    for name, html in pages.items():
        assert '<link rel="manifest" href="/manifest.webmanifest">' in html, name
        assert 'rel="apple-touch-icon" href="/static/icons/apple-touch-icon.png"' in html, name
        assert 'name="apple-mobile-web-app-title" content="Job Buddy"' in html, name


def test_the_notification_card_explains_the_iphone_route(people, monkeypatch):
    public, private = notify.generate_keys()
    monkeypatch.setenv("VAPID_PUBLIC_KEY", public)
    monkeypatch.setenv("VAPID_PRIVATE_KEY", private)
    html = signed_in("wife").get("/profile").text
    assert "On an iPhone" in html and "Add to Home Screen" in html


def test_the_script_tells_an_iphone_in_a_browser_tab_what_to_do():
    js = open("app/static/push.js").read()
    assert "Add to Home Screen" in js and "iOS 16.4" in js and "navigator.standalone" in js
