from weather_alarm_base import WeatherAlarmBase

# Home Assistant reports wind in the unit its weather integration is configured
# for, and on this instance that is km/h (`weather.forecast_home` carries
# wind_speed_unit: km/h). The limit bands are written in m/s -- 10 / 20 / 30 / 40
# with STORM VARNING! above 40 -- because m/s is what Sweden uses for wind.
#
# Those two facts disagreed silently. The app read the raw forecast number and
# labelled it "m/s", so on 2026-08-31 a 36 km/h breeze (10 m/s) was reported as
# `Jätteblåsigt!`, and every band below the top one fired at roughly a third of
# the wind it was meant to. Nobody saw it earlier because the forecast call
# itself had been failing, so no wind was ever evaluated.
WIND_UNIT_TO_MS = {
    "m/s": 1.0,
    "km/h": 1.0 / 3.6,
    "mph": 0.44704,
    "kn": 0.514444,
}

# What the forecast provider reports, NOT what the limits are written in. The
# limits are always m/s. Matches this instance's Home Assistant today; set
# `wind_speed_unit` in config.yaml if the provider ever changes.
DEFAULT_WIND_UNIT = "km/h"


def to_metres_per_second(value, unit):
    """Convert a wind speed to m/s. Raises ValueError on an unknown unit.

    A free function so the conversion is testable without an AppDaemon app.
    """
    try:
        factor = WIND_UNIT_TO_MS[unit]
    except KeyError:
        raise ValueError(
            f"unknown wind_speed_unit {unit!r}; expected one of "
            f"{', '.join(sorted(WIND_UNIT_TO_MS))}"
        ) from None
    return value * factor


class WeatherWindAlarm(WeatherAlarmBase):
    """AppDaemon app for monitoring wind gust forecasts and sending notifications."""

    def initialize(self):
        """Validate the source unit before anything can act on a bad one.

        Policy D1: fail loud. A silently wrong unit is exactly the defect this
        app just had, and it misreports storms rather than crashing -- which is
        far worse than refusing to start.
        """
        unit = self.args.get("wind_speed_unit", DEFAULT_WIND_UNIT)
        if unit not in WIND_UNIT_TO_MS:
            raise ValueError(
                f"'wind_speed_unit' must be one of "
                f"{', '.join(sorted(WIND_UNIT_TO_MS))}, got {unit!r}"
            )
        self.wind_speed_unit = unit
        super().initialize()
        if unit != "m/s":
            self.log(
                f"Wind forecasts are read as {unit} and converted to m/s; "
                f"limit bands are m/s"
            )

    def _extract_weather_value(self, forecast):
        """Extract wind gust speed from forecast, converted to m/s."""
        wind_gust_speed = forecast.get('wind_gust_speed')
        if wind_gust_speed is None:
            return None

        try:
            value = float(wind_gust_speed)
        except (ValueError, TypeError):
            return None

        # Unit is validated in initialize(), so this cannot raise here.
        return to_metres_per_second(value, self.wind_speed_unit)

    def _get_weather_description(self):
        """Get weather description for logging."""
        return "Wind speed"

    def _get_weather_unit(self):
        """Get weather unit for logging."""
        return "m/s"

    def _get_warning_title(self):
        """Get warning title for notifications."""
        return "Wind Warning"
