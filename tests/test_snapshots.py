"""Snapshots: save the Job Board by hand, restore it later. Never deletes a job, never touches a Tracker, and a restore can be undone."""
import gzip
import json
from datetime import date, datetime

import pytest

from app import db, ingest, snapshots
from conftest import make_job, signed_in


def job(job_id):
    with db.SessionLocal() as s:
        row = s.get(db.Job, job_id)
        if row is not None:
            s.expunge(row)
        return row


def snaps():
    with db.SessionLocal() as s:
        return [(x.name, x.kind, x.job_count) for x in snapshots.listing(s)]


@pytest.fixture
def board(people):
    make_job("a1", title="Alpha", deadline=date(2026, 12, 1), found_by={"1": ["developer"]}, matched=[1],
             first_seen=datetime(2026, 9, 1, 8, 30), required_skills=["Python", "SQL"])
    make_job("b1", title="Bravo")
    return signed_in("dad")


def test_taking_a_snapshot_saves_every_job_in_a_compressed_copy(board):
    board.post("/admin/snapshots", {"name": "Before keywords", "note": "just in case"})
    assert snaps() == [("Before keywords", "manual", 2)]
    with db.SessionLocal() as s:
        snap = s.query(db.BoardSnapshot).one()
        rows = {r["id"]: r for r in snapshots.decode(snap.data)}
    assert set(rows) == {"a1", "b1"} and rows["a1"]["title"] == "Alpha" and rows["a1"]["found_by"] == {"1": ["developer"]}
    assert rows["a1"]["deadline"] == "2026-12-01" and snap.size_bytes == len(snap.data) and gzip.decompress(snap.data)
    page = board.get("/admin/snapshots").text
    assert "Before keywords" in page and "just in case" in page and "2 jobs right now" in page


def test_a_name_is_optional(board):
    board.post("/admin/snapshots", {})
    assert snaps()[0][0].startswith("Snapshot ")


def test_restoring_puts_every_changed_job_back_exactly_as_it_was(board):
    board.post("/admin/snapshots", {"name": "Good board"})
    with db.SessionLocal() as s:
        a = s.get(db.Job, "a1")
        a.title, a.deadline, a.found_by, a.required_skills = "Changed", date(2027, 1, 1), None, []
        a.closed_at, a.close_reason = datetime(2026, 10, 2), "page gone (404)"
        a.binned_at, a.bin_reason = datetime(2026, 10, 2), "Expired: page gone (404)"
        s.commit()
    board.post("/admin/snapshots/1/restore")
    a = job("a1")
    assert a.title == "Alpha" and a.deadline == date(2026, 12, 1) and a.found_by == {"1": ["developer"]}
    assert a.required_skills == ["Python", "SQL"] and a.first_seen == datetime(2026, 9, 1, 8, 30)
    assert a.closed_at is None and a.binned_at is None  # the bin state comes back too


def test_a_job_added_after_the_snapshot_goes_to_the_bin_not_the_shredder(board):
    board.post("/admin/snapshots", {"name": "Good board"})
    make_job("c1", title="Charlie, added later")
    board.post("/admin/snapshots/1/restore")
    c = job("c1")
    assert c is not None and c.binned_at is not None and c.bin_reason == "Added after the snapshot “Good board”"
    assert "Charlie" not in signed_in("wife").get("/board?status=").text
    assert "Charlie" in board.get("/admin/bin").text  # and it can be restored from the bin


def test_a_job_deleted_since_is_brought_back(board):
    board.post("/admin/snapshots", {"name": "Good board"})
    with db.SessionLocal() as s:
        s.delete(s.get(db.Job, "b1"))
        s.commit()
    board.post("/admin/snapshots/1/restore")
    assert job("b1") is not None and job("b1").title == "Bravo"


def test_no_tracker_is_touched_and_a_tracked_newer_job_stays_on_the_board(board):
    board.post("/admin/snapshots", {"name": "Good board"})
    make_job("c1", title="Tracked and newer")
    wife = signed_in("wife")
    wife.post("/board/c1/shortlist")
    wife.post("/board/a1/shortlist")
    with db.SessionLocal() as s:
        before = [(e.job_id, e.status, e.notes) for e in s.query(db.TrackerEntry).order_by(db.TrackerEntry.id)]
    board.post("/admin/snapshots/1/restore")
    with db.SessionLocal() as s:
        assert [(e.job_id, e.status, e.notes) for e in s.query(db.TrackerEntry).order_by(db.TrackerEntry.id)] == before
    assert job("c1").binned_at is None  # someone is tracking it, so it was not binned
    assert "Tracked and newer" in wife.get("/tracker").text


def test_every_restore_can_be_undone_by_the_automatic_snapshot_it_takes_first(board):
    board.post("/admin/snapshots", {"name": "Old board"})
    with db.SessionLocal() as s:
        s.get(db.Job, "a1").title = "Edited after the snapshot"
        s.commit()
    board.post("/admin/snapshots/1/restore")
    assert job("a1").title == "Alpha"
    assert snaps()[0] == ("Before restoring: Old board", "auto", 2)
    board.post("/admin/snapshots/2/restore")  # undo
    assert job("a1").title == "Edited after the snapshot"


def test_the_page_tells_the_admin_what_a_restore_did_and_how_to_undo_it(board):
    board.post("/admin/snapshots", {"name": "Good board"})
    make_job("c1", title="Later")
    board.post("/admin/snapshots/1/restore")
    page = board.get("/admin/snapshots").text.replace("&#39;", "'").replace("&#34;", '"')
    assert "Restored. 2 jobs put back as they were, 1 newer jobs moved to the bin" in page
    assert "To undo this, restore" in page and "Publish to send it to the live site" in page


def test_only_ten_snapshots_and_the_oldest_automatic_one_makes_room(board):
    for i in range(snapshots.MAX_SNAPSHOTS - 1):
        board.post("/admin/snapshots", {"name": f"Manual {i}"})
    board.post("/admin/snapshots/1/restore")  # the 10th: an automatic one
    assert len(snaps()) == snapshots.MAX_SNAPSHOTS
    board.post("/admin/snapshots/2/restore")  # another restore needs room: the old automatic one is dropped, never a manual one
    names = [n for n, kind, _ in snaps()]
    assert len(names) == snapshots.MAX_SNAPSHOTS and sum(k == "manual" for _, k, _ in snaps()) == snapshots.MAX_SNAPSHOTS - 1
    assert sum(k == "auto" for _, k, _ in snaps()) == 1


def test_a_full_set_of_manual_snapshots_refuses_another_politely(board):
    for i in range(snapshots.MAX_SNAPSHOTS):
        board.post("/admin/snapshots", {"name": f"Manual {i}"})
    board.post("/admin/snapshots", {"name": "One too many"})
    assert len(snaps()) == snapshots.MAX_SNAPSHOTS and "One too many" not in [n for n, _, _ in snaps()]
    assert f"There are already {snapshots.MAX_SNAPSHOTS} snapshots" in board.get("/admin/snapshots").text


def test_nothing_happens_while_a_refresh_is_running(board, monkeypatch):
    monkeypatch.setitem(ingest.STATE, "running", True)
    board.post("/admin/snapshots", {"name": "During a run"})
    assert snaps() == [] and "A refresh is running" in board.get("/admin/snapshots").text
    monkeypatch.setitem(ingest.STATE, "running", False)
    board.post("/admin/snapshots", {"name": "After"})
    monkeypatch.setitem(ingest.STATE, "running", True)
    board.post("/admin/snapshots/1/restore")
    assert len(snaps()) == 1  # no automatic snapshot either: the restore never started


def test_a_snapshot_can_be_downloaded_and_deleted(board):
    board.post("/admin/snapshots", {"name": "Keep me"})
    r = board.get("/admin/snapshots/1/download")
    assert r.status_code == 200 and r.headers["content-type"] == "application/gzip"
    assert "job-board-snapshot-1.json.gz" in r.headers["content-disposition"]
    assert {j["id"] for j in json.loads(gzip.decompress(r.content))["jobs"]} == {"a1", "b1"}
    board.post("/admin/snapshots/1/delete")
    assert snaps() == []
    assert board.get("/admin/snapshots/1/download").status_code == 404
    board.post("/admin/snapshots/1/restore")
    assert "no longer exists" in board.get("/admin/snapshots").text


def test_only_admins_can_use_any_of_it(board):
    board.post("/admin/snapshots", {"name": "Keep me"})
    wife = signed_in("wife")
    assert wife.get("/admin/snapshots").status_code == 403
    for url in ("/admin/snapshots", "/admin/snapshots/1/restore", "/admin/snapshots/1/delete"):
        assert wife.post(url, {}).status_code == 403, url
    assert wife.get("/admin/snapshots/1/download").status_code == 403
    assert len(snaps()) == 1
    assert "Snapshots" in board.get("/admin").text  # the sidebar link
