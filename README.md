# Automatt

This repo tracks tools developed to assist in the authorship of Matthew Gritzmacher's [Daily Crossword Links](https://crosswordlinks.substack.com) newsletter.

### Discord bot

The Automatt Discord bot is deployed as a systemd service, where it listens for requests to rerun the daily script. That requires creating the following file in `/etc/systemd/system/automatt-bot.service`

```
[Unit]
Description=Automatt Discord bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/this/project/discord-bot
ExecStart=/path/to/python bot.py
Environment=CONFIG_PATH=/path/to/this/project/email.yaml
Restart=unless-stopped
User=<yourUser>

[Install]
WantedBy=multi-user.target
```
