from datetime import datetime, timedelta, time
from typing import Dict
import appdaemon.plugins.hass.hassapi as hass


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
                        message=startup_message
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
            response = self.call_service(
                "weather/get_forecasts",
                target={"device_id": self.device_id},
                data={"type": "hourly"}
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

    def _extract_forecast_data(self, response):
        """Extract forecast data from the service response."""
        try:
            # Handle different response structures
            if isinstance(response, list) and response:
                response = response[0]

            if isinstance(response, dict):
                if 'forecast' in response:
                    return response['forecast']
                elif 'datetime' in response:
                    return [response]

            if isinstance(response, list):
                return response

            self.log(f"Unexpected response structure: {type(response)}")
            return None

        except Exception as e:
            self.log(f"Error extracting forecast data: {e}")
            return None

    def _check_forecast_data(self, forecast_data):
        """Check weather data in forecast data against limits. Override in subclasses."""
        if not isinstance(forecast_data, list):
            self.log("Forecast data is not a list")
            return

        for forecast in forecast_data:
            if not isinstance(forecast, dict):
                continue

            # Get the weather value (implemented by subclasses)
            weather_value = self._extract_weather_value(forecast)
            if weather_value is None:
                continue

            forecast_time = self._parse_forecast_time(forecast.get('datetime'))
            self._check_weather_limit(weather_value, forecast_time)

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
        """Check if weather value exceeds any configured limits."""
        # Validate weather value before processing
        if not self._validate_weather_value(weather_value):
            self.log(f"Skipping invalid weather value: {weather_value}")
            return

        for limit in self.limits:
            gt = limit.get("gt", 0)
            lt = limit.get("lt", float('inf'))

            if gt <= weather_value < lt:
                self.log(f"{self._get_weather_description()} {weather_value} {self._get_weather_unit()} triggers limit: {limit.get('message')}")
                self._send_notification(limit, weather_value, forecast_time)
                break

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
                        message=full_message
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
