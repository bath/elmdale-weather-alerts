import os
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import boto3

ses = boto3.client("ses")
dynamodb = boto3.resource("dynamodb")
sns = boto3.client("sns")


# ---------- Weather fetching (One Call 3.0) ----------

def get_weather():
    """
    Calls OpenWeather One Call 3.0 API to get hourly + daily forecast.
    """
    api_key = os.environ["WEATHER_API_KEY"]
    lat = os.environ.get("LAT", "38.3736")
    lon = os.environ.get("LON", "-96.6447")

    url = (
        "https://api.openweathermap.org/data/3.0/onecall"
        f"?lat={lat}&lon={lon}"
        "&exclude=minutely,alerts,current"
        "&units=imperial"
        f"&appid={api_key}"
    )

    print("Requesting URL (appid redacted):", url.replace(api_key, "REDACTED"))

    try:
        with urllib.request.urlopen(url) as response:
            data = json.loads(response.read().decode("utf-8"))
        return data
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print("OpenWeather HTTP error:", e.code, body)
        raise


def find_freeze_hours(hourly, hours_ahead, threshold_f):
    """
    Return list of hourly entries within `hours_ahead` where temp <= threshold_f.
    """
    upcoming = hourly[:hours_ahead]
    return [
        h for h in upcoming
        if h.get("temp") is not None and h["temp"] <= threshold_f
    ]


def find_warm_clear_days(daily, warm_clear_days, warm_threshold_f):
    """
    Look at the next `warm_clear_days` entries in the daily forecast.
    Return True if *all* of those days have temp.min >= warm_threshold_f.
    """
    if not daily:
        return False

    upcoming_days = daily[:warm_clear_days]
    for d in upcoming_days:
        temp = d.get("temp", {})
        min_temp = temp.get("min")
        if min_temp is None or min_temp < warm_threshold_f:
            return False

    return True


# ---------- Time formatting ----------

def format_time(ts_utc):
    """Convert unix timestamp to a nice local time string in Central time."""
    ct = datetime.fromtimestamp(ts_utc, tz=timezone.utc).astimezone(
        ZoneInfo("America/Chicago")
    )
    return ct.strftime("%Y-%m-%d %I:%M %p %Z")


def format_date(ts_utc):
    """Date-only, Central time."""
    ct = datetime.fromtimestamp(ts_utc, tz=timezone.utc).astimezone(
        ZoneInfo("America/Chicago")
    )
    return ct.strftime("%Y-%m-%d")


# ---------- Email helpers (SES) ----------

def base_recipients_and_sender():
    sender = os.environ["SES_SENDER"]
    recipient_list = [
        addr.strip()
        for addr in os.environ["RECIPIENTS"].split(",")
        if addr.strip()
    ]
    return sender, recipient_list


def send_freeze_email(freeze_hours, min_temp, hours_ahead, threshold_f):
    sender, recipient_list = base_recipients_and_sender()

    first_freeze = freeze_hours[0]
    last_freeze = freeze_hours[-1]

    start_time = format_time(first_freeze["dt"])
    end_time = format_time(last_freeze["dt"])

    subject = "❄️ Cold Alert: Turn ON Bathroom Heaters"

    body_lines = [
        "❄️ ACTION REQUIRED: Turn ON bathroom heaters.",
        "",
        "🔍 What was checked:",
        f"- Analyzed hourly forecast for the next {hours_ahead} hours",
        f"- Freeze threshold: {threshold_f:.1f}°F",
        f"- Found {len(freeze_hours)} hour(s) at or below threshold",
        "",
        "📊 Forecast details:",
        f"- Lowest temperature: {min_temp:.1f}°F",
        f"- Freeze period: {start_time} to {end_time}",
        "",
        "💡 Why this action is required:",
        f"- Temperatures are forecast to reach {min_temp:.1f}°F, which is at or below the {threshold_f:.1f}°F threshold",
        "- This indicates freeze risk that could damage bathroom plumbing",
        "- Heaters should be activated to prevent freezing",
    ]

    body_text = "\n".join(body_lines)

    ses.send_email(
        Source=sender,
        Destination={"ToAddresses": recipient_list},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {"Text": {"Data": body_text, "Charset": "UTF-8"}},
        },
    )


def send_warm_ok_email(warm_clear_days, warm_threshold_f, hours_ahead, threshold_f):
    sender, recipient_list = base_recipients_and_sender()

    subject = "☀️ Warm Alert: Turn OFF Bathroom Heaters"

    body_lines = [
        "☀️ ACTION REQUIRED: Turn OFF bathroom heaters.",
        "",
        "🔍 What was checked:",
        f"- Analyzed hourly forecast for the next {hours_ahead} hours for immediate freeze risk",
        f"- Analyzed daily forecast for the next {warm_clear_days} day(s) for sustained warm conditions",
        f"- Freeze threshold: {threshold_f:.1f}°F",
        f"- Warm threshold: {warm_threshold_f:.1f}°F",
        "",
        "✅ Check results:",
        f"- No temperatures at or below {threshold_f:.1f}°F in the next {hours_ahead} hours",
        f"- All {warm_clear_days} upcoming day(s) have minimum temperatures at or above {warm_threshold_f:.1f}°F",
        "",
        "💡 Why this action is safe:",
        "- No immediate freeze risk exists in the short-term forecast",
        f"- Sustained warm conditions are forecast for the next {warm_clear_days} day(s)",
        "- Heaters can be safely turned off to conserve energy",
    ]

    body_text = "\n".join(body_lines)

    ses.send_email(
        Source=sender,
        Destination={"ToAddresses": recipient_list},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {"Text": {"Data": body_text, "Charset": "UTF-8"}},
        },
    )


def send_status_email(
    last_state,
    current_state,
    hourly,
    daily,
    hours_ahead,
    threshold_f,
    warm_clear_days,
    warm_threshold_f,
):
    sender, recipient_list = base_recipients_and_sender()

    subject = "🧪 Test Alert: Elmdale Weather Monitor Status"

    # Short-term hourly summary
    upcoming = hourly[:hours_ahead]
    short_term_line = "No hourly data available."
    freeze_hours_count = 0
    if upcoming:
        temps = [h.get("temp") for h in upcoming if h.get("temp") is not None]
        if temps:
            short_min = min(temps)
            short_max = max(temps)
            freeze_hours_count = len([t for t in temps if t <= threshold_f])
            short_term_line = (
                f"Next {len(temps)} hourly points: "
                f"min {short_min:.1f}°F, max {short_max:.1f}°F "
                f"(threshold: {threshold_f:.1f}°F)"
            )

    # Daily forecast summary (up to 10 days)
    day_lines = []
    if daily:
        for i, d in enumerate(daily[:10]):
            dt = d.get("dt")
            temp = d.get("temp", {})
            min_t = temp.get("min")
            max_t = temp.get("max")
            date_str = format_date(dt) if dt is not None else f"Day {i+1}"

            tag = ""
            if min_t is not None and min_t <= threshold_f:
                tag = " (⚠️ below freeze threshold)"
            elif min_t is not None and min_t < warm_threshold_f:
                tag = " (below warm-clear threshold)"

            day_lines.append(
                f"- {date_str}: low {min_t:.1f}°F, high {max_t:.1f}°F{tag}"
            )

    if not day_lines:
        day_lines.append("- (no daily forecast data available)")

    body_lines = [
        "🧪 TEST MODE: This is a status check email. No state changes were made.",
        "",
        "🔍 What was checked:",
        f"- Hourly forecast analysis: next {hours_ahead} hours",
        f"- Daily forecast analysis: next {warm_clear_days} day(s) for warm-clear determination",
        f"- Freeze threshold: {threshold_f:.1f}°F",
        f"- Warm threshold: {warm_threshold_f:.1f}°F",
        "",
        "📊 Current system state:",
        f"- Last stored state: {last_state or 'None (initial run)'}",
        f"- Forecast-derived state: {current_state}",
        "",
        "📈 Short-term forecast results:",
        short_term_line,
        f"- Hours at/below freeze threshold: {freeze_hours_count}",
        "",
        "📅 Daily forecast (next 10 days):",
        *day_lines,
        "",
        "ℹ️ Note: This is a test email. The stored state was not modified.",
    ]

    body_text = "\n".join(body_lines)

    ses.send_email(
        Source=sender,
        Destination={"ToAddresses": recipient_list},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {"Text": {"Data": body_text, "Charset": "UTF-8"}},
        },
    )


# ---------- SMS helpers (SNS) ----------

def get_sns_topic_arn():
    arn = os.environ.get("SNS_TOPIC_ARN")
    if not arn:
        print("SNS_TOPIC_ARN not set; skipping SMS.")
    return arn


def send_freeze_sms(min_temp, hours_ahead, start_time, end_time):
    topic_arn = get_sns_topic_arn()
    if not topic_arn:
        return

    msg = (
        f"Freeze alert for Elmdale bathroom: forecast low {min_temp:.1f}°F "
        f"in the next {hours_ahead} hours ({start_time} to {end_time}). "
        f"Turn bathroom heaters ON."
    )

    sns.publish(
        TopicArn=topic_arn,
        Message=msg,
        Subject="Elmdale Freeze Alert",
    )


def send_warm_ok_sms(warm_clear_days, warm_threshold_f):
    topic_arn = get_sns_topic_arn()
    if not topic_arn:
        return

    msg = (
        "Warm-clear alert for Elmdale bathroom: "
        f"next {warm_clear_days} nights have lows ≥ {warm_threshold_f:.1f}°F. "
        "Safe to turn bathroom heaters OFF."
    )

    sns.publish(
        TopicArn=topic_arn,
        Message=msg,
        Subject="Elmdale Warm-Clear Alert",
    )


def send_test_sms():
    topic_arn = get_sns_topic_arn()
    if not topic_arn:
        print("SNS_TOPIC_ARN not set; skipping TEST SMS.")
        return

    msg = "TEST SMS from Elmdale freeze monitor: SNS wiring is working."
    sns.publish(
        TopicArn=topic_arn,
        Message=msg,
        Subject="Elmdale Freeze Monitor TEST",
    )


# ---------- State persistence (DynamoDB) ----------

def get_state_table():
    table_name = os.environ["STATE_TABLE_NAME"]
    return dynamodb.Table(table_name)


def get_last_state():
    """
    Returns 'COLD', 'WARM', or None if no state has been stored yet.
    """
    table = get_state_table()
    resp = table.get_item(Key={"id": "main"})
    item = resp.get("Item")
    if not item:
        return None
    return item.get("mode")


def set_last_state(mode):
    table = get_state_table()
    now_iso = datetime.now(timezone.utc).isoformat()
    table.put_item(
        Item={
            "id": "main",
            "mode": mode,
            "updated_at": now_iso,
        }
    )


# ---------- Main handler ----------

def lambda_handler(event, context):
    # Configs
    hours_ahead = int(os.environ.get("HOURS_AHEAD", "12"))
    threshold_f = float(os.environ.get("FREEZE_THRESHOLD_F", "32"))

    warm_clear_days = int(os.environ.get("WARM_CLEAR_DAYS", "2"))
    warm_threshold_f = float(os.environ.get("WARM_THRESHOLD_F", "35"))

    mode = "NORMAL"
    if isinstance(event, dict):
        mode = event.get("mode", "NORMAL")

    try:
        weather = get_weather()
        hourly = weather.get("hourly", [])
        daily = weather.get("daily", [])

        if not hourly:
            print("No hourly data in weather response.")
            if mode == "TEST":
                last_state = get_last_state()
                send_status_email(
                    last_state=last_state,
                    current_state="UNKNOWN (no hourly data)",
                    hourly=[],
                    daily=daily or [],
                    hours_ahead=hours_ahead,
                    threshold_f=threshold_f,
                    warm_clear_days=warm_clear_days,
                    warm_threshold_f=warm_threshold_f,
                )
                send_test_sms()
                return {
                    "statusCode": 200,
                    "body": "TEST: sent status email + test SMS with limited data.",
                }
            elif mode == "TEST_SMS_ONLY":
                send_test_sms()
                return {
                    "statusCode": 200,
                    "body": "TEST_SMS_ONLY: test SMS sent (no hourly data).",
                }
            return {"statusCode": 200, "body": "No hourly data."}

        # 1) Check for immediate freeze risk
        freeze_hours = find_freeze_hours(hourly, hours_ahead, threshold_f)

        # 2) Check for warm-clear window (if no freeze)
        warm_ok = False
        if not freeze_hours and daily:
            warm_ok = find_warm_clear_days(daily, warm_clear_days, warm_threshold_f)

        # 3) Determine current state from forecast
        if warm_ok:
            current_state = "WARM"
        else:
            current_state = "COLD"  # default conservative

        last_state = get_last_state()
        print(
            f"Last state: {last_state}, current forecast state: {current_state}, "
            f"mode: {mode}"
        )

        # ---------- TEST MODE ----------
        if mode == "TEST":
            send_status_email(
                last_state=last_state,
                current_state=current_state,
                hourly=hourly,
                daily=daily or [],
                hours_ahead=hours_ahead,
                threshold_f=threshold_f,
                warm_clear_days=warm_clear_days,
                warm_threshold_f=warm_threshold_f,
            )
            send_test_sms()
            return {
                "statusCode": 200,
                "body": "TEST: status email + test SMS sent; FSM state unchanged.",
            }

        # ---------- TEST_SMS_ONLY MODE ----------
        if mode == "TEST_SMS_ONLY":
            send_test_sms()
            return {
                "statusCode": 200,
                "body": "TEST_SMS_ONLY: test SMS sent.",
            }

        # ---------- TEST_COLD MODE ----------
        if mode == "TEST_COLD":
            # Simulate cold conditions: create mock weather data with freeze risk
            now_ts = int(time.time())
            mock_hourly = []
            for i in range(hours_ahead):
                # Create hours with temperatures below threshold
                mock_hourly.append({
                    "dt": now_ts + (i * 3600),
                    "temp": threshold_f - 2.0 - (i * 0.5),  # Decreasing temps below threshold
                })
            
            mock_daily = []
            for i in range(warm_clear_days + 1):
                # Create days with min temps below warm threshold
                mock_daily.append({
                    "dt": now_ts + (i * 86400),
                    "temp": {
                        "min": warm_threshold_f - 5.0,
                        "max": warm_threshold_f + 10.0,
                    }
                })
            
            # Process with mock data
            test_freeze_hours = find_freeze_hours(mock_hourly, hours_ahead, threshold_f)
            test_min_temp = min(h["temp"] for h in test_freeze_hours) if test_freeze_hours else mock_hourly[0]["temp"]
            
            # Send cold alert email (but don't change state)
            if test_freeze_hours:
                send_freeze_email(test_freeze_hours, test_min_temp, hours_ahead, threshold_f)
                start_time = format_time(test_freeze_hours[0]["dt"])
                end_time = format_time(test_freeze_hours[-1]["dt"])
            else:
                send_freeze_email([mock_hourly[0]], test_min_temp, hours_ahead, threshold_f)
                start_time = format_time(mock_hourly[0]["dt"])
                end_time = start_time
            
            return {
                "statusCode": 200,
                "body": "TEST_COLD: cold alert email sent with simulated freeze conditions; FSM state unchanged.",
            }

        # ---------- TEST_WARM MODE ----------
        if mode == "TEST_WARM":
            # Simulate warm conditions: create mock weather data with no freeze risk
            now_ts = int(time.time())
            mock_hourly = []
            for i in range(hours_ahead):
                # Create hours with temperatures above threshold
                mock_hourly.append({
                    "dt": now_ts + (i * 3600),
                    "temp": threshold_f + 10.0 + (i * 0.5),  # Increasing temps above threshold
                })
            
            mock_daily = []
            for i in range(warm_clear_days):
                # Create days with min temps above warm threshold
                mock_daily.append({
                    "dt": now_ts + (i * 86400),
                    "temp": {
                        "min": warm_threshold_f + 5.0,
                        "max": warm_threshold_f + 25.0,
                    }
                })
            
            # Send warm alert email (but don't change state)
            send_warm_ok_email(warm_clear_days, warm_threshold_f, hours_ahead, threshold_f)
            
            return {
                "statusCode": 200,
                "body": "TEST_WARM: warm alert email sent with simulated warm conditions; FSM state unchanged.",
            }

        # ---------- NORMAL MODE FSM LOGIC ----------

        if last_state is None:
            # First run: set state and send one alert so you know where you are.
            set_last_state(current_state)
            if current_state == "COLD":
                if not freeze_hours:
                    # No specific freeze hours but still not warm-clear:
                    fake_hour = {
                        "dt": hourly[0]["dt"],
                        "temp": hourly[0]["temp"],
                    }
                    min_temp = fake_hour["temp"]
                    send_freeze_email([fake_hour], min_temp, hours_ahead, threshold_f)
                    start_time = format_time(fake_hour["dt"])
                    end_time = start_time
                else:
                    min_temp = min(h["temp"] for h in freeze_hours)
                    send_freeze_email(freeze_hours, min_temp, hours_ahead, threshold_f)
                    start_time = format_time(freeze_hours[0]["dt"])
                    end_time = format_time(freeze_hours[-1]["dt"])

                send_freeze_sms(min_temp, hours_ahead, start_time, end_time)
                msg = "Initial state set to COLD and freeze-type alert sent."

            else:
                send_warm_ok_email(warm_clear_days, warm_threshold_f, hours_ahead, threshold_f)
                send_warm_ok_sms(warm_clear_days, warm_threshold_f)
                msg = "Initial state set to WARM and warm-type alert sent."

            return {"statusCode": 200, "body": msg}

        # No change in state -> no email/SMS
        if current_state == last_state:
            print("State unchanged; no alert sent.")
            return {
                "statusCode": 200,
                "body": f"State unchanged ({current_state}); no alert.",
            }

        # State changed -> send one alert and update
        set_last_state(current_state)

        if current_state == "COLD":
            # Transition WARM -> COLD
            if not freeze_hours:
                fake_hour = {
                    "dt": hourly[0]["dt"],
                    "temp": hourly[0]["temp"],
                }
                min_temp = fake_hour["temp"]
                send_freeze_email([fake_hour], min_temp, hours_ahead, threshold_f)
                start_time = format_time(fake_hour["dt"])
                end_time = start_time
            else:
                min_temp = min(h["temp"] for h in freeze_hours)
                send_freeze_email(freeze_hours, min_temp, hours_ahead, threshold_f)
                start_time = format_time(freeze_hours[0]["dt"])
                end_time = format_time(freeze_hours[-1]["dt"])

            send_freeze_sms(min_temp, hours_ahead, start_time, end_time)

            return {
                "statusCode": 200,
                "body": "Transition to COLD; freeze alert sent.",
            }

        else:  # current_state == "WARM"
            # Transition COLD -> WARM
            send_warm_ok_email(warm_clear_days, warm_threshold_f, hours_ahead, threshold_f)
            send_warm_ok_sms(warm_clear_days, warm_threshold_f)
            return {
                "statusCode": 200,
                "body": "Transition to WARM; warm-ok alert sent.",
            }

    except Exception as e:
        print(f"Error in lambda_handler: {e}")
        return {"statusCode": 500, "body": str(e)}
