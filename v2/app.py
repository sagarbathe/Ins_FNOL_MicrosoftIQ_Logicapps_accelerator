import hmac
import logging
import re
import secrets
import time
import uuid
from contextlib import contextmanager
from urllib.parse import urlparse

from azure.core.exceptions import HttpResponseError, ResourceExistsError
from azure.identity import ManagedIdentityCredential
from flask import Flask, abort, g, jsonify, redirect, render_template, request
from openai import APIError
from werkzeug.exceptions import HTTPException

from v2 import auth, runtime_digest
from v2.email_processing import InitialTriage, acknowledge_notification, process_email
from v2 import escalation
from v2.recovery import RecoveryClient, recover
from v2.runtime import AgentRuntime, GroundingError
from v2.settings import Settings
from v2.state import BlobState, StateBusy, conversation_key, run_prefix

COOKIE = "__Host-fnol_v2"
ID_PATTERN = re.compile(r"[A-Z0-9][A-Z0-9-]{0,63}")
SESSION_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")
LOGGER = logging.getLogger("fnol.v2")


def create_app(settings=None, store=None, runtime=None, triage=None):
    settings = settings or Settings.from_env()
    store = store or BlobState(
        settings.storage_url, settings.encryption_key, ManagedIdentityCredential(),
    )
    runtime = runtime or AgentRuntime(settings)
    triage = triage or InitialTriage(settings)
    app = Flask(__name__)
    app.config.update(
        MAX_CONTENT_LENGTH=16384, TRUSTED_HOSTS=[urlparse(settings.public_url).hostname],
        BUILD_SHA256=runtime_digest(),
    )

    def set_cookie(response, session_id):
        response.set_cookie(
            COOKIE, session_id, secure=True, httponly=True, samesite="Lax",
            path="/", max_age=3600,
        )
        return response

    def session_id():
        value = request.cookies.get(COOKIE, "")
        if not SESSION_PATTERN.fullmatch(value):
            abort(401, "Sign in first.")
        return value

    def check_session(session, *, signed_in=True):
        if not session or session.get("expires_at", 0) <= time.time():
            abort(401, "Session expired. Sign in again.")
        if signed_in and (
            session.get("tenant_id") != settings.tenant_id or not session.get("user_id")
        ):
            abort(401, "Sign in first.")

    @contextmanager
    def current_session(*, edit=False):
        if getattr(g, "teams_session", None) is not None:
            session = g.teams_session
            check_session(session)
            yield session
        elif edit:
            with store.edit("sessions/" + session_id()) as session:
                check_session(session)
                yield session
        else:
            session = store.read("sessions/" + session_id())
            check_session(session)
            yield session

    def authorized_case(session, case_id):
        if not isinstance(case_id, str) or not ID_PATTERN.fullmatch(case_id):
            abort(400, "Invalid case identifier.")
        case = store.read("cases/" + case_id)
        if (not case or case.get("tenant_id") != session["tenant_id"]
                or session["user_id"] not in case.get("allowed_users", [])):
            abort(403, "You are not assigned to this case.")
        return case

    def conversation_identity(session, case_id):
        owner = [session["tenant_id"], session["user_id"], case_id,
                 settings.agent_name, settings.agent_version]
        return conversation_key(*owner), owner

    @app.before_request
    def protect_writes():
        authorization = request.headers.get("Authorization")
        if request.path == "/api/intake/email" or re.fullmatch(
            r"/api/intake/email/EMAIL-[A-F0-9]{32}/notified", request.path,
        ):
            if (request.method != "POST" or not authorization
                    or not authorization.startswith("Bearer ")
                    or not 1 <= len(authorization[7:]) <= 16384):
                abort(401, "The isolated email workflow identity is required.")
            auth.intake_identity(settings, authorization[7:])
            request.max_content_length = 262144
            return
        if authorization is not None:
            if not request.path.startswith("/api/"):
                abort(400, "Bearer authentication is supported only for the private API.")
            if (not authorization.startswith("Bearer ")
                    or not 1 <= len(authorization[7:]) <= 16384):
                abort(401, "A Teams bearer token is required.")
            g.teams_session = auth.teams_session(settings, authorization[7:])
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if request.headers.get("Origin") != settings.public_url:
                abort(403, "The request origin is not allowed.")
            if getattr(g, "teams_session", None) is not None:
                return
            session = store.read("sessions/" + session_id())
            check_session(session)
            supplied = request.headers.get("X-CSRF-Token", "")
            if not supplied or not hmac.compare_digest(supplied, session["csrf"]):
                abort(403, "Invalid CSRF token.")

    @app.after_request
    def security_headers(response):
        ancestors = (
            "https://teams.microsoft.com https://*.teams.microsoft.com https://*.cloud.microsoft"
            if request.path == "/teams" and settings.teams_enabled else "'none'"
        )
        scripts = (
            " https://res.cdn.office.net/teams-js/2.34.0/js/MicrosoftTeams.min.js"
            if request.path == "/teams" and settings.teams_enabled else ""
        )
        response.headers.update({
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                f"default-src 'self'; script-src 'self'{scripts}; frame-ancestors {ancestors}; "
                "base-uri 'none'; form-action 'self'; object-src 'none'"
            ),
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Strict-Transport-Security": "max-age=31536000",
        })
        return response

    @app.get("/healthz")
    def health():
        return jsonify(status="running", build_sha256=app.config["BUILD_SHA256"])

    @app.get("/readyz")
    def ready():
        return jsonify(ready=settings.integrations_ready), 200 if settings.integrations_ready else 503

    @app.get("/")
    def index():
        return render_template("index.html", teams=False)

    @app.get("/teams")
    def teams():
        if not settings.teams_enabled:
            abort(404, "Teams integration is not configured.")
        return render_template("index.html", teams=True)

    @app.get("/privacy")
    def privacy():
        return render_template("policy.html", privacy=True)

    @app.get("/terms")
    def terms():
        return render_template("policy.html", privacy=False)

    @app.get("/login")
    def login():
        new_id, session = store.new_session()
        application, cache = auth.client(settings, session)
        flow = application.initiate_auth_code_flow(
            [auth.FOUNDRY_SCOPE], redirect_uri=settings.public_url + "/auth/callback",
            prompt="select_account",
        )
        with store.edit("sessions/" + new_id) as saved:
            saved["flow"] = flow
            saved["token_cache"] = cache.serialize()
        return set_cookie(redirect(flow["auth_uri"]), new_id)

    @app.get("/auth/callback")
    def callback():
        with store.edit("sessions/" + session_id()) as session:
            check_session(session, signed_in=False)
            flow = session.pop("flow", None)
            if not flow:
                abort(400, "The sign-in request has expired or was already consumed.")
            application, cache = auth.client(settings, session)
            try:
                result = application.acquire_token_by_auth_code_flow(flow, request.args)
            except ValueError:
                abort(400, "Sign-in state validation failed.")
            if "error" in result:
                LOGGER.warning("Entra sign-in refused: %s", result.get("error"))
                abort(401, "Entra sign-in or consent failed. Check your tenant's consent policy.")
            claims = result.get("id_token_claims", {})
            if claims.get("tid") != settings.tenant_id or not claims.get("oid"):
                abort(403, "An account in the configured tenant is required.")
            user_id = str(uuid.UUID(claims["oid"]))
            new_id, _ = store.new_session({
                "tenant_id": settings.tenant_id, "user_id": user_id,
                "token_cache": cache.serialize(),
            })
            session.clear()
            session["expires_at"] = 0
        return set_cookie(redirect("/"), new_id)

    @app.get("/api/me")
    def me():
        with current_session() as session:
            return jsonify(
                tenant_id=session["tenant_id"], user_id=session["user_id"],
                csrf=session.get("csrf", ""), integrations_ready=settings.integrations_ready,
                agent_reference={"name": settings.agent_name, "version": settings.agent_version},
                email_operator=session["user_id"] == settings.email_operator_id,
            )

    @app.get("/api/cases")
    def cases():
        with current_session() as session:
            return jsonify(cases=store.cases(session["tenant_id"], session["user_id"]))

    @app.get("/api/cases/<case_id>/intake")
    def case_intake(case_id):
        with current_session() as session:
            case = authorized_case(session, case_id)
            source = case.get("intake")
            return jsonify(intake={
                key: source[key] for key in
                ("sender", "subject", "received_at", "visible_text", "notice", "triage")
                if key in source
            } if source else None, processing={
                key: case["processing"][key] for key in
                ("phase", "created_at", "screened_at", "notified_at")
                if key in case.get("processing", {})
            })

    @app.post("/api/intake/email")
    def receive_email():
        try:
            return jsonify(process_email(settings, store, triage, request.get_json()))
        except ValueError as exc:
            abort(400, str(exc))

    @app.post("/api/intake/email/<case_id>/notified")
    def email_notified(case_id):
        if not re.fullmatch(r"EMAIL-[A-F0-9]{32}", case_id):
            abort(400, "Invalid email case identifier.")
        try:
            acknowledge_notification(store, case_id, request.get_json())
        except ValueError as exc:
            abort(400, str(exc))
        return jsonify(status="recorded")

    @app.get("/api/cases/<case_id>/conversation")
    def conversation(case_id):
        with current_session() as session:
            authorized_case(session, case_id)
            key, owner = conversation_identity(session, case_id)
            history = store.read(key)
            if history and history.get("owner") != owner:
                abort(403, "Conversation ownership does not match.")
            return jsonify(
                messages=history.get("messages", []) if history else [],
                consent_links=history.get("consent_links", []) if history else [],
                pending_consent=bool(history and history.get("pending_consent")),
            )

    @app.get("/api/cases/<case_id>/runs")
    def case_runs(case_id):
        with current_session() as session:
            authorized_case(session, case_id)
            records, more = store.runs([session["tenant_id"], session["user_id"], case_id])
            return jsonify(
                runs=[{key: value for key, value in item.items() if key != "owner"} for item in records],
                limited=more, limit=1000,
            )

    @app.post("/api/cases/<case_id>/escalation/<action>")
    def escalation_action(case_id, action):
        if action not in ("preview", "confirm"):
            abort(404, "Unknown escalation action.")
        with current_session(edit=True) as session:
            authorized_case(session, case_id)
            owner = [session["tenant_id"], session["user_id"], case_id]
            try:
                if action == "preview":
                    return jsonify(escalation.preview(store, owner, request.get_json()))
                return jsonify(escalation.confirm(settings, store, session, owner, request.get_json()))
            except ValueError as exc:
                abort(400, str(exc))

    @app.post("/api/cases/<case_id>/recovery")
    def email_recovery(case_id):
        with current_session() as session:
            authorized_case(session, case_id)
            if not settings.email_operator_id or session["user_id"] != settings.email_operator_id:
                abort(403, "Only the configured intake operator may reconcile or request a replay.")
            try:
                return jsonify(recover(
                    store, case_id, session["user_id"], request.get_json(),
                    RecoveryClient(settings.email_workflow_id),
                ))
            except ValueError as exc:
                abort(400, str(exc))

    @app.post("/logout")
    def logout():
        with store.edit("sessions/" + session_id()) as session:
            check_session(session)
            session.clear()
            session["expires_at"] = 0
        response = jsonify(signed_out=True)
        response.delete_cookie(COOKIE, secure=True, httponly=True, samesite="Lax", path="/")
        return response

    @app.post("/api/chat")
    def chat():
        data = request.get_json()
        if not isinstance(data, dict):
            abort(400, "A JSON request object is required.")
        case_id = data.get("case_id", "")
        if not isinstance(case_id, str) or not ID_PATTERN.fullmatch(case_id):
            abort(400, "Invalid case identifier.")
        if set(data) - {"case_id", "message", "resume"}:
            abort(400, "Unexpected request fields; response ownership is managed by the server.")
        resume = data.get("resume", False)
        if not isinstance(resume, bool):
            abort(400, "resume must be a boolean.")
        message = data.get("message", "")
        if not resume and (not isinstance(message, str) or not 1 <= len(message.strip()) <= 6000):
            abort(400, "Enter a question of 1-6000 characters.")
        with current_session(edit=True) as session:
            case = authorized_case(session, case_id)
            key, owner = conversation_identity(session, case_id)
            try:
                store.create(key, {"owner": owner, "messages": [], "response_id": None})
            except ResourceExistsError:
                pass
            with store.edit(key) as history:
                if not history or history.get("owner") != owner:
                    abort(403, "Conversation ownership does not match.")
                if resume:
                    if not history.get("pending_consent"):
                        abort(409, "There is no pending authorization request.")
                    message = history["pending_question"]
                run_owner = [session["tenant_id"], session["user_id"], case_id]
                run_id = str(uuid.uuid4())
                run_key = run_prefix(run_owner) + run_id
                store.create(run_key, {
                    "id": run_id, "owner": run_owner, "started_at": int(time.time()),
                    "agent": {"name": settings.agent_name, "version": settings.agent_version},
                    "outcome": "running", "resume": resume, "tools": [],
                })
                try:
                    result = runtime.respond(session, case, message, history.get("response_id"))
                except Exception as exc:
                    with store.edit(run_key) as record:
                        record.update(outcome="failed", finished_at=int(time.time()),
                                      error_type=type(exc).__name__)
                    raise
                history["response_id"] = result["response_id"]
                history["pending_consent"] = bool(result["consent_links"])
                history["consent_links"] = result["consent_links"]
                history["pending_question"] = message
                history["messages"] = (history["messages"] + [
                    {"role": "user", "text": message}, {"role": "assistant", "text": result["text"]},
                ])[-40:]
            with store.edit(run_key) as record:
                record.update(
                    outcome="consent_required" if result["consent_links"] else "completed",
                    finished_at=int(time.time()),
                    tools=[{
                        key: value for key, value in item.items()
                        if key in ("type", "server_label", "name", "status")
                        and isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", value)
                    } for item in result.get("tool_statuses", [])],
                )
        return jsonify(
            text=result["text"], consent_links=result["consent_links"],
            tool_statuses=result.get("tool_statuses", []),
        )

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(error=error.name, message=error.description), error.code

    @app.errorhandler(auth.InteractionRequired)
    def interaction_error(error):
        return jsonify(error="interaction_required", message=str(error)), 401

    @app.errorhandler(StateBusy)
    def busy_error(error):
        LOGGER.warning("Concurrent request or lost state lease")
        return jsonify(error="state_busy", message=str(error)), 409

    @app.errorhandler(GroundingError)
    def grounding_error(error):
        LOGGER.error("Agent or intake processing failed")
        return jsonify(error="grounding_failed", message=str(error)), 502

    @app.errorhandler(escalation.MailSendError)
    def mail_send_error(error):
        LOGGER.error("User-confirmed delegated escalation was not accepted or has an uncertain outcome")
        return jsonify(error="escalation_not_accepted", message=str(error)), 502

    @app.errorhandler(Exception)
    def service_error(error):
        correlation = secrets.token_hex(8)
        LOGGER.error("Failed request reference=%s", correlation)
        if isinstance(error, (HttpResponseError, APIError)):
            status = getattr(error, "status_code", None)
            LOGGER.error("Upstream service failure: type=%s status=%s", type(error).__name__, status)
            headers = getattr(getattr(error, "response", None), "headers", None) or {}
            for name in ("apim-request-id", "x-ms-request-id", "x-request-id"):
                value = headers.get(name, "")
                if isinstance(value, str) and re.fullmatch(
                    r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", value,
                ):
                    LOGGER.error(
                        "Upstream request reference=%s header=%s id=%s", correlation, name, value,
                    )
            detail = getattr(error, "body", None)
            if isinstance(detail, dict):
                detail = detail.get("error", detail)
            code = detail.get("code", "") if isinstance(detail, dict) else ""
            code = code if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", code) else ""
            LOGGER.error("Upstream error code=%s", code)
            if code == "tool_user_error":
                return jsonify(
                    error="grounding_unavailable",
                    message=(
                        "A required data source could not be accessed with your signed-in account. "
                        "Check your source permissions and required authorization. "
                        "No data was returned and no fallback identity was used."
                    ),
                    reference=correlation,
                ), 502
        else:
            LOGGER.error("Application failure: type=%s", type(error).__name__)
        return jsonify(error="service_failure", message="Request failed.", reference=correlation), 503

    return app
