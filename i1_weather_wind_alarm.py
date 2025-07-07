from datetime import datetime
import json
import appdaemon.plugins.hass.hassapi as hass

#WindAlarm:
#  module: i1_weather_wind_alarm
#  class: WeatherWindAlarm
#  sensor: "weather.smhi_home"
#  recipients:
#    - mobile_app_pixel_9_pro
#  name: "Vind"
#  limits:
#    - lt: 20
#      gt: 10
#      message: "Lite blåsigt"
#      msg_cooldown: 86400
#    - lt: 30
#      gt: 20
#      message: "Mycket blåsigt"
#      msg_cooldown: 86400
#    - lt: 40
#      gt: 30
#      message: "Jätteblåsigt!"
#      msg_cooldown: 21600
#    - lt: 1000
#      gt: 40
#      message: "STORM VARNING!"
#      msg_cooldown: 3600

class WeatherWindAlarm(hass.Hass):
  def initialize(self):
    self.log("Loading WindAlarm()")

    self.sensor = self.args.get("sensor")
    self.recipients = self.args.get("recipients")
    self.alert_name = self.args.get("name")
    self.limits = self.args.get("limits")

    if self.sensor is None:
      self.log(" >> WindAlarm.initialize(): Warning - Not configured")
      return

    if not isinstance(self.recipients, list):
      self.recipients = [self.recipients]

    self.listen_state(self.state_change, self.sensor, attribute="wind_speed")
    
    self.log(" >> WindAlarm {} ==> {}".format(self.sensor,
                                                 self.recipients))

    # add some more stuff to the limits dict
    for limit in self.limits:
        limit["lmts"] = datetime(1970, 1, 1) # last message timestamp

    self.check_state(self.get_state(self.sensor, "wind_speed"))

  def state_change(self, entity, attribute, old, new, kwargs):
    if attribute != "wind_speed":
        return
    if new != old and new is not None:
      self.check_state(new)


  def check_state(self, new):
    if new is None:
        return

    self.log("check_state({})".format(new))
    value = float(new)

    now = datetime.now()
    message = None
    for limit in self.limits:
        self.log("lim: {}".format(limit))
        if value < limit.get("lt") and value >= limit.get("gt"):
            if (now - limit.get("lmts")).total_seconds() > limit.get("msg_cooldown"):
                self.log("SEND: {}".format(limit.get("message")))
                message = "{} ({}°)".format(limit.get("message"), value)
                limit["lmts"] = datetime.now()
            else:
                self.log("Cooldown active {} {}".format((now - limit.get("lmts")).total_seconds(), limit.get("msg_cooldown")))

            break

    if message is None:
        self.log("No message, returning")
        return

    for recipient in self.recipients:
        self.log("sending '{}' to {}".format(message, recipient))
        self.call_service("notify/{}".format(recipient), title="{} temp".format(self.alert_name), message=message)
    
