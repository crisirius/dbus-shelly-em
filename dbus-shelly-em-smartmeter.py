#!/usr/bin/env python

# import normal packages
import platform 
import logging
import sys
import os
if sys.version_info.major == 2:
    import gobject
else:
    from gi.repository import GLib as gobject
import time
import requests # for http GET
import configparser # for config/ini file

# our own packages from victron
sys.path.insert(1, os.path.join(os.path.dirname(__file__), '/opt/victronenergy/dbus-systemcalc-py/ext/velib_python'))
from vedbus import VeDbusService


class DbusShellyemService:
  def __init__(self, servicename, paths, productname='Shelly EM', connection='Shelly EM HTTP JSON service'):
    config = self._getConfig()
    deviceinstance = int(config['DEFAULT']['Deviceinstance'])
    customname = config['DEFAULT']['CustomName']
    self.channel = int(config['DEFAULT'].get('Channel', 0))           # Kanal Index (0 oder 1)
    self.role = config['DEFAULT'].get('VictronRole', 'grid')          # Victron Role (pvinverter oder grid)
    self.position = int(config['DEFAULT'].get('AcPosition', 0))       # AcPosition (0=AC input 1; 1=AC output; 2=AC input 2)
    
    self._dbusservice = VeDbusService("{}.http_{:02d}".format(servicename, deviceinstance))
    self._paths = paths
    
    logging.debug("%s /DeviceInstance = %d" % (servicename, deviceinstance))
    
    # Create the management objects, as specified in the ccgx dbus-api document
    self._dbusservice.add_path('/Mgmt/ProcessName', __file__)
    self._dbusservice.add_path('/Mgmt/ProcessVersion', 'Unknown version, running on Python ' + platform.python_version())
    self._dbusservice.add_path('/Mgmt/Connection', connection)
    
    # Create the mandatory objects
    self._dbusservice.add_path('/DeviceInstance', deviceinstance)
    self._dbusservice.add_path('/ProductId', 45069)  # Beispiel ET340 Energy Meter
    self._dbusservice.add_path('/DeviceType', 345)   # Beispiel ET340 Energy Meter
    self._dbusservice.add_path('/ProductName', productname)
    self._dbusservice.add_path('/CustomName', customname)    
    self._dbusservice.add_path('/Latency', None)    
    self._dbusservice.add_path('/FirmwareVersion', 0.1)
    self._dbusservice.add_path('/HardwareVersion', 0)
    self._dbusservice.add_path('/Connected', 1)
    self._dbusservice.add_path('/Role', self.role)          # Victron Role aus Config
    self._dbusservice.add_path('/Position', self.position)  # AcPosition aus Config
    self._dbusservice.add_path('/Serial', self._getShellySerial())
    self._dbusservice.add_path('/UpdateIndex', 0)
    
    # add path values to dbus
    for path, settings in self._paths.items():
      self._dbusservice.add_path(
        path, settings['initial'], gettextcallback=settings['textformat'], writeable=True, onchangecallback=self._handlechangedvalue)

    # last update
    self._lastUpdate = 0

    # add _update function 'timer'
    gobject.timeout_add(250, self._update)  # Pause 250ms bevor nächste Anfrage
    
    # add _signOfLife 'timer' für Log-Feedback alle 5 Minuten
    gobject.timeout_add(self._getSignOfLifeInterval()*60*1000, self._signOfLife)
 
  def _getShellySerial(self):
    meter_data = self._getShellyData()  
    if not meter_data['mac']:
        raise ValueError("Response does not contain 'mac' attribute")
    return meter_data['mac']
 
  def _getConfig(self):
    config = configparser.ConfigParser()
    config.read("%s/config.ini" % (os.path.dirname(os.path.realpath(__file__))))
    return config
 
  def _getSignOfLifeInterval(self):
    config = self._getConfig()
    value = config['DEFAULT']['SignOfLifeLog']
    if not value: 
        value = 0
    return int(value)
  
  def _getShellyStatusUrl(self):
    config = self._getConfig()
    accessType = config['DEFAULT']['AccessType']
    if accessType == 'OnPremise': 
        URL = "http://%s:%s@%s/status" % (config['ONPREMISE']['Username'], config['ONPREMISE']['Password'], config['ONPREMISE']['Host'])
        URL = URL.replace(":@", "")
    else:
        raise ValueError("AccessType %s is not supported" % (config['DEFAULT']['AccessType']))
    return URL
 
  def _getShellyData(self):
    URL = self._getShellyStatusUrl()
    meter_r = requests.get(url=URL)
    if not meter_r:
        raise ConnectionError("No response from Shelly EM - %s" % (URL))
    meter_data = meter_r.json()
    if not meter_data:
        raise ValueError("Converting response to JSON failed")
    return meter_data
 
  def _signOfLife(self):
    logging.info("--- Start: sign of life ---")
    logging.info("Last _update() call: %s" % (self._lastUpdate))
    logging.info("Last '/Ac/Power': %s" % (self._dbusservice['/Ac/Power']))
    logging.info("--- End: sign of life ---")
    return True
 
  def _update(self):
    try:
      meter_data = self._getShellyData()
      idx = self.channel

      self._dbusservice['/Ac/L1/Voltage'] = meter_data['emeters'][idx]['voltage']
      current = meter_data['emeters'][idx]['power'] / meter_data['emeters'][idx]['voltage']
      self._dbusservice['/Ac/L1/Current'] = current
      self._dbusservice['/Ac/L1/Power'] = meter_data['emeters'][idx]['power']
      self._dbusservice['/Ac/L1/Energy/Forward'] = meter_data['emeters'][idx]['total'] / 1000
      self._dbusservice['/Ac/L1/Energy/Reverse'] = meter_data['emeters'][idx]['total_returned'] / 1000

      self._dbusservice['/Ac/Current'] = self._dbusservice['/Ac/L1/Current']
      self._dbusservice['/Ac/Power'] = self._dbusservice['/Ac/L1/Power']
      self._dbusservice['/Ac/Energy/Forward'] = self._dbusservice['/Ac/L1/Energy/Forward']
      self._dbusservice['/Ac/Energy/Reverse'] = self._dbusservice['/Ac/L1/Energy/Reverse']

      logging.debug("House Consumption (/Ac/Power): %s" % (self._dbusservice['/Ac/Power']))
      logging.debug("House Forward (/Ac/Energy/Forward): %s" % (self._dbusservice['/Ac/Energy/Forward']))
      logging.debug("House Reverse (/Ac/Energy/Reverse): %s" % (self._dbusservice['/Ac/Energy/Reverse']))
      logging.debug("---")

      index = self._dbusservice['/UpdateIndex'] + 1
      if index > 255:
          index = 0
      self._dbusservice['/UpdateIndex'] = index
      self._lastUpdate = time.time()
    except Exception as e:
      logging.critical('Error at %s', '_update', exc_info=e)
    return True

  def _handlechangedvalue(self, path, value):
    logging.debug("someone else updated %s to %s" % (path, value))
    return True


def main():
  logging.basicConfig(format='%(asctime)s,%(msecs)d %(name)s %(levelname)s %(message)s',
                      datefmt='%Y-%m-%d %H:%M:%S',
                      level=logging.INFO,
                      handlers=[
                          logging.FileHandler("%s/current.log" % (os.path.dirname(os.path.realpath(__file__)))),
                          logging.StreamHandler()
                      ])
  try:
      logging.info("Start")

      from dbus.mainloop.glib import DBusGMainLoop
      DBusGMainLoop(set_as_default=True)

      config = configparser.ConfigParser()
      config.read("%s/config.ini" % (os.path.dirname(os.path.realpath(__file__))))
      victron_role = config['DEFAULT'].get('VictronRole', 'pvinverter')
      servicename = f"com.victronenergy.{victron_role}"

      _kwh = lambda p, v: (str(round(v, 2)) + 'KWh')
      _a = lambda p, v: (str(round(v, 1)) + 'A')
      _w = lambda p, v: (str(round(v, 1)) + 'W')
      _v = lambda p, v: (str(round(v, 1)) + 'V')

      pvac_output = DbusShellyemService(
        servicename=servicename,
        paths={
          '/Ac/Energy/Forward': {'initial': 0, 'textformat': _kwh},
          '/Ac/Energy/Reverse': {'initial': 0, 'textformat': _kwh},
          '/Ac/Power': {'initial': 0, 'textformat': _w},
          '/Ac/Current': {'initial': 0, 'textformat': _a},
          '/Ac/Voltage': {'initial': 0, 'textformat': _v},
          '/Ac/L1/Voltage': {'initial': 0, 'textformat': _v},
          '/Ac/L1/Current': {'initial': 0, 'textformat': _a},
          '/Ac/L1/Power': {'initial': 0, 'textformat': _w},
          '/Ac/L1/Energy/Forward': {'initial': 0, 'textformat': _kwh},
          '/Ac/L1/Energy/Reverse': {'initial': 0, 'textformat': _kwh},
        })

      logging.info('Connected to dbus, and switching over to gobject.MainLoop() (= event based)')
      mainloop = gobject.MainLoop()
      mainloop.run()
  except Exception as e:
      logging.critical('Error at %s', 'main', exc_info=e)

if __name__ == "__main__":
  main()
