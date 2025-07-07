from weather_alarm_base import WeatherAlarmBase


class WeatherTemperatureAlarm(WeatherAlarmBase):
    """AppDaemon app for monitoring temperature forecasts and sending notifications."""

    def _extract_weather_value(self, forecast):
        """Extract temperature from forecast."""
        temperature = forecast.get('temperature')
        if temperature is None:
            return None

        try:
            return float(temperature)
        except (ValueError, TypeError):
            return None

    def _get_weather_description(self):
        """Get weather description for logging."""
        return "Temperature"

    def _get_weather_unit(self):
        """Get weather unit for logging."""
        return "°C"

    def _get_warning_title(self):
        """Get warning title for notifications."""
        return "Temperature Warning"
