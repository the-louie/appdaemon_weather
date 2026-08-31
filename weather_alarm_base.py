from datetime import datetime, timedelta, time
from typing import Dict
import appdaemon.plugins.hass.hassapi as hass

import notification_policy as policy


class WeatherAlarmBase(hass.Hass):
    """Base class for weather alarm apps with shared functionality."""

    # Constants
    DEFAULT_TIME_OF_DAY = "18:15"
    DEFAULT_COOLDOWN = 86400  # 24 hours in seconds
    MIN_NOTIFICATION_INTERVAL = 60  # Minimum seconds between notifications
    MAX_MESSAGE_LENGTH = 1000
    MAX_WEATHER_VALUE = 1000
    MIN_WEATHER_VALUE = -100
    CLEANUP_MAX_AGE_DAYS = 7
    CLEANUP_TIME = "02:00"

    def __init__(self, *args, **kwargs):
        """Initialize the base class with rate limiting."""
        super().__init__(*args, **kwargs)
        self.last_notification_time: Dict[str, datetime] = {}  # Track last notification per recipient
        self.min_notification_interval = self.MIN_NOTIFICATION_INTERVAL  # Minimum seconds between notifications per recipient

    def initialize(self):
        """Initialize the weather alarm app."""
        self.log(f"Loading {self.__class__.__name__}()")

        # Get configuration from app configuration
        self.device_id = self.args.get("device_id")
        self.recipients = self.args.get("recipients")
        self.alert_name = self.args.get("name", f"{self.__class__.__name__}")
        self.limits = self.args.get("limits", [])

        # Android companion-app delivery settings. The default HA notification channel
        # can be disabled on the phone, which silently discards every notification sent
        # to it - HA reports success and nothing arrives. Sending on a dedicated channel
        # keeps weather alerts independent of that setting and lets them be muted on
        # their own without affecting other apps. See backlog T-52.
        self.notification_channel = self.args.get("notification_channel", "weather_alerts")
        self.notification_priority = self.args.get("notification_priority", "high")

        # Policy D2 quiet hours, severity-aware. Unlike the battery checker,
        # weather has a genuine "news" case: STORM VARNING! at 02:00 should wake
        # you, Lite regn should not. Severity is already encoded in each band's
        # msg_cooldown - the config assigns 3600s to the most severe bands and
        # 86400s to the mildest - so a band at or below the threshold bypasses
        # quiet hours rather than requiring a second, redundant severity flag.
        self.quiet_hours = self.args.get("quiet_hours", True)
        self.quiet_start = self.args.get("quiet_start", policy.DEFAULT_QUIET_START)
        self.quiet_end = self.args.get("quiet_end", policy.DEFAULT_QUIET_END)
        self.quiet_bypass_cooldown = self.args.get("quiet_bypass_cooldown", 3600)

        # Validate configuration
        if not self._validate_config():
            return

        # Initialize cooldown tracking
        self._initialize_cooldowns()

        # Schedule checks
        self.run_in(self.check_weather_forecast, 0)
        self._schedule_daily_checks()

        # Schedule periodic cleanup (daily at 02:00)
        cleanup_time = time(2, 0)  # 02:00
        self.run_daily(self._cleanup_old_data, cleanup_time)

        # Send startup messages to recipients who have it enabled
        self._send_startup_messages()

        self.log(f"{self.__class__.__name__} initialized - daily checks scheduled per recipient")

    def _validate_time_format(self, time_str):
        """Validate time format is HH:MM."""
        if not isinstance(time_str, str):
            return False
        try:
            parts = time_str.split(':')
            if len(parts) != 2:
                return False
            hour, minute = int(parts[0]), int(parts[1])
            return 0 <= hour <= 23 and 0 <= minute <= 59
        except (ValueError, TypeError):
            return False

    def _validate_config(self):
        """Validate the app configuration."""
        if self.device_id is None:
            self.log(f" >> {self.__class__.__name__}.initialize(): Warning - device_id not configured")
            return False

        if not self.recipients:
            self.log(f" >> {self.__class__.__name__}.initialize(): Warning - no recipients configured")
            return False

        if not isinstance(self.recipients, list):
            self.recipients = [self.recipients]

        # Process recipients - each recipient should be a dict with notification_target, startup_message, and time_of_day
        self.processed_recipients = []
        for recipient in self.recipients:
            if not isinstance(recipient, dict):
                self.log(f" >> {self.__class__.__name__}.initialize(): Warning - recipient must be a dict: {recipient}")
                return False

            if not recipient:
                self.log(f" >> {self.__class__.__name__}.initialize(): Warning - empty recipient dict")
                return False

            # Extract notification target (either 'notification_target' or 'name' field)
            notification_target = recipient.get('notification_target') or recipient.get('name')
            if not notification_target:
                self.log(f" >> {self.__class__.__name__}.initialize(): Warning - recipient missing notification_target or name: {recipient}")
                return False

            self.processed_recipients.append({
                'name': notification_target,
                'startup_message': recipient.get('startup_message', True),
                'time_of_day': recipient.get('time_of_day', self.DEFAULT_TIME_OF_DAY)
            })

            # Validate time format
            time_of_day = recipient.get('time_of_day', self.DEFAULT_TIME_OF_DAY)
            if not self._validate_time_format(time_of_day):
                self.log(f" >> {self.__class__.__name__}.initialize(): Warning - invalid time format '{time_of_day}' for recipient {notification_target}")
                return False

        if not self.limits:
            self.log(f" >> {self.__class__.__name__}.initialize(): Warning - no limits configured")
            return False

        # Validate limit ranges
        for i, limit in enumerate(self.limits):
            if not isinstance(limit, dict):
                self.log(f" >> {self.__class__.__name__}.initialize(): Warning - limit at index {i} is not a dict")
                return False

            try:
                gt = limit.get("gt", 0)
                lt = limit.get("lt", float('inf'))

                # Ensure values are numeric
                if not isinstance(gt, (int, float)) or not isinstance(lt, (int, float)):
                    self.log(f" >> {self.__class__.__name__}.initialize(): Warning - non-numeric limit values at index {i}: gt={gt}, lt={lt}")
                    return False

                # Validate range
                if gt >= lt:
                    self.log(f" >> {self.__class__.__name__}.initialize(): Warning - invalid limit range at index {i}: gt={gt}, lt={lt}")
                    return False

                # Validate cooldown if present
                cooldown = limit.get("msg_cooldown")
                if cooldown is not None and (not isinstance(cooldown, (int, float)) or cooldown < 0):
                    self.log(f" >> {self.__class__.__name__}.initialize(): Warning - invalid cooldown value at index {i}: {cooldown}")
                    return False

            except (ValueError, TypeError) as e:
                self.log(f" >> {self.__class__.__name__}.initialize(): Warning - error processing limit at index {i}: {e}")
                return False

        return True

    def _validate_weather_value(self, value):
        """Validate weather value is reasonable."""
        if value is None:
            return False

        try:
            float_value = float(value)
            # Check for reasonable ranges (adjust based on weather type)
            if not (self.MIN_WEATHER_VALUE <= float_value <= self.MAX_WEATHER_VALUE):  # Very broad range for different weather types
                self.log(f"Warning: Weather value {float_value} seems unreasonable")
                return False
            return True
        except (ValueError, TypeError):
            return False

    def _initialize_cooldowns(self):
        """Initialize cooldown tracking for each recipient and limit."""
        self.recipient_cooldowns = {}
        for recipient in self.processed_recipients:
            recipient_name = recipient['name']
            self.recipient_cooldowns[recipient_name] = {}
            for limit in self.limits:
                message = limit.get("message", "default")
                cooldown = limit.get("msg_cooldown", 86400)
                self.recipient_cooldowns[recipient_name][message] = datetime.now() - timedelta(seconds=cooldown)

    def _schedule_daily_checks(self):
        """Schedule daily checks for each recipient at their specified time."""
        scheduled_times = set()

        for recipient in self.processed_recipients:
            time_of_day = recipient['time_of_day']
            if time_of_day not in scheduled_times:
                # Only schedule once per unique time
                # Convert HH:MM string to time object for AppDaemon
                try:
                    hour, minute = map(int, time_of_day.split(':'))
                    time_obj = time(hour, minute)
                    self.run_daily(self.check_weather_forecast, time_obj)
                    self.log(f"Scheduled daily check at {time_of_day}")
                    scheduled_times.add(time_of_day)
                except (ValueError, TypeError) as e:
                    self.log(f"Error scheduling daily check for time {time_of_day}: {e}")
                    return False

    def _held_by_quiet_hours(self, msg_cooldown):
        """True when this band should be held until the quiet window opens.

        A band whose msg_cooldown is at or below `quiet_bypass_cooldown` is
        treated as critical and always delivered - STORM VARNING! at 02:00
        should wake you. Bands with no cooldown set are treated as non-critical.
        """
        if not self.quiet_hours:
            return False
        if not policy.in_quiet_hours(self.get_now().hour, self.quiet_start, self.quiet_end):
            return False
        if msg_cooldown is not None and msg_cooldown <= self.quiet_bypass_cooldown:
            return False  # severe enough to wake someone
        return True

    def _notification_data(self) -> dict:
        """Build the companion-app data block for a notification.

        Returns the Android delivery hints every notify call in this app must carry:
        a dedicated channel, plus priority/ttl so the message is not deferred by Doze.
        Returns an empty dict if no channel is configured, so the caller can pass it
        unconditionally.
        """
        if not self.notification_channel:
            return {}
        data = {"channel": self.notification_channel}
        if self.notification_priority:
            data["priority"] = self.notification_priority
            data["ttl"] = 0
        return data

    def _send_startup_messages(self):
        """Send startup verification messages to recipients who have it enabled."""
        startup_message = f"{self.alert_name} - {self.__class__.__name__} is now active and monitoring {self._get_weather_description().lower()} conditions."
        startup_message = self._sanitize_message(startup_message)
        title = self._sanitize_message(f"{self.alert_name} - Startup")

        for recipient in self.processed_recipients:
            if recipient.get('startup_message', True):
                recipient_name = recipient['name']
                try:
                    self.call_service(
                        "notify/{}".format(recipient_name),
                        title=title,
                        message=startup_message,
                        data=self._notification_data()
                    )
                    self.log(f"Startup message sent to {recipient_name}")
                except Exception as e:
                    self.log(f"Error sending startup message to {recipient_name}: {e}")
            else:
                self.log(f"Skipping startup message for {recipient['name']} (disabled)")

    def _cleanup_old_data(self, kwargs=None):
        """Clean up old cooldown data to prevent memory bloat."""
        now = datetime.now()
        max_age = timedelta(days=7)  # Keep 7 days of data

        for recipient_name in list(self.recipient_cooldowns.keys()):
            for message in list(self.recipient_cooldowns[recipient_name].keys()):
                last_time = self.recipient_cooldowns[recipient_name][message]
                if (now - last_time) > max_age:
                    del self.recipient_cooldowns[recipient_name][message]

            # Remove empty recipient entries
            if not self.recipient_cooldowns[recipient_name]:
                del self.recipient_cooldowns[recipient_name]

    def _log_with_level(self, message, level="INFO"):
        """Log message with specified level."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_message = f"[{timestamp}] {self.__class__.__name__}: {message}"

        if level == "ERROR":
            self.log(log_message, level="ERROR")
        elif level == "WARNING":
            self.log(log_message, level="WARNING")
        else:
            self.log(log_message)

    def _log_performance_metrics(self, operation, duration):
        """Log performance metrics for monitoring."""
        self._log_with_level(f"Performance: {operation} took {duration:.3f}s")

    def check_weather_forecast(self, kwargs=None):
        """Fetch weather forecast and check weather data."""
        start_time = datetime.now()
        self._log_with_level("Starting weather forecast check")

        try:
            # `type` goes as a direct kwarg, NOT as data={...}. AppDaemon puts
            # every kwarg except `target` straight into service_data, so
            # data={"type": "hourly"} produces service_data {"data": {...}} and
            # Home Assistant rejects the whole call:
            #   invalid_format: extra keys not allowed @ data['data']
            # The call then returns no forecast at all, which surfaces one
            # frame later as the misleading "Could not extract forecast data".
            # Contrast the notify/ calls below, where data= IS a real field.
            response = self.call_service(
                "weather/get_forecasts",
                target={"device_id": self.device_id},
                type="hourly"
            )

            if response is None:
                self._log_with_level("No response from weather service", "WARNING")
                return

            forecast_data = self._extract_forecast_data(response)
            if forecast_data is None:
                self._log_with_level("Could not extract forecast data from response", "WARNING")
                return

            self._check_forecast_data(forecast_data)

            # Log performance metrics
            duration = (datetime.now() - start_time).total_seconds()
            self._log_performance_metrics("weather_forecast_check", duration)

        except ValueError as e:
            self._log_with_level(f"Value error in weather service call: {e}", "ERROR")
        except TypeError as e:
            self._log_with_level(f"Type error in weather service call: {e}", "ERROR")
        except Exception as e:
            self._log_with_level(f"Unexpected error checking weather forecast: {e}", "ERROR")
            # Log the full exception for debugging
            import traceback
            self._log_with_level(f"Full traceback: {traceback.format_exc()}", "ERROR")

    def _extract_forecast_data(self, response, _depth=0):
        """Extract the forecast list from a weather/get_forecasts response.

        The response is nested and the shape depends on both the Home Assistant
        version and how AppDaemon wraps the websocket result, so this searches
        rather than assuming one layout. Shapes seen in the wild:

            {"weather.forecast_home": {"forecast": [...]}}   <- HA >= 2024.x, entity-keyed
            {"response": {"weather.x": {"forecast": [...]}}} <- AD websocket wrapper
            {"result": {"response": {...}}}                  <- AD wrapper, another layer
            {"forecast": [...]}                              <- older / direct
            [ {...}, {...} ]                                 <- bare forecast list

        The entity-keyed shape is why this previously returned None: the top-level
        key is an entity id, not 'forecast', so the old lookup fell through. Fixed
        2026-08-30 after the live service was confirmed returning 48 hourly entries
        that the app then discarded.
        """
        try:
            if _depth > 6:
                return None

            # A bare list of forecast entries.
            if isinstance(response, list):
                if response and isinstance(response[0], dict) and 'datetime' in response[0]:
                    return response
                # Otherwise recurse into the first element (legacy behaviour).
                return self._extract_forecast_data(response[0], _depth + 1) if response else None

            if not isinstance(response, dict):
                self.log(f"Unexpected response structure: {type(response)}")
                return None

            # Direct hit.
            forecast = response.get('forecast')
            if isinstance(forecast, list):
                return forecast

            # A single forecast entry.
            if 'datetime' in response:
                return [response]

            # Descend through known wrapper keys first, then any entity-id key.
            for key in ('response', 'result'):
                if key in response:
                    found = self._extract_forecast_data(response[key], _depth + 1)
                    if found is not None:
                        return found

            for key, value in response.items():
                if isinstance(value, (dict, list)):
                    found = self._extract_forecast_data(value, _depth + 1)
                    if found is not None:
                        return found

            return None

        except Exception as e:
            self.log(f"Error extracting forecast data: {e}")
            return None

    def _check_forecast_data(self, forecast_data):
        """Evaluate the whole forecast window and notify once, for its worst hour.

        This used to notify per matching forecast entry, walking the window in
        chronological order. Because the mildest hours usually come first, the
        first match spent the recipient's rate-limit budget and the PEAK of the
        window became the entry most likely to be dropped.

        Observed live 2026-08-31:

            Wind speed 36.0 triggers limit: Jätteblåsigt!
            Rate limit active for mobile_app_pixel_9_pro, skipping notification

        - the single most severe hour in the forecast, discarded after a dozen
        'Lite blåsigt' entries had already consumed the budget. An alarm that
        structurally cannot warn about the worst weather it can see is not
        doing its job.

        Severity comes from msg_cooldown, where lower means more severe. That is
        not a new convention: _held_by_quiet_hours already reads it that way, and
        it is the reason a band does not need a separate severity flag. Ties go
        to the earliest forecast time, i.e. whichever arrives soonest.
        """
        if not isinstance(forecast_data, list):
            self.log("Forecast data is not a list")
            return

        matches = []
        for forecast in forecast_data:
            if not isinstance(forecast, dict):
                continue

            # Get the weather value (implemented by subclasses)
            weather_value = self._extract_weather_value(forecast)
            if weather_value is None:
                continue

            if not self._validate_weather_value(weather_value):
                self.log(f"Skipping invalid weather value: {weather_value}")
                continue

            limit = self._match_limit(weather_value)
            if limit is None:
                continue

            forecast_time = self._parse_forecast_time(forecast.get('datetime'))
            matches.append((limit, weather_value, forecast_time))

        if not matches:
            return

        limit, weather_value, forecast_time = min(matches, key=self._severity_key)
        self.log(
            f"{self._get_weather_description()} {weather_value} "
            f"{self._get_weather_unit()} triggers limit: {limit.get('message')} "
            f"(worst of {len(matches)} matching hours)"
        )
        self._send_notification(limit, weather_value, forecast_time)

    def _match_limit(self, weather_value):
        """Return the first configured band containing this value, or None."""
        for limit in self.limits:
            gt = limit.get("gt", 0)
            lt = limit.get("lt", float('inf'))
            if gt <= weather_value < lt:
                return limit
        return None

    @staticmethod
    def _severity_key(match):
        """Sort key: most severe first, then soonest.

        `min()` over this picks the worst hour. Timestamps are compared as
        epoch floats so a tz-aware and a naive datetime can never raise, and a
        missing time sorts last rather than crashing the comparison.
        """
        limit, _weather_value, forecast_time = match
        cooldown = limit.get("msg_cooldown", float('inf'))
        when = forecast_time.timestamp() if forecast_time else float('inf')
        return (cooldown, when)

    def _extract_weather_value(self, forecast):
        """Extract weather value from forecast. Override in subclasses."""
        raise NotImplementedError("Subclasses must implement _extract_weather_value")

    def _parse_forecast_time(self, forecast_time):
        """Parse forecast time string to datetime object."""
        if not forecast_time:
            return None

        try:
            if isinstance(forecast_time, str):
                return datetime.fromisoformat(forecast_time.replace('Z', '+00:00'))
            elif isinstance(forecast_time, datetime):
                return forecast_time
        except (ValueError, TypeError):
            pass

        return None

    def _check_weather_limit(self, weather_value, forecast_time=None):
        """Check a SINGLE value against the limits and notify if it matches.

        Retained for a caller that has one reading rather than a window. The
        forecast path deliberately does not use this any more: calling it per
        entry is what let the mildest hour spend the rate limit and suppress
        the worst one. See _check_forecast_data.
        """
        # Validate weather value before processing
        if not self._validate_weather_value(weather_value):
            self.log(f"Skipping invalid weather value: {weather_value}")
            return

        limit = self._match_limit(weather_value)
        if limit is None:
            return

        self.log(f"{self._get_weather_description()} {weather_value} {self._get_weather_unit()} triggers limit: {limit.get('message')}")
        self._send_notification(limit, weather_value, forecast_time)

    def _get_weather_description(self):
        """Get weather description for logging. Override in subclasses."""
        return "Weather value"

    def _get_weather_unit(self):
        """Get weather unit for logging. Override in subclasses."""
        return "units"

    def _sanitize_message(self, message):
        """Sanitize message content for safe notification sending."""
        if not isinstance(message, str):
            return str(message)

        # Remove or escape potentially problematic characters
        # Limit length to prevent notification service issues
        max_length = 1000
        if len(message) > max_length:
            message = message[:max_length - 3] + "..."

        return message

    def _send_notification(self, triggered_limit, weather_value, forecast_time=None):
        """Send notification to all configured recipients with per-recipient cooldown."""
        now = datetime.now()
        limit_message = triggered_limit.get('message', f'{self._get_weather_description()} warning')
        cooldown_seconds = triggered_limit.get("msg_cooldown", 86400)

        # Create notification message
        message = f"{limit_message} ({weather_value:.1f} {self._get_weather_unit()})"
        if forecast_time:
            time_str = forecast_time.strftime("%Y-%m-%d %H:%M")
            full_message = f"{message}\nForecast time: {time_str}"
        else:
            full_message = message

        # Sanitize messages
        full_message = self._sanitize_message(full_message)
        title = self._sanitize_message(f"{self.alert_name} - {self._get_warning_title()}")

        self.log(f"Checking notifications for: {full_message}")

        # D2: hold non-critical bands overnight. Checked once for the whole
        # notification rather than per recipient - severity is a property of the
        # band, not of who is being told. Deliberately does NOT touch the
        # cooldown, so the alert lands when the window opens rather than being
        # treated as already sent.
        if self._held_by_quiet_hours(cooldown_seconds):
            self.log(
                f"Holding '{limit_message}' until quiet hours end "
                f"({self.quiet_start:02d}:00-{self.quiet_end:02d}:00); "
                f"band cooldown {cooldown_seconds}s is above the "
                f"{self.quiet_bypass_cooldown}s critical threshold"
            )
            return

        for recipient in self.processed_recipients:
            recipient_name = recipient['name']
            if self._should_send_notification(recipient_name, limit_message, cooldown_seconds, now):
                # Check rate limiting
                if not self._check_rate_limit(recipient_name, now):
                    self.log(f"Rate limit active for {recipient_name}, skipping notification")
                    continue

                try:
                    self.call_service(
                        "notify/{}".format(recipient_name),
                        title=title,
                        message=full_message,
                        data=self._notification_data()
                    )
                    self.recipient_cooldowns[recipient_name][limit_message] = now
                    self.last_notification_time[recipient_name] = now
                    self.log(f"Notification sent to {recipient_name}")
                except Exception as e:
                    self.log(f"Error sending notification to {recipient_name}: {e}")
            else:
                self.log(f"Cooldown active for {recipient_name} on limit '{limit_message}'")

    def _get_warning_title(self):
        """Get warning title for notifications. Override in subclasses."""
        return "Weather Warning"

    def _check_rate_limit(self, recipient, now):
        """Check if notification is allowed based on rate limiting."""
        last_time = self.last_notification_time.get(recipient)
        if last_time is None:
            return True

        time_since_last = (now - last_time).total_seconds()
        return time_since_last >= self.min_notification_interval

    def _should_send_notification(self, recipient, limit_message, cooldown_seconds, now):
        """Check if notification should be sent based on cooldown."""
        last_message_time = self.recipient_cooldowns[recipient].get(
            limit_message,
            now - timedelta(seconds=cooldown_seconds)
        )

        if (now - last_message_time).total_seconds() >= cooldown_seconds:
            return True

        remaining_cooldown = cooldown_seconds - (now - last_message_time).total_seconds()
        self.log(f"Cooldown active for {recipient} on limit '{limit_message}': {remaining_cooldown:.0f}s remaining")
        return False

    def check_state(self, new=None):
        """Legacy method for backward compatibility - not used in this implementation."""
        pass
