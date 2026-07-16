import os
import time
import argparse
import logging
import urllib.request
import urllib.error
import socket
import smtplib
import ssl
import sys
from email.message import EmailMessage

URL = "https://myquangcayngodong.com"

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)


def send_email(smtp_server: str,
               smtp_port: int,
               smtp_user: str,
               smtp_pass: str,
               use_tls: bool,
               mail_from: str,
               mail_to: str,
               subject: str,
               body: str) -> None:
    msg = EmailMessage()
    msg["From"] = mail_from
    msg["To"] = mail_to
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        if smtp_port == 465:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(smtp_server, smtp_port, context=context) as server:
                if smtp_user and smtp_pass:
                    server.login(smtp_user, smtp_pass)
                server.send_message(msg)
        else:
            with smtplib.SMTP(smtp_server, smtp_port, timeout=10) as server:
                if use_tls:
                    server.starttls(context=ssl.create_default_context())
                if smtp_user and smtp_pass:
                    server.login(smtp_user, smtp_pass)
                server.send_message(msg)
        logging.info("Alert email sent to %s", mail_to)
    except Exception as e:
        logging.exception("Failed to send email to %s: %s", mail_to, e)


def uptime_bot(url: str,
               interval: int = 60,
               timeout: int = 10,
               smtp_cfg: dict | None = None) -> None:
    """
    Periodically check URL availability and print clear, timestamped messages.
    Sends email alerts using smtp_cfg when the site goes down and recovery emails when it returns.

    - url: target URL to check
    - interval: seconds between checks
    - timeout: request timeout in seconds
    - smtp_cfg: dict with keys: smtp_server, smtp_port, smtp_user, smtp_pass, use_tls, mail_from, mail_to
    """
    logging.info("Starting monitor: %s (checking every %ss, timeout %ss)", url, interval, timeout)

    last_up = True  # assume up at start to avoid immediate alert
    try:
        while True:
            start = time.time()
            is_up = False
            detail = ""
            try:
                resp = urllib.request.urlopen(url, timeout=timeout)
                elapsed = time.time() - start
                status = getattr(resp, "getcode", lambda: None)()
                if status is None:
                    logging.info("OK: %s (response received in %.2fs)", url, elapsed)
                    is_up = True
                elif 200 <= status < 400:
                    logging.info("UP: %s (HTTP %s, %.2fs)", url, status, elapsed)
                    is_up = True
                elif status == 404:
                    logging.error("URL NOT FOUND: %s (HTTP 404, %.2fs)", url, elapsed)
                    detail = f"HTTP 404 after {elapsed:.2f}s"
                else:
                    logging.warning("UNUSUAL STATUS: %s (HTTP %s, %.2fs)", url, status, elapsed)
                    detail = f"HTTP {status} after {elapsed:.2f}s"

            except urllib.error.HTTPError as e:
                elapsed = time.time() - start
                if e.code == 404:
                    logging.error("URL NOT FOUND: %s (HTTP 404) after %.2fs", url, elapsed)
                    detail = f"HTTP 404 after {elapsed:.2f}s"
                else:
                    logging.error("HTTPError: %s (code=%s, reason=%s) after %.2fs", url, e.code, getattr(e, "reason", e), elapsed)
                    detail = f"HTTPError {e.code}: {getattr(e, 'reason', e)}"
            except ValueError as e:
                # Raised for malformed URL
                logging.error("Invalid URL provided: %s — %s", url, e)
                detail = f"Invalid URL: {e}"
            except urllib.error.URLError as e:
                elapsed = time.time() - start
                reason = getattr(e, "reason", e)
                reason_str = str(reason)
                if isinstance(reason, socket.gaierror) or any(
                    s in reason_str.lower() for s in ("name or service not known", "nodename nor servname", "getaddrinfo failed", "no address associated")
                ):
                    logging.error("URL NOT FOUND / DNS ERROR: %s (reason=%s) after %.2fs", url, reason_str, elapsed)
                    detail = f"DNS/Name resolution error: {reason_str}"
                else:
                    logging.error("URLError: %s (reason=%s) after %.2fs", url, reason_str, elapsed)
                    detail = f"URLError: {reason_str}"
            except Exception as e:
                logging.exception("Unexpected error while checking %s: %s", url, e)
                detail = f"Unexpected error: {e}"

            # Determine state change and notify if configured
            if smtp_cfg and smtp_cfg.get("mail_to"):
                if not is_up and last_up:
                    subj = f"ALERT: {url} is DOWN"
                    body = f"Monitor detected that {url} is down.\n\nDetails: {detail}\nTime: {time.strftime('%Y-%m-%d %H:%M:%S')}"
                    send_email(
                        smtp_cfg["smtp_server"],
                        smtp_cfg["smtp_port"],
                        smtp_cfg.get("smtp_user"),
                        smtp_cfg.get("smtp_pass"),
                        smtp_cfg.get("use_tls", True),
                        smtp_cfg["mail_from"],
                        smtp_cfg["mail_to"],
                        subj,
                        body,
                    )
                    last_up = False
                elif is_up and not last_up:
                    subj = f"RECOVERY: {url} is UP"
                    body = f"Monitor noticed that {url} has recovered and is reachable again.\nTime: {time.strftime('%Y-%m-%d %H:%M:%S')}"
                    send_email(
                        smtp_cfg["smtp_server"],
                        smtp_cfg["smtp_port"],
                        smtp_cfg.get("smtp_user"),
                        smtp_cfg.get("smtp_pass"),
                        smtp_cfg.get("use_tls", True),
                        smtp_cfg["mail_from"],
                        smtp_cfg["mail_to"],
                        subj,
                        body,
                    )
                    last_up = True
                else:
                    # no state change -> do nothing
                    pass
            else:
                # no smtp configured; update last_up so behavior is consistent if user adds smtp later
                last_up = is_up

            time.sleep(interval)
    except KeyboardInterrupt:
        logging.info("Monitoring stopped by user.")
        sys.exit(0)


def parse_args():
    p = argparse.ArgumentParser(description="Simple uptime checker with clearer messages and optional email alerts.")
    p.add_argument("--url", "-u", default=URL, help="URL to check")
    p.add_argument("--interval", "-i", type=int, default=60, help="Seconds between checks")
    p.add_argument("--timeout", "-t", type=int, default=10, help="Request timeout in seconds")

    # SMTP / alert options (can also be provided via environment variables)
    p.add_argument("--smtp-server", default=os.environ.get("SMTP_SERVER"), help="SMTP server (env SMTP_SERVER)")
    p.add_argument("--smtp-port", type=int, default=int(os.environ.get("SMTP_PORT", "587")), help="SMTP port (env SMTP_PORT)")
    p.add_argument("--smtp-user", default=os.environ.get("SMTP_USER"), help="SMTP username (env SMTP_USER)")
    p.add_argument("--smtp-pass", default=os.environ.get("SMTP_PASS"), help="SMTP password (env SMTP_PASS)")
    p.add_argument("--smtp-tls", action="store_true", help="Use STARTTLS (default False unless port 587)")
    p.add_argument("--mail-from", default=os.environ.get("MAIL_FROM"), help="Alert from address (env MAIL_FROM)")
    p.add_argument("--mail-to", default=os.environ.get("MAIL_TO"), help="Alert recipient address (env MAIL_TO)")

    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    smtp_cfg = None
    if args.mail_to and args.mail_from and args.smtp_server:
        smtp_cfg = {
            "smtp_server": args.smtp_server,
            "smtp_port": args.smtp_port,
            "smtp_user": args.smtp_user,
            "smtp_pass": args.smtp_pass,
            "use_tls": args.smtp_tls if args.smtp_tls else (args.smtp_port == 587),
            "mail_from": args.mail_from,
            "mail_to": args.mail_to,
        }
        logging.info("Email alerts enabled to %s via %s:%s", smtp_cfg["mail_to"], smtp_cfg["smtp_server"], smtp_cfg["smtp_port"])
    else:
        logging.info("Email alerts not configured (provide --mail-to, --mail-from and --smtp-server or set env vars).")

    uptime_bot(args.url, args.interval, args.timeout, smtp_cfg)
