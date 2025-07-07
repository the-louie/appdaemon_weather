from datetime import datetime, timedelta
import appdaemon.plugins.hass.hassapi as hass


class WeatherAlarmBase(hass.Hass):
    """Base class for weather alarm apps with shared functionality."""

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
        self.run_every(self.check_weather_forecast, "now", 6 * 60 * 60)  # 6 hours

        self.log(f"{self.__class__.__name__} initialized - will check every 6 hours")

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

        if not self.limits:
            self.log(f" >> {self.__class__.__name__}.initialize(): Warning - no limits configured")
            return False

        # Validate limit ranges
        for i, limit in enumerate(self.limits):
            gt = limit.get("gt", 0)
            lt = limit.get("lt", float('inf'))
            if gt >= lt:
                self.log(f" >> {self.__class__.__name__}.initialize(): Warning - invalid limit range at index {i}: gt={gt}, lt={lt}")
                return False

        return True

    def _initialize_cooldowns(self):
        """Initialize cooldown tracking for each recipient and limit."""
        self.recipient_cooldowns = {}
        for recipient in self.recipients:
            self.recipient_cooldowns[recipient] = {}
            for limit in self.limits:
                message = limit.get("message", "default")
                cooldown = limit.get("msg_cooldown", 86400)
                self.recipient_cooldowns[recipient][message] = datetime.now() - timedelta(seconds=cooldown)

    def check_weather_forecast(self, kwargs=None):
        """Fetch weather forecast and check weather data."""
        self.log("Checking weather forecast...")

        try:
            response = self.call_service(
                "weather/get_forecasts",
                target={"device_id": self.device_id},
                data={"type": "hourly"}
            )

            if response is None:
                self.log("No response from weather service")
                return

            forecast_data = self._extract_forecast_data(response)
            if forecast_data is None:
                self.log("Could not extract forecast data from response")
                return

            self._check_forecast_data(forecast_data)

        except Exception as e:
            self.log(f"Error checking weather forecast: {e}")

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

        self.log(f"Checking notifications for: {full_message}")

        for recipient in self.recipients:
            if self._should_send_notification(recipient, limit_message, cooldown_seconds, now):
                try:
                    self.call_service(
                        "notify/{}".format(recipient),
                        title=f"{self.alert_name} - {self._get_warning_title()}",
                        message=full_message
                    )
                    self.recipient_cooldowns[recipient][limit_message] = now
                    self.log(f"Notification sent to {recipient}")
                except Exception as e:
                    self.log(f"Error sending notification to {recipient}: {e}")

    def _get_warning_title(self):
        """Get warning title for notifications. Override in subclasses."""
        return "Weather Warning"

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
