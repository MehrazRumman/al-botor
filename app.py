import os
import re
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from slack_sdk.errors import SlackApiError
from flask import Flask, request

SAVE_FLAG_REGEX = re.compile(r"--save(d)?\b", re.IGNORECASE)
CANVAS_ID_REGEX = re.compile(r"^F[A-Z0-9]{8,}$")
WELCOME_TEXT = "Bhai apnader jonne kaz korte chole ashlam"

bolt_app = App(
    token=os.environ["SLACK_BOT_TOKEN"],
    signing_secret=os.environ["SLACK_SIGNING_SECRET"],
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


def _get_bot_user_id(client, logger):
    try:
        auth = client.auth_test()
        return auth.get("user_id", "")
    except SlackApiError as e:
        logger.warning("Could not resolve bot user ID: %s", e.response.get("error"))
    return ""


@bolt_app.event("member_joined_channel")
def handle_member_joined_channel_events(body, client, logger):
    event = body.get("event", {})
    channel_id = event.get("channel")
    joined_user_id = event.get("user")
    bot_user_id = _get_bot_user_id(client, logger)

    if not channel_id or not joined_user_id or not bot_user_id:
        return

    if joined_user_id != bot_user_id:
        return

    try:
        client.chat_postMessage(
            channel=channel_id,
            text=WELCOME_TEXT,
        )
    except SlackApiError as e:
        logger.warning("Failed to post welcome message: %s", e.response.get("error"))


@bolt_app.event("message")
def handle_message_events(body, client, logger):
    event = body["event"]
    text = event.get("text", "")
    channel_id = event.get("channel")
    user_id = event.get("user")

    # ignore bot messages to avoid loops
    if event.get("subtype") == "bot_message":
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

@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    return handler.handle(request)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    flask_app.run(host="0.0.0.0", port=port)
