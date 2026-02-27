import os
import re
import time
import logging
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from slack_sdk.errors import SlackApiError
from flask import Flask, request

SAVE_FLAG_REGEX = re.compile(r"--save(d)?\b", re.IGNORECASE)
AREA51_TRIGGER_REGEX = re.compile(r"(^|\s)@?area51(\s|$)", re.IGNORECASE)
SIGN_OUT_REGEX = re.compile(r"\bsign(?:ing)?\s*-?\s*out\b", re.IGNORECASE)
CANVAS_ID_REGEX = re.compile(r"^F[A-Z0-9]{8,}$")
SLACK_USER_ID_REGEX = re.compile(r"^[UW][A-Z0-9]+$")
WELCOME_TEXT = "Bhai apnader jonne kaz korte chole ashlam"
AREA51_MEMBER_IDS = [
    "Rumman",
    "Joy Adhikary",
    "Ishmoth Ura Nuri",
    "Intishar Ishmam",
]
AREA51_MESSAGE_TEXT = "\n Anik bhai sobaire meeting e dakse, Sobai ashen !! :rocket: \n Eta apnader meeting link:https://meet.google.com/eea-ubxh-qfi . \n Join koren, Ami ektu chill kori :sunglasses: "
AREA51_USER_ID_CACHE = {}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

bolt_app = App(
    token=os.environ["SLACK_BOT_TOKEN"],
    signing_secret=os.environ["SLACK_SIGNING_SECRET"],
    # Required so the bot can process its own channel_join system message
    # and send the welcome text on join/rejoin.
    ignoring_self_events_enabled=False,
)


def _extract_canvas_id(payload):
    canvas_id = payload.get("canvas_id")
    if canvas_id:
        return canvas_id

    direct_canvas_id = payload.get("id") or payload.get("file_id")
    if direct_canvas_id:
        return direct_canvas_id

    canvas = payload.get("canvas", {})
    return canvas.get("id") or canvas.get("file_id")


def _get_or_create_channel_canvas_id(client, channel_id, logger):
    try:
        created = client.conversations_canvases_create(
            channel_id=channel_id,
            document_content={"type": "markdown", "markdown": "# Saved Items\n"},
        )
        created_canvas_id = _extract_canvas_id(created)
        if created_canvas_id and CANVAS_ID_REGEX.match(created_canvas_id):
            return created_canvas_id
    except SlackApiError as e:
        if e.response.get("error") not in {
            "already_exists",
            "already_in_channel",
            "free_team_canvas_tab_already_exists",
        }:
            raise

        try:
            info = client.conversations_info(channel=channel_id)
            existing_canvas_id = _extract_canvas_id(info.get("channel", {}).get("properties", {}).get("canvas", {}))
            if existing_canvas_id and CANVAS_ID_REGEX.match(existing_canvas_id):
                return existing_canvas_id
        except SlackApiError:
            pass

        logger.error(
            "Canvas exists, but no canvas_id was resolved. "
            "Add read scopes channels:read,groups:read,mpim:read,im:read "
            "and reinstall the app so the existing canvas ID can be discovered."
        )
    return None


def _get_user_display_name(client, user_id, logger):
    if not user_id:
        return "unknown user"

    try:
        info = client.users_info(user=user_id)
        user = info.get("user", {})
        profile = user.get("profile", {})
        display_name = profile.get("display_name") or profile.get("real_name") or user.get("name") or user_id
        return display_name
    except SlackApiError as e:
        logger.warning("Could not resolve user display name for %s: %s", user_id, e.response.get("error"))
        return f"<@{user_id}>"


def _get_bot_member_ids(client, logger):
    try:
        auth = client.auth_test()
        return auth.get("user_id", ""), auth.get("bot_id", "")
    except SlackApiError as e:
        logger.warning("Could not resolve bot identity: %s", e.response.get("error"))
    return "", ""


def _build_mentions(user_ids):
    return " ".join(f"<@{user_id}>" for user_id in user_ids if user_id)


def _normalize_user_key(value):
    return (value or "").strip().lower()


def _build_workspace_user_index(client, logger):
    user_index = {}
    cursor = None
    try:
        while True:
            response = client.users_list(limit=200, cursor=cursor)
            members = response.get("members", [])
            for member in members:
                if member.get("deleted") or member.get("is_bot") or member.get("is_app_user"):
                    continue

                user_id = member.get("id", "")
                profile = member.get("profile", {})
                keys = [
                    user_id,
                    member.get("name", ""),
                    profile.get("display_name", ""),
                    profile.get("display_name_normalized", ""),
                    profile.get("real_name", ""),
                    profile.get("real_name_normalized", ""),
                ]

                for key in keys:
                    normalized = _normalize_user_key(key)
                    if normalized and normalized not in user_index:
                        user_index[normalized] = user_id

            cursor = response.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
    except SlackApiError as e:
        logger.warning("Failed to fetch users list for mentions: %s", e.response.get("error"))
        return {}

    return user_index


def _resolve_area51_member_ids(client, logger):
    unresolved_keys = []
    resolved_user_ids = []

    for member_ref in AREA51_MEMBER_IDS:
        normalized_ref = _normalize_user_key(member_ref)
        if not normalized_ref:
            continue

        if SLACK_USER_ID_REGEX.match(member_ref):
            resolved_user_ids.append(member_ref)
            continue

        cached_user_id = AREA51_USER_ID_CACHE.get(normalized_ref)
        if cached_user_id:
            resolved_user_ids.append(cached_user_id)
        else:
            unresolved_keys.append(normalized_ref)

    if unresolved_keys:
        user_index = _build_workspace_user_index(client, logger)
        for unresolved_key in unresolved_keys:
            resolved_user_id = user_index.get(unresolved_key)
            if resolved_user_id:
                AREA51_USER_ID_CACHE[unresolved_key] = resolved_user_id
                resolved_user_ids.append(resolved_user_id)
            else:
                logger.warning("Could not resolve AREA51 member: %s", unresolved_key)

    # preserve order while removing duplicates
    return list(dict.fromkeys(resolved_user_ids))


def _post_welcome_with_retry(client, channel_id, logger):
    retryable_errors = {
        "not_in_channel",
        "channel_not_found",
        "internal_error",
        "request_timeout",
        "ratelimited",
    }
    max_attempts = 4

    for attempt in range(1, max_attempts + 1):
        try:
            client.chat_postMessage(
                channel=channel_id,
                text=WELCOME_TEXT,
            )
            # print(f"[welcome] posted in channel={channel_id} attempt={attempt}", flush=True)
            return True
        except SlackApiError as e:
            error = e.response.get("error", "unknown_error")
            logger.warning(
                "Welcome post failed: channel=%s attempt=%s error=%s",
                channel_id,
                attempt,
                error,
            )
            # print(
            #     f"[welcome] failed channel={channel_id} attempt={attempt} error={error}",
            #     flush=True,
            # )
            if error not in retryable_errors or attempt == max_attempts:
                return False

            retry_after = None
            headers = getattr(e.response, "headers", {}) or {}
            if isinstance(headers, dict):
                retry_after = headers.get("Retry-After")
            sleep_seconds = float(retry_after) if retry_after else float(attempt)
            time.sleep(sleep_seconds)

    return False


def _welcome_if_bot_join_event(event, client, logger):
    channel_id = event.get("channel")
    joined_member_id = event.get("user") or event.get("bot_id")
    bot_user_id, bot_id = _get_bot_member_ids(client, logger)
    # logger.info(
    #     "Join event check: channel=%s joined_member_id=%s bot_user_id=%s bot_id=%s type=%s subtype=%s",
    #     channel_id,
    #     joined_member_id,
    #     bot_user_id,
    #     bot_id,
    #     event.get("type"),
    #     event.get("subtype"),
    # )

    if not channel_id or not joined_member_id:
        return False

    bot_member_ids = {member_id for member_id in (bot_user_id, bot_id) if member_id}
    if not bot_member_ids or joined_member_id not in bot_member_ids:
        return False

    return _post_welcome_with_retry(client, channel_id, logger)


def _is_join_system_message(event):
    subtype = event.get("subtype", "")
    if subtype not in {"channel_join", "group_join"}:
        return False

    text = (event.get("text") or "").lower()
    return "has joined" in text or "has rejoined" in text


@bolt_app.event("member_joined_channel")
def handle_member_joined_channel_events(body, client, logger):
    event = body.get("event", {})
    _welcome_if_bot_join_event(event, client, logger)


@bolt_app.event("message")
def handle_message_events(body, client, logger):
    event = body["event"]
    text = event.get("text", "")
    channel_id = event.get("channel")
    user_id = event.get("user")
    subtype = event.get("subtype", "")

    if _is_join_system_message(event):
        if _welcome_if_bot_join_event(event, client, logger):
            return

    # ignore bot-generated messages to avoid loops
    if subtype == "bot_message":
        return

    if channel_id and SIGN_OUT_REGEX.search(text):
        mention = f"<@{user_id}>" if user_id else ""
        honorific = "apu" if user_id == "D07AVS5KAPK" else "bhai"
        greeting_target = f"{mention} {honorific}".strip()
        try:
            client.chat_postMessage(
                channel=channel_id,
                text=f"{greeting_target} ajke onk kosto korsen, Eibar rest koren :wave: :zzz:",
            )
        except SlackApiError as e:
            logger.warning("Failed to send sign-out response: %s", e.response.get("error"))
        return

    if channel_id and AREA51_TRIGGER_REGEX.search(text):
        try:
            member_ids = _resolve_area51_member_ids(client, logger)
            if not member_ids:
                logger.warning("No valid AREA51 members found for mention.")
                return
            mentions = _build_mentions(member_ids)
            message = f"{mentions} {AREA51_MESSAGE_TEXT}".strip()
            client.chat_postMessage(channel=channel_id, text=message)
        except SlackApiError as e:
            logger.warning("Failed to send @area51 meeting alert: %s", e.response.get("error"))
        return

    if not channel_id or not SAVE_FLAG_REGEX.search(text):
        return

    saved_text = SAVE_FLAG_REGEX.sub("", text).strip()
    if not saved_text:
        return

    saved_by = _get_user_display_name(client, user_id, logger)
    entry = f"\n- Saved by {saved_by}: {saved_text}"

    try:
        canvas_id = _get_or_create_channel_canvas_id(client, channel_id, logger)
        if not canvas_id or not CANVAS_ID_REGEX.match(canvas_id):
            logger.error("Unable to resolve a valid canvas_id for channel=%s", channel_id)
            return

        # Insert at the end of the channel canvas document.
        client.canvases_edit(
            canvas_id=canvas_id,
            changes=[
                {
                    "operation": "insert_at_end",
                    "document_content": {
                        "type": "markdown",
                        "markdown": entry,
                    },
                }
            ],
        )
    except SlackApiError as e:
        logger.error("Failed to update canvas: %s", e)
        return

    # React ✅ on the message and send confirmation.
    try:
        client.reactions_add(
            channel=channel_id,
            name="white_check_mark",
            timestamp=event["ts"],
        )
        mention = f"<@{user_id}> " if user_id else ""
        client.chat_postMessage(
            channel=channel_id,
            text=f"{mention} Bhai apnar message canvas-e save hoye gese! :white_check_mark:",
        )
    except SlackApiError as e:
        logger.warning("Saved to canvas but failed to react/confirm: %s", e.response.get("error"))

# --- Flask server for Slack Events API ---
flask_app = Flask(__name__)
handler = SlackRequestHandler(bolt_app)

@flask_app.get("/")
def health():
    return "ok", 200

@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    # payload = request.get_json(silent=True) or {}
    # event = payload.get("event", {})
    # print(
    #     "[slack-event] type=%s event_type=%s subtype=%s channel=%s user=%s bot_id=%s"
    #     % (
    #         payload.get("type"),
    #         event.get("type"),
    #         event.get("subtype"),
    #         event.get("channel"),
    #         event.get("user"),
    #         event.get("bot_id"),
    #     ),
    #     flush=True,
    # )
    return handler.handle(request)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    flask_app.run(host="0.0.0.0", port=port)
