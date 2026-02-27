# Al Botor

## What it does
This is a Slack bot built for workspace automation. It listens to Slack events and:
- Sends a custom sign-out reply.
- Sends an `@area51` meeting alert by mentioning configured members.
- Saves flagged messages (`--save` / `--saved`) into the channel canvas.
- Posts a welcome message when the bot joins/rejoins a channel.

The bot is deployed on Render, and deployments are automated through GitHub Actions.

## Technologies used
- Python 3
- Flask
- Slack Bolt for Python (`slack_bolt`)
- Slack SDK (`slack_sdk`)
- Gunicorn
- GitHub Actions (CI/CD)
- Render (hosting/deployment)
