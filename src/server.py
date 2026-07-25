"""Legacy, unused Microdot Wi-Fi setup portal.

USB configuration replaced this onboarding flow. The module remains only
as explicit cleanup debt and is never imported by the running app.
"""
import asyncio
import config
import machine
from lib.microdot import Microdot, Response

if False:
    from typing import Any

app = Microdot()

_SETUP_FORM = """<!doctype html>
<html><head><title>Bus Display Setup</title></head>
<body style="font-family: sans-serif; max-width: 400px; margin: 40px auto;">
<h1>Bus Display Wi-Fi Setup</h1>
<form method="POST" action="/save">
  <label>Wi-Fi SSID<br><input name="ssid" required></label><br><br>
  <label>Wi-Fi Password<br><input name="password" type="password"></label><br><br>
  <button type="submit">Save &amp; Reboot</button>
</form>
</body></html>"""

_SAVED_PAGE = """<!doctype html>
<html><body style="font-family: sans-serif; max-width: 400px; margin: 40px auto;">
<h1>Saved</h1>
<p>Rebooting and joining your Wi-Fi network now.</p>
</body></html>"""


@app.route("/")
async def setup_form(request: "Any") -> "Any":
    return Response(body=_SETUP_FORM, headers={"Content-Type": "text/html"})


@app.route("/save", methods=["POST"])
async def save_wifi(request: "Any") -> "Any":
    ssid = request.form.get("ssid", "")
    password = request.form.get("password", "")
    if not ssid:
        return Response(body="SSID is required", status_code=400)

    cfg = config.load()
    cfg["wifi"] = {"ssid": ssid, "password": password}
    config.save(cfg)
    print("server: saved wifi config for ssid =", ssid, "-- rebooting")

    async def reboot_soon() -> None:
        await asyncio.sleep(1)
        machine.reset()

    asyncio.create_task(reboot_soon())
    return Response(body=_SAVED_PAGE, headers={"Content-Type": "text/html"})
