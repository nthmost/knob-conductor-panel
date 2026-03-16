# Reverse proxy with Apache

To expose the panel at a public domain (e.g. `knob.nthmost.com`), add a VirtualHost on your edge server:

```apache
<VirtualHost *:443>
    ServerName knob.yourstation.com

    # SSE requires flushed packets
    ProxyPreserveHost On
    ProxyPass        /events  http://YOUR_LAN_IP:8082/events  flushpackets=on
    ProxyPassReverse /events  http://YOUR_LAN_IP:8082/events
    ProxyPass        /        http://YOUR_LAN_IP:8082/
    ProxyPassReverse /        http://YOUR_LAN_IP:8082/

    RequestHeader set X-Forwarded-Proto "https"

    # SSL — obtain with: certbot --apache -d knob.yourstation.com
    SSLEngine on
    SSLCertificateFile    /etc/letsencrypt/live/knob.yourstation.com/fullchain.pem
    SSLCertificateKeyFile /etc/letsencrypt/live/knob.yourstation.com/privkey.pem
</VirtualHost>
```

Enable required modules:

```bash
sudo a2enmod proxy proxy_http headers
sudo systemctl reload apache2
```

> **Note on the audio stream:** The panel proxies the Icecast stream through `/stream`
> so the browser never makes a cross-origin or mixed-content request.
> No special Apache config is needed for audio playback.
