"""Turning browser notifications on and off, and the service worker that shows them."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from .. import auth, db, notify, web

router = APIRouter()


@router.get("/sw.js", include_in_schema=False)
def service_worker():
    """Served from the root so it can show notifications for the whole site."""
    return FileResponse(web.HERE / "static" / "sw.js", media_type="text/javascript", headers={"Service-Worker-Allowed": "/"})


@router.post("/push/subscribe")
def subscribe(database=Depends(auth.get_db), person=Depends(auth.current_person),
              endpoint: str = Form(""), p256dh: str = Form(""), auth_key: str = Form("", alias="auth")):
    if not notify.configured():
        raise HTTPException(status_code=400, detail="Notifications aren't set up on this site yet.")
    endpoint, p256dh, auth_key = endpoint.strip(), p256dh.strip(), auth_key.strip()
    if not notify.allowed_endpoint(endpoint) or not (20 < len(p256dh) <= 200) or not (8 < len(auth_key) <= 100):
        raise HTTPException(status_code=400, detail="That browser's notification details weren't valid.")
    sub = database.query(db.PushSubscription).filter(db.PushSubscription.endpoint == endpoint).first()
    if sub is None:
        sub = db.PushSubscription(endpoint=endpoint)
        database.add(sub)
    sub.person_id, sub.p256dh, sub.auth = person.id, p256dh, auth_key  # a browser belongs to whoever last turned it on
    database.commit()
    return JSONResponse({"ok": True})


@router.post("/push/unsubscribe")
def unsubscribe(database=Depends(auth.get_db), person=Depends(auth.current_person), endpoint: str = Form("")):
    database.query(db.PushSubscription).filter(db.PushSubscription.person_id == person.id,
                                               db.PushSubscription.endpoint == endpoint.strip()).delete()
    database.commit()
    return JSONResponse({"ok": True})


@router.post("/push/test")
def test(person=Depends(auth.current_person)):
    sent = notify.deliver(person.id, {"title": "Job Buddy", "body": "Notifications are working.", "url": "/", "tag": "test"}) \
        if notify.configured() else 0
    return JSONResponse({"ok": bool(sent), "sent": sent})
