# Running as a systemd service

Create `/etc/systemd/system/panel.service`:

```ini
[Unit]
Description=Radio Control Panel
After=network.target

[Service]
User=YOUR_USER
WorkingDirectory=/home/YOUR_USER/panel
EnvironmentFile=/home/YOUR_USER/panel/.env
ExecStart=/home/YOUR_USER/panel-env/bin/uvicorn main:app --host 0.0.0.0 --port 8082
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now panel
```

## Conductor OSS service

Create `/etc/systemd/system/conductor.service`:

```ini
[Unit]
Description=Conductor OSS Workflow Engine
After=network.target mysql.service

[Service]
User=YOUR_USER
ExecStart=/usr/bin/java -Xms256m -Xmx768m -jar /opt/conductor-mc/conductor-server-boot.jar
WorkingDirectory=/opt/conductor-mc
Restart=on-failure

[Install]
WantedBy=multi-user.target
```
