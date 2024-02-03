import RPi.GPIO as GPIO
import paho.mqtt.client as MQTT
import paho.mqtt.reasoncodes as reasoncodes
import paho.mqtt.packettypes as packettypes
import time
from datetime import datetime, timedelta
import os
import weakref
import sys
import json

os.system('clear')
print("Spa Controller: Script started")

# sudo apt install python3-paho-mqtt
# Enable 1 wire interface using sudo raspi-config
    
# This script is run at boot using systemd
# https://www.dexterindustries.com/howto/run-a-program-on-your-raspberry-pi-at-startup/

# About logging to journal
# run python in unbuffered mode (python -u scriptname)
# To follow messages: journalctl -u managespacontroller.service -f
# To stop the messages, set the debug parameter False in the config file

# To restart the script: sudo systemctl managespacontroller.service



#TODO: interrupt driven GPIOS!!!!
#https://roboticsbackend.com/raspberry-pi-gpio-interrupts-tutorial/



### Begin class definitions ###

class Gpio(object):
    instanceArr = [] # This create a list with all instances of this class
    
    def __init__(self, unique_id, config):
        # Add this instance to instanceArr
        self.__class__.instanceArr.append(weakref.proxy(self))
        
        # Add all attributes from config file
        for key, value in config.items():
            if debug: print(f"Initializing Gpio[{config['name']}][{key}] = {value}")
            setattr(self, key, value)
        
        # Add additional attributes
        self.unique_id = unique_id
        self._value = None

    @property
    def value(self):
        if self._value == None: 
            # No value available yet, get it!
            self.read()
        return self._value

    @value.setter
    def value(self,value):
        if self._value != value:
            if debug: print(f"Gpio[{self.name}] = {value}")
            if self.direction =='output':
                state = self.gpio_off if value == self.payload_off else self.gpio_on        
                GPIO.output(self.pin, state)
            
            self._value = value
            self.publish()

    def set_io_direction(self):
        def get_initial_state(self):
            return self.gpio_on if hasattr(self, 'initial_state') and self.initial_state == 'on' else self.gpio_off

        if self.direction == 'output':
            GPIO.setup(self.pin, GPIO.OUT)
        else:
            if hasattr(self, 'pull_up_down'):
                match self.pull_up_down:
                    case "up"  : pull_up_down = GPIO.PUD_UP
                    case "down": pull_up_down = GPIO.PUD_DOWN
                    case "off" : pull_up_down = GPIO.PUD_OFF
                    case _     : pull_up_down = GPIO.PUD_DOWN
            else: 
                pull_up_down = GPIO.PUD_DOWN
            GPIO.setup(self.pin, GPIO.IN, pull_up_down)
        
    def read(self):
        self.value = self.payload_on if GPIO.input(self.pin) == self.gpio_on else self.payload_off

    def write(self, state):
        if self.direction == 'output':
            if problem_detection.state == True:
                state = self.payload_off
            else:
                # Detect conflicts
                if hasattr(self, 'conflict'):
                    gpio_conflict = globals()[self.conflict]
                    if state == self.payload_on: gpio_conflict.value = gpio_conflict.payload_off
                    elif gpio_conflict.value != gpio_conflict.initial_state: gpio_conflict.value = gpio_conflict.initial_state
                pass

            state_pin = self.gpio_off if state == self.payload_off else self.gpio_on        
            GPIO.output(self.pin, state_pin)
        self._value = state
        self.publish()
    
    def publish(self):
        if debug: print(f"Gpio[{self.name}] = {self._value}")
        client.publish(self.state_topic, self._value)


class Sensor(object):
    instanceArr = [] # This create a list with all instances of this class
    
    def __init__(self, unique_id, config):
        # Add this instance to instanceArr
        self.__class__.instanceArr.append(weakref.proxy(self))
        
        # Add all attributes from config file
        for key, value in config.items():
            if debug: print(f"Initializing Sensor[{config['name']}][{key}] = {value}")
            setattr(self, key, value)
        
        # Add additional attributes
        self.unique_id = unique_id
        self._value = None

    @property
    def value(self):
        if self._value == None: 
            # No value available yet, get it!
            self.read()
        return self._value

    @value.setter
    def value(self,value):
        if self._value is None or self._value+0.1 < value or value < self._value-0.1: #TODO: gebruik digits voor afronding uit config file
            if debug: print(f"Sensor[{self.name}] = {value}")
            client.publish(self.state_topic, value)
            self._value = value
    
    def read(self):
        def read_w1sensor_file(device_file):
            f = open(device_file, 'r')
            lines = f.readlines()
            f.close()
            return lines

        def get_w1sensor_value(sensor):
            value = 0
            try:
                lines = read_w1sensor_file(sensor.filename)
                while lines[0].strip()[-3:] != 'YES':
                    time.sleep(0.2)
                    lines = read_w1sensor_file()
                equals_pos = lines[1].find('t=')
                if equals_pos != -1:
                    value = (lines[1][equals_pos+2:]).strip()
                    if value.isnumeric():
                        value = float(value)
                        if hasattr(sensor,'scale')        : value = value * sensor.scale
                        if hasattr(sensor,'offset')       : value += sensor.offset
                        if hasattr(sensor,'round_digits') : value = round(value, sensor.round_digits)
            
            except Exception as error:
                    if debug: print(f"Sensor[{sensor.name}]: {error}!")
                    value = 0
            finally:
                return value

        match self.sensor_type:
            case "w1sensor":
                self.value = get_w1sensor_value(sensor)
    
    def publish(self):
        if debug: print(f"Sensor[{self.name}] = {self._value}")
        client.publish(self.state_topic, self._value)


class Monitor(object):
    instanceArr = [] # This create a list with all instances of this class
    
    def __init__(self, unique_id, config):
        # Add this instance to instanceArr
        self.__class__.instanceArr.append(weakref.proxy(self))
        
        # Add all attributes from config file
        for key, value in config.items():
            if debug: print(f"Initializing Monitor[{config['name']}][{key}] = {value}")
            setattr(self, key, value)
        
        # Add additional attributes
        self.unique_id = unique_id
        self._value = None
        
    @property
    def value(self):
        if self._value == None: 
            # No value available yet, get it!
            self.read()
        return self._value
    
    @value.setter
    def value(self,value):
        if self._value != value:
            if debug: print(f"Monitor[{self.name}] = {value}")
            client.publish(self.state_topic, value)
            self._value = value
            pass

    def read(self):
        status = self.payload_off
        for key, value in self.monitor.items():
            check2perform = value.split(',')
            sensor2check  = globals()[check2perform[0].strip()]
            if len(check2perform) == 1:
                # Simple state check required
                if sensor2check.value == sensor2check.payload_on: status = self.payload_on
            else:
                # We need to check the value indicated
                value2check   = check2perform[1].strip()
                match value2check[:1]: #check first character
                    case '<':
                        if sensor2check.value <  int(value2check[1:]): status = self.payload_on
                    case '>':
                        if sensor2check.value >  int(value2check[1:]): status = self.payload_on
                    case '=':
                        if sensor2check.value == int(value2check[1:]): status = self.payload_on
                    case '!':
                        if sensor2check.value != int(value2check[1:]): status = self.payload_on
            
            if status == self.payload_on: break # At least one problem found, exit loop        
        
        self.value = status
    
    def publish(self):
        if debug: print(f"Monitor[{self.name}] = {self._value}")
        client.publish(self.state_topic, self._value)


class Problem(object):
    def __init__(self):
        self.state      = False
        self.last_state = False

    def check(self):
        new_state = False
        for monitor in Monitor.instanceArr:
            if monitor.value == monitor.payload_on: new_state = True
        self.last_state = self.state
        self.state = new_state

### End class definitions ###


### Begin MQTT functions ###

def connect_mqtt_broker(client, MQTT_SERVER, MQTT_PORT, MQTT_KEEPALIVE):
    if debug: print("\nStarting connection")
    client.connect(
        host        = MQTT_SERVER,
        port        = MQTT_PORT,
        keepalive   = MQTT_KEEPALIVE,
        clean_start = True
    )
    return


# Callback function when connection is established
def on_connect(client, userdata, flags, rc, other):
    if rc == 0:
        if debug: print("Connection success!")
        client.connected_flag=True
        # Send online message
        client.publish(config["mqtt"]["statustopic"], payload=config["mqtt"]["statusonline"], qos=config["mqtt"]["qos"], retain=True)
        # Subscribe to messages
        if debug: print("Subscribe to mqtt messages: " + config["mqtt"]["subscribe_topic"])
        client.subscribe(config["mqtt"]["subscribe_topic"])
        
        #publish_ha_autodiscovery(config, client)
    else:
        client.connected_flag=False
        if debug: print(f"Connection failed with code {rc}")
    return


# Callback function when connection is disconnected gracefully
def on_disconnect(client, userdata, flags, rc):
    connected_flag=False
    if debug: print(f"Disconnected gracefully with code {rc}")
    return

        
# Callback function when a message is sent
def on_publish(client,userdata,result):
    #if debug: print("-->MQTT message sent")
    pass
    return


# Callback function when a message is received
def on_message(client, userdata, msg):
    target  = (msg.topic).split("/")[1]
    value = (msg.payload).decode('UTF-8')
    qos     = msg.qos
    
    if debug: print(f"\nMessage received: {target=}, {value=}, {qos=}")
    globals()[target].write(value)
    return


def publish_ha_discovery_info(entry):
    if hasattr(entry, "config_topic"):
        # Build device message
        device_dict = {"device": config["mqtt"]["device"],} 
        
        # Build payload message
        payload_dict = {
            "device_class": entry.device_class,
            "name": entry.name,
            "state_topic": entry.state_topic,
            "unique_id": entry.unique_id
        }
        if hasattr(entry, "command_topic") : payload_dict.update({"command_topic": entry.command_topic})
        if hasattr(entry, "payload_off")   : payload_dict.update({"payload_off": entry.payload_off})
        if hasattr(entry, "payload_on")    : payload_dict.update({"payload_on": entry.payload_on})

        # Merge device_dict and payload_dict
        device_dict.update(payload_dict)

        # Convert to JSON
        payload_json = json.dumps(device_dict)
        
        # Publish discovery message to MQTT
        if debug: print("Publishing MQTT discovery message to " + entry.config_topic)
        if debug: print(payload_json)
        client.publish(entry.config_topic, payload=payload_json, qos=config["mqtt"]["qos"], retain=True)

### End MQTT functions

def str2bool(v):
  return v.lower() in ("yes", "true", "t", "1")



############
### MAIN ###
############

# Declare global variables
global debug
global config
global client

# Load configuration file  
with open(__file__ +".json", "r") as jsonfile:
    config = json.load(jsonfile)
    debug = str2bool(config["mqtt"]["debug"])
    if debug: print("Configuration read successful")

# Create MQTT Client instance
client = MQTT.Client(protocol=MQTT.MQTTv5)
client.username_pw_set(username=config['mqtt']['user'], password=config['mqtt']['password'])
client.connected_flag=False
client.will_set(config["mqtt"]["statustopic"],config["mqtt"]["statusoffline"],qos=config["mqtt"]["qos"],retain=True)

# Set the callback functions for the MQTT client
client.on_connect    = on_connect
client.on_publish    = on_publish
client.on_message    = on_message
client.on_disconnect = on_disconnect

# Connect to the MQTT broker
connect_mqtt_broker(client, config['mqtt']['server'], config['mqtt']['port'], config['mqtt']['keepalive'])

# Start the MQTT loop to receive messages
client.loop_start()

# Wait for the MQTT connection to be established
while not client.connected_flag:
    time.sleep(0.5)

# Initialize basic sensor and gpio settings
# Enable 1 wire temperature sensors
os.system('modprobe w1-gpio')
os.system('modprobe w1-therm')
# Generic GPIO settings
GPIO.setmode(GPIO.BOARD)
GPIO.setwarnings(False)

# Create object instances
for gpio    in config["gpios"]     : exec(f"globals()[gpio]    = Gpio(gpio, config['gpios'][gpio])")
for sensor  in config["sensors"]   : exec(f"globals()[sensor]  = Sensor(sensor, config['sensors'][sensor])")
for monitor in config["monitors"]  : exec(f"globals()[monitor] = Monitor(monitor, config['monitors'][monitor])")
problem_detection = Problem()

#Initialize gpio input/output direction
for gpio    in Gpio.instanceArr    : gpio.set_io_direction()

# Publish HA autodiscovery information
for gpio    in Gpio.instanceArr    : publish_ha_discovery_info(gpio)
for sensor  in Sensor.instanceArr  : publish_ha_discovery_info(sensor)
for monitor in Monitor.instanceArr : publish_ha_discovery_info(monitor)

# Get monitor readings. This will automatically update underlying sensors
for monitor in Monitor.instanceArr: monitor.read()

# Check for problems
problem_detection.check()

# Set gpio initial_states (gpio_off if problem detected)
for gpio in Gpio.instanceArr: 
    if gpio.direction == 'output': gpio.write(gpio.initial_state)

# Set the republished date/time
republished_on = datetime.now()

try:
    while True:
        # Get values, publish if value has changed
        for gpio    in Gpio.instanceArr    : gpio.read()
        for sensor  in Sensor.instanceArr  : sensor.read()
        for monitor in Monitor.instanceArr : monitor.read()
        
        # Check problem status and act accordingly
        problem_detection.check()
        if problem_detection.state != problem_detection.last_state:
            if problem_detection.state == True:
                # Problem is detected -> Switch all gpios off
                for gpio in Gpio.instanceArr: gpio.write(gpio.payload_off)
            else:
                # Problem is solved -> Swith all gpios to initial_state
                for gpio in Gpio.instanceArr: 
                    if gpio.direction == 'output': gpio.write(gpio.initial_state)
        
        # Republish all states/values if time has elapsed
        if datetime.now() > republished_on + timedelta(seconds=config['mqtt']['republish_sec']):
            republished_on = datetime.now()
            
            # Timer has elapsed. Republish HA autodiscovery messages
            if debug: print("\nTimer has elapsed. Republishing all HA autodiscovery messages")
            for gpio    in Gpio.instanceArr    : publish_ha_discovery_info(gpio)
            for sensor  in Sensor.instanceArr  : publish_ha_discovery_info(sensor)
            for monitor in Monitor.instanceArr : publish_ha_discovery_info(monitor)

            # Timer has elapsed. Republish all states/values
            if debug: print("\nTimer has elapsed. Republishing all states")
            for gpio    in Gpio.instanceArr    : gpio.publish()
            for sensor  in Sensor.instanceArr  : sensor.publish()
            for monitor in Monitor.instanceArr : monitor.publish()
            # Republish HA autodiscovery messages
            
        #if debug: print(f"Going to sleep for {config['mqtt']['sleep']} seconds...")
        time.sleep(config['mqtt']['sleep'])

except KeyboardInterrupt:
    if debug: print("\nSpa Controller: Script halted")

#except:  
#    # this catches ALL other exceptions including errors.  
#    # You won't get any error messages for debugging  
#    # so only use it once your code is working  
#    if debug: print("Other error or exception occurred!")
    
finally:
    # Revert to initial gpio states before quitting
    for gpio in Gpio.instanceArr: 
        if gpio.direction == 'output': gpio.write(gpio.initial_state)
    
    # Stop the MQTT loop and disconnect from the MQTT Broker
    if debug: print("\nDisconnect from broker")
    client.loop_stop()
    #client.disconnect()
    client.disconnect(reasoncodes.ReasonCodes(packettypes.PacketTypes.DISCONNECT, "Disconnect", 4))
