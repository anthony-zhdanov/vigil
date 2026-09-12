from __future__ import annotations

import html
import json
from typing import Any, Callable

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.booking.runtime import BookingRuntime, SetupSession, SetupView
from app.booking.security import sign_csrf, verify_csrf


SESSION_COOKIE = "vigil_booking_setup"
RuntimeGetter = Callable[[], BookingRuntime | None]


def create_booking_setup_router(get_runtime: RuntimeGetter) -> APIRouter:
    router = APIRouter()

    def runtime() -> BookingRuntime:
        value = get_runtime()
        if value is None:
            raise HTTPException(status_code=503, detail="Booking setup is unavailable")
        return value

    def session(request: Request, value: BookingRuntime) -> SetupSession:
        result = value.get_setup_session(request.cookies.get(SESSION_COOKIE))
        if result is None:
            raise HTTPException(status_code=401, detail="Setup session is invalid")
        return result

    def verify_request_csrf(
        request: Request, value: BookingRuntime, submitted: str
    ) -> None:
        cookie = request.cookies.get(SESSION_COOKIE) or ""
        if not verify_csrf(cookie, submitted, value.session_secret):
            raise HTTPException(status_code=403, detail="Invalid CSRF token")

    @router.get("/booking/setup/claim")
    async def claim_setup(token: str) -> RedirectResponse:
        value = runtime()
        setup_session = value.claim_setup_token(token)
        if setup_session is None:
            raise HTTPException(status_code=410, detail="Setup link is invalid or expired")
        response = RedirectResponse("/booking/setup", status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            setup_session,
            max_age=7200,
            httponly=True,
            secure=value.secure_cookies,
            samesite="lax",
            path="/booking/setup",
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @router.get("/booking/setup", response_class=HTMLResponse)
    async def setup_page(request: Request) -> HTMLResponse:
        value = runtime()
        active_session = session(request, value)
        view = value.setup_view(active_session)
        cookie = request.cookies.get(SESSION_COOKIE) or ""
        csrf = sign_csrf(cookie, value.session_secret)
        return HTMLResponse(
            _render_setup(view, csrf, configured=set(value.provider_bundles)),
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )

    @router.post("/booking/setup/oauth/{provider_name}")
    async def start_oauth(
        request: Request,
        provider_name: str,
        csrf_token: str = Form(...),
    ) -> RedirectResponse:
        value = runtime()
        active_session = session(request, value)
        verify_request_csrf(request, value, csrf_token)
        url = value.start_oauth(active_session, provider_name)
        return RedirectResponse(url, status_code=303)

    @router.get("/booking/setup/oauth/{provider_name}/callback")
    async def oauth_callback(
        provider_name: str,
        state: str,
        code: str,
    ) -> RedirectResponse:
        value = runtime()
        try:
            value.finish_oauth(provider_name, state=state, code=code)
        except Exception:
            return RedirectResponse("/booking/setup?oauth=failed", status_code=303)
        return RedirectResponse("/booking/setup?oauth=connected", status_code=303)

    @router.post("/booking/setup/config")
    async def save_config(request: Request) -> RedirectResponse:
        value = runtime()
        active_session = session(request, value)
        form = await request.form()
        submitted_csrf = str(form.get("csrf_token") or "")
        verify_request_csrf(request, value, submitted_csrf)
        days = [
            day
            for day in (
                "monday",
                "tuesday",
                "wednesday",
                "thursday",
                "friday",
                "saturday",
                "sunday",
            )
            if form.get(day) == "on"
        ]
        starts = str(form.get("day_start") or "08:00")
        ends = str(form.get("day_end") or "17:00")
        weekly_hours = {day: [{"start": starts, "end": ends}] for day in days}
        try:
            value.save_configuration(
                active_session,
                connection_id=str(form.get("connection_id") or ""),
                resource_id=str(form.get("resource_id") or ""),
                resource_name=str(form.get("resource_name") or ""),
                timezone_name=str(form.get("timezone") or "America/Toronto"),
                mode=str(form.get("mode") or "disabled"),
                service_values={
                    "service_key": str(form.get("service_key") or ""),
                    "display_name": str(form.get("display_name") or "Routine service"),
                    "duration_minutes": int(str(form.get("duration_minutes") or "60")),
                    "lead_time_minutes": int(str(form.get("lead_time_minutes") or "60")),
                    "buffer_before_minutes": int(str(form.get("buffer_before_minutes") or "0")),
                    "buffer_after_minutes": int(str(form.get("buffer_after_minutes") or "0")),
                    "horizon_days": int(str(form.get("horizon_days") or "30")),
                    "slot_interval_minutes": int(str(form.get("slot_interval_minutes") or "30")),
                    "weekly_hours": weekly_hours,
                    "enabled": True,
                },
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse("/booking/setup?saved=true", status_code=303)

    @router.post("/booking/setup/disconnect")
    async def disconnect(
        request: Request,
        connection_id: str = Form(...),
        csrf_token: str = Form(...),
    ) -> RedirectResponse:
        value = runtime()
        active_session = session(request, value)
        verify_request_csrf(request, value, csrf_token)
        value.disconnect(active_session, connection_id)
        return RedirectResponse("/booking/setup?disconnected=true", status_code=303)

    return router


def _render_setup(view: SetupView, csrf: str, *, configured: set[str]) -> str:
    business_name = html.escape(str(view.session.client.get("business_name") or "Vigil"))
    connection_by_provider = {
        str(connection.get("provider")): connection for connection in view.connections
    }
    provider_rows = []
    for provider in ("google", "jobber"):
        connection = connection_by_provider.get(provider)
        status = str(connection.get("status")) if connection else "not connected"
        account = str(connection.get("provider_account_name") or "") if connection else ""
        available = provider in configured
        label = "Google Calendar" if provider == "google" else "Jobber"
        action = (
            f'<form method="post" action="/booking/setup/oauth/{provider}">'
            f'<input type="hidden" name="csrf_token" value="{csrf}">'
            f'<button type="submit">{"Reconnect" if connection else "Connect"}</button></form>'
            if available
            else '<span class="muted">Credentials required</span>'
        )
        disconnect = ""
        if connection and connection.get("status") not in {"disconnected", "revoked"}:
            disconnect = (
                '<form method="post" action="/booking/setup/disconnect">'
                f'<input type="hidden" name="csrf_token" value="{csrf}">'
                f'<input type="hidden" name="connection_id" value="{html.escape(str(connection["id"]))}">'
                '<button class="danger" type="submit">Disconnect</button></form>'
            )
        provider_rows.append(
            f'<div class="provider"><div><strong>{label}</strong><span>{html.escape(status)}</span>'
            f'<small>{html.escape(account)}</small></div><div class="actions">{action}{disconnect}</div></div>'
        )

    config = view.config or {}
    active_connection = str(config.get("connection_id") or "")
    connection_options = "".join(
        f'<option value="{html.escape(str(item["id"]))}" '
        f'{"selected" if str(item["id"]) == active_connection else ""}>'
        f'{html.escape(str(item.get("provider") or ""))} - '
        f'{html.escape(str(item.get("provider_account_name") or item.get("status") or ""))}</option>'
        for item in view.connections
        if item.get("status") in {"connected", "availability_unsupported"}
    )
    resource_options = "".join(
        f'<option value="{html.escape(resource.id)}" data-name="{html.escape(resource.name)}" '
        f'{"selected" if resource.id == str(config.get("resource_id") or "") else ""}>'
        f'{html.escape(resource.name)}</option>'
        for resource in view.resources
    )
    service_rows = "".join(
        f'<tr><td>{html.escape(str(item.get("display_name") or ""))}</td>'
        f'<td>{int(item.get("duration_minutes") or 0)} min</td>'
        f'<td>{"Enabled" if item.get("enabled") else "Disabled"}</td></tr>'
        for item in view.services
    ) or '<tr><td colspan="3" class="muted">No services configured</td></tr>'
    mode = str(config.get("mode") or "disabled")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{business_name} booking setup</title>
<style>
:root{{--ink:#17211b;--muted:#66706a;--line:#d8ded9;--paper:#fff;--soft:#f5f7f5;--green:#176b48;--blue:#245f9e;--red:#a32929}}
*{{box-sizing:border-box}} body{{margin:0;font:15px/1.45 system-ui,sans-serif;color:var(--ink);background:var(--paper)}}
header,section{{padding:24px max(24px,calc((100vw - 960px)/2));border-bottom:1px solid var(--line)}}
header{{background:var(--soft)}} h1{{font-size:24px;margin:0}} h2{{font-size:18px;margin:0 0 18px}} h3{{font-size:15px;margin:22px 0 12px}}
.provider{{display:flex;justify-content:space-between;align-items:center;gap:20px;padding:14px 0;border-top:1px solid var(--line)}}
.provider span,.provider small{{display:block;color:var(--muted)}} .actions{{display:flex;gap:8px}}
form.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}} label{{display:grid;gap:6px;font-weight:600}}
input,select{{width:100%;min-height:40px;border:1px solid #aeb7b0;border-radius:4px;padding:8px;background:#fff;color:var(--ink)}}
.days,.modes{{display:flex;flex-wrap:wrap;gap:8px;grid-column:1/-1}} .days label,.modes label{{display:flex;align-items:center;gap:6px;font-weight:500}}
.days input,.modes input{{width:auto;min-height:auto}} button{{border:0;border-radius:4px;padding:10px 14px;background:var(--green);color:#fff;font-weight:700;cursor:pointer}}
button.danger{{background:var(--red)}} .submit{{grid-column:1/-1;justify-self:start}} table{{width:100%;border-collapse:collapse}} th,td{{padding:10px;text-align:left;border-bottom:1px solid var(--line)}}
.muted{{color:var(--muted)}} @media(max-width:640px){{form.grid{{grid-template-columns:1fr}}.provider{{align-items:flex-start;flex-direction:column}}}}
</style></head><body>
<header><h1>{business_name}</h1><span class="muted">Booking setup</span></header>
<section><h2>Connections</h2>{''.join(provider_rows)}</section>
<section><h2>Scheduling</h2><form class="grid" method="post" action="/booking/setup/config">
<input type="hidden" name="csrf_token" value="{csrf}"><input type="hidden" id="resource_name" name="resource_name" value="{html.escape(str(config.get("resource_name") or ""))}">
<label>Provider<select name="connection_id" required>{connection_options}</select></label>
<label>Calendar or team member<select id="resource_id" name="resource_id" required>{resource_options}</select></label>
<label>Timezone<input name="timezone" value="{html.escape(str(config.get("timezone") or "America/Toronto"))}" required></label>
<div class="modes"><strong>Mode</strong>{''.join(f'<label><input type="radio" name="mode" value="{item}" {"checked" if item == mode else ""}>{item.title()}</label>' for item in ("disabled","shadow","live"))}</div>
<label>Service type<select name="service_key"><option value="drain_or_sewer">Drain or sewer</option><option value="leak_or_pipe_repair">Leak or pipe repair</option><option value="fixture_or_install">Fixture or install</option><option value="water_heater">Water heater</option></select></label>
<label>Service name<input name="display_name" value="Routine plumbing service" required></label>
<label>Duration (minutes)<input type="number" name="duration_minutes" min="15" max="1440" step="15" value="60"></label>
<label>Minimum notice (minutes)<input type="number" name="lead_time_minutes" min="0" value="60"></label>
<label>Buffer before (minutes)<input type="number" name="buffer_before_minutes" min="0" value="0"></label>
<label>Buffer after (minutes)<input type="number" name="buffer_after_minutes" min="0" value="0"></label>
<label>Booking horizon (days)<input type="number" name="horizon_days" min="1" max="365" value="30"></label>
<label>Slot interval (minutes)<input type="number" name="slot_interval_minutes" min="5" value="30"></label>
<div class="days"><strong>Working days</strong>{''.join(f'<label><input type="checkbox" name="{day}" checked>{day[:3].title()}</label>' for day in ("monday","tuesday","wednesday","thursday","friday"))}</div>
<label>Day starts<input type="time" name="day_start" value="08:00"></label><label>Day ends<input type="time" name="day_end" value="17:00"></label>
<button class="submit" type="submit">Save service</button></form>
<h3>Configured services</h3><table><thead><tr><th>Service</th><th>Duration</th><th>Status</th></tr></thead><tbody>{service_rows}</tbody></table></section>
<script>const r=document.getElementById('resource_id'),n=document.getElementById('resource_name');if(r)r.addEventListener('change',()=>n.value=r.options[r.selectedIndex]?.dataset.name||'');</script>
</body></html>"""
