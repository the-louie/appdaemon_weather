from weather_alarm_base import WeatherAlarmBase


class WeatherWindAlarm(WeatherAlarmBase):
    """AppDaemon app for monitoring wind gust forecasts and sending notifications."""

    def _extract_weather_value(self, forecast):
        """Extract wind gust speed from forecast."""
        wind_gust_speed = forecast.get('wind_gust_speed')
        if wind_gust_speed is None:
            return None

        try:
            return float(wind_gust_speed)
        except (ValueError, TypeError):
            return None

    def _get_weather_description(self):
        """Get weather description for logging."""
        return "Wind speed"

    def _get_weather_unit(self):
        """Get weather unit for logging."""
        return "m/s"

    def _get_warning_title(self):
        """Get warning title for notifications."""
        return "Wind Warning"