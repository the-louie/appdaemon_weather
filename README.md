# AppDaemon Weather Alarm System

A comprehensive weather monitoring system for Home Assistant that provides intelligent notifications for wind, rain, and temperature conditions. Built with AppDaemon, this system fetches weather forecasts every 6 hours and sends personalized notifications when conditions exceed configured thresholds.

## Summary

This weather alarm system consists of three specialized AppDaemon apps that monitor different weather parameters from your Home Assistant weather integration. Each app runs independently every 6 hours, fetches hourly weather forecasts, and sends notifications to configured recipients when conditions exceed predefined limits. The system features per-recipient cooldown periods to prevent notification spam, comprehensive error handling, and a modular architecture that makes it easy to extend for additional weather parameters.

The apps use a shared base class (`WeatherAlarmBase`) that handles all common functionality including configuration validation, forecast data extraction, notification management, and cooldown tracking. Each weather-specific app only needs to implement the data extraction and display formatting for its particular weather parameter, making the codebase highly maintainable and extensible.

## Features

- **Multi-parameter monitoring**: Wind gust speeds, precipitation, and temperature
- **Intelligent scheduling**: Checks every 6 hours with immediate first run
- **Per-recipient cooldown**: Each recipient has independent notification timing
- **Configurable thresholds**: Multiple severity levels with custom messages
- **Robust error handling**: Graceful handling of missing data and service errors
- **Comprehensive logging**: Detailed logs for debugging and monitoring
- **Modular architecture**: Easy to extend for additional weather parameters
- **Timezone support**: Proper handling of forecast timestamps

## Installation

1. Copy all Python files to your AppDaemon `apps` directory
2. Add the configuration to your `apps.yaml` file (see Configuration section)
3. Restart AppDaemon

## Configuration

Add the following configuration to your `apps.yaml` file:

```yaml
WindAlarm:
  module: i1_weather_wind_alarm
  class: WeatherWindAlarm
  device_id: "your_weather_device_id"
  recipients:
    - mobile_app_your_device
  name: "Wind"
  limits:
    - lt: 20
      gt: 10
      message: "Light wind"
      msg_cooldown: 86400
    - lt: 30
      gt: 20
      message: "Strong wind"
      msg_cooldown: 86400
    - lt: 40
      gt: 30
      message: "Very strong wind!"
      msg_cooldown: 21600
    - lt: 1000
      gt: 40
      message: "STORM WARNING!"
      msg_cooldown: 3600

RainAlarm:
  module: i1_weather_rain_alarm
  class: WeatherRainAlarm
  device_id: "your_weather_device_id"
  recipients:
    - mobile_app_your_device
  name: "Rain"
  limits:
    - lt: 2.5
      gt: 0.5
      message: "Light rain"
      msg_cooldown: 86400
    - lt: 7.5
      gt: 2.5
      message: "Moderate rain"
      msg_cooldown: 86400
    - lt: 15
      gt: 7.5
      message: "Heavy rain"
      msg_cooldown: 21600
    - lt: 1000
      gt: 15
      message: "TORRENTIAL RAIN!"
      msg_cooldown: 3600

TemperatureAlarm:
  module: i1_weather_temperature_alarm
  class: WeatherTemperatureAlarm
  device_id: "your_weather_device_id"
  recipients:
    - mobile_app_your_device
  name: "Temperature"
  limits:
    - lt: 15
      gt: 10
      message: "Cool"
      msg_cooldown: 86400
    - lt: 5
      gt: 0
      message: "Cold"
      msg_cooldown: 86400
    - lt: 0
      gt: -10
      message: "Very cold!"
      msg_cooldown: 21600
    - lt: -10
      gt: -50
      message: "EXTREMELY COLD!"
      msg_cooldown: 3600
    - lt: 25
      gt: 20
      message: "Warm"
      msg_cooldown: 86400
    - lt: 30
      gt: 25
      message: "Hot"
      msg_cooldown: 86400
    - lt: 35
      gt: 30
      message: "Very hot!"
      msg_cooldown: 21600
    - lt: 1000
      gt: 35
      message: "EXTREMELY HOT!"
      msg_cooldown: 3600
```

## Configuration Parameters

### Required Parameters

- **`device_id`**: The Home Assistant device ID of your weather integration
- **`recipients`**: List of notification service names (e.g., `mobile_app_your_device`)
- **`limits`**: Array of threshold configurations

### Limit Configuration

Each limit has the following parameters:

- **`gt`**: Greater than threshold (minimum value for this limit)
- **`lt`**: Less than threshold (maximum value for this limit)
- **`message`**: Custom message to display in notifications
- **`msg_cooldown`**: Cooldown period in seconds before sending another notification

### Cooldown Periods

- **86400 seconds (24 hours)**: For moderate conditions
- **21600 seconds (6 hours)**: For severe conditions
- **3600 seconds (1 hour)**: For extreme conditions

## How It Works

1. **Initialization**: Each app validates its configuration and initializes cooldown tracking
2. **Scheduling**: Apps run immediately and then every 6 hours
3. **Forecast Fetching**: Calls Home Assistant's `weather.get_forecasts` service
4. **Data Processing**: Extracts relevant weather data from the forecast response
5. **Threshold Checking**: Compares current values against configured limits
6. **Notification Logic**: Checks per-recipient cooldowns before sending notifications
7. **Message Formatting**: Creates formatted messages with weather values and forecast times

## Architecture

### Base Class (`WeatherAlarmBase`)

Handles all shared functionality:
- Configuration validation
- Forecast data extraction
- Notification management
- Cooldown tracking
- Error handling
- Logging

### Weather-Specific Classes

Each inherits from the base class and implements:
- **`_extract_weather_value()`**: Extract specific weather data
- **`_get_weather_description()`**: Return description for logging
- **`_get_weather_unit()`**: Return appropriate units
- **`_get_warning_title()`**: Return notification title

## Extending the System

To add a new weather parameter (e.g., humidity), create a new file:

```python
from weather_alarm_base import WeatherAlarmBase

class WeatherHumidityAlarm(WeatherAlarmBase):
    def _extract_weather_value(self, forecast):
        humidity = forecast.get('humidity')
        return float(humidity) if humidity is not None else None

    def _get_weather_description(self):
        return "Humidity"

    def _get_weather_unit(self):
        return "%"

    def _get_warning_title(self):
        return "Humidity Warning"
```

## Troubleshooting

### Common Issues

1. **No notifications received**: Check recipient configuration and notification service setup
2. **Invalid device_id**: Verify the device ID in Home Assistant Developer Tools
3. **No forecast data**: Ensure your weather integration provides hourly forecasts
4. **Cooldown issues**: Check the `msg_cooldown` values in your configuration

### Logs

Check AppDaemon logs for detailed information:
- Configuration validation messages
- Forecast data extraction status
- Notification sending attempts
- Cooldown timing information

## Requirements

- Home Assistant with AppDaemon
- Weather integration with hourly forecast support
- Notification services configured in Home Assistant

## Files

- `weather_alarm_base.py`: Shared base class with common functionality
- `i1_weather_wind_alarm.py`: Wind gust speed monitoring
- `i1_weather_rain_alarm.py`: Precipitation monitoring
- `i1_weather_temperature_alarm.py`: Temperature monitoring
- `config.yaml.example`: Example configuration file
