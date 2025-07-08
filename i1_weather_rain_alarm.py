from weather_alarm_base import WeatherAlarmBase


class WeatherRainAlarm(WeatherAlarmBase):
    """AppDaemon app for monitoring rainfall forecasts and sending notifications."""

    def _extract_weather_value(self, forecast):
        """Extract precipitation from forecast."""
        precipitation = forecast.get('precipitation')
        if precipitation is None:
            return None

        try:
            return float(precipitation)
        except (ValueError, TypeError):
            return None

    def _get_weather_description(self):
        """Get weather description for logging."""
        return "Precipitation"

    def _get_weather_unit(self):
        """Get weather unit for logging."""
        return "mm/h"

    def _get_warning_title(self):
        """Get warning title for notifications."""
        return "Rain Warning"