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
from random import randrange
#from threading import Thread, Event
#from LCDI2C_backpack import LCDI2C_backpack
import traceback
import liquidcrystal_i2c # https://github.com/pl31/python-liquidcrystal_i2c/tree/master

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

# sudo apt install python3-luma.lcd
# https://github.com/rm-hull/luma.lcd

#TODO: interrupt driven GPIOS!!!!
#https://roboticsbackend.com/raspberry-pi-gpio-interrupts-tutorial/



### Begin class definitions ###
#class SpaEntity(object):

class Gpio(object):
    def __init__(self, unique_id, config):
        # Add all attributes from config file
        for key, value in config.items():
            if debug: print(f"Initializing Gpio[{config['name']}][{key}] = {value}")
            setattr(self, key, value)
        
        # Add additional attributes
        self.unique_id = unique_id
        self._value = None
        self.changed_on = datetime.now()
        self.actor = "automation"

    @property
    def value(self):
        if self._value == None: 
            # No value available yet, get it!
            self.read()
        return self._value

    @value.setter
    def value(self,value):
        if self._value != value:
            if debug: print(f"Gpio[{self.name}] --> {value}")
            if self.direction =='output':
                state = self.gpio_off if value == self.payload_off else self.gpio_on        
                GPIO.output(self.pin, state)
            
            self._value = value
            self.changed_on = datetime.now()
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
                    gpio_conflict = mySpa[self.conflict]
                    if state == self.payload_on: gpio_conflict.value = gpio_conflict.payload_off
                    elif gpio_conflict.value != gpio_conflict.initial_state: gpio_conflict.value = gpio_conflict.initial_state

            state_pin = self.gpio_off if state == self.payload_off else self.gpio_on        
            GPIO.output(self.pin, state_pin)
        self._value = state
        self.changed_on = datetime.now()
        self.publish()

        
        if hasattr(self, "actions_on") and state == self.payload_on:
            for key, value in self.actions_on.items():
                mySpa[key].write(value)
        elif hasattr(self, "actions_off") and state == self.payload_off:
            for key, value in self.actions_off.items():
                mySpa[key].write(value)
        return
    
    def publish(self):
        if hasattr(self, "state_topic"):
            if debug: print(f"Gpio[{self.name}] = {self._value}")
            client.publish(self.state_topic, self._value)
        return

    def is_active(self):
        if self.value == self.payload_on:
            return True
        else:
            return False
        
    def schedule(self):
        if hasattr(self, 'schedule_on_secs') and not(mySpa['spa_operation'].is_active()) and not(mySpa['spa_status'].is_active()):
            seconds_on = self.schedule_on_secs
            seconds_off = self.schedule_off_secs + randrange(10) # Add some random time to prevent all pumps from switching on at the same time
            if not(self.is_active()) and datetime.now() > self.changed_on + timedelta(seconds=seconds_off):
                # Start ON schedule
                if debug: print(f"Gpio[{self.name}] *** Starting ON schedule for {seconds_on} seconds")
                self.actor = "automation"
                self.write(self.payload_on)
            elif self.is_active() and datetime.now() > self.changed_on + timedelta(seconds=seconds_on):
                # Start OFF schedule
                if debug: print(f"Gpio[{self.name}] *** Starting OFF schedule for {seconds_off} seconds")
                self.actor = "automation"
                self.write(self.payload_off)

    def status_message(self):
        if self.value == self.payload_on and hasattr(self,'short_name'):
            return str(self.short_name)
        else:
            return None


class Sensor(object):
    def __init__(self, unique_id, config):
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
    def __init__(self, unique_id, config):
        # Add all attributes from config file
        for key, value in config.items():
            if debug: print(f"Initializing Monitor[{config['name']}][{key}] = {value}")
            setattr(self, key, value)
        
        # Add additional attributes
        self.unique_id = unique_id
        self._value = None
        self.changed_on = None
        
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
            self.changed_on = datetime.now()

    def read(self):
        status = self.payload_off
        for key, value in self.monitor.items():
            monitorarr = value.split(',')
            check2perform = monitorarr[0].strip()
            sensor2check  = monitorarr[1].strip()
            value2check = None if len(monitorarr)<3 else monitorarr[2].strip()
                        
            match check2perform:
                case "state_on":
                    if mySpa[sensor2check].is_active(): status = self.payload_on
                case "state_off":
                    if not(mySpa[sensor2check].is_active()): status = self.payload_on
                case "state_on_ignore_automation":
                    if mySpa[sensor2check].is_active() and mySpa[sensor2check].actor != 'automation': status = self.payload_on
                case "state_off_ignore_automation":
                    if not(mySpa[sensor2check].is_active()) and mySpa[sensor2check].actor != 'automation': status = self.payload_on
                case "value_greater":
                    if mySpa[sensor2check].value >  int(value2check): status = self.payload_on
                case "value_less":
                    if mySpa[sensor2check].value <  int(value2check): status = self.payload_on
                case "value_equal":
                    if mySpa[sensor2check].value == int(value2check): status = self.payload_on
                case "value_not_equal":
                    if mySpa[sensor2check].value != int(value2check): status = self.payload_on
                case "time_on":
                    if mySpa[sensor2check].is_active() and datetime.now() > mySpa[sensor2check].changed_on + timedelta(seconds=int(value2check)):
                        status = self.payload_on
                        if sensor2check.name == 'Spa Operation':
                            pass
                case "time_off":
                    if not(mySpa[sensor2check].is_active()) and datetime.now() > mySpa[sensor2check].changed_on + timedelta(seconds=int(value2check)):
                        status = self.payload_on
                case "_":
                    if debug: print(f"ERROR! Unknown monitor command ({check2perform}) defined for {mySpa[sensor2check].name}")
            
            if status == self.payload_on: break # At least one check is positive, exit loop        
        
        self.value = status
    
    def publish(self):
        if debug: print(f"Monitor[{self.name}] = {self._value}")
        client.publish(self.state_topic, self._value)

    def is_active(self):
        if self.value == self.payload_on:
            return True
        else:
            return False


class Problem(object):
    def __init__(self):
        self.state      = False
        self.last_state = False
        self.problem    = None

    def check(self):
        new_state = False
        problem = "WARNING:"
        for monitor in get_list_by_type(mySpa, Monitor):
            if monitor.is_active() and monitor.device_class == 'problem': 
                new_state = True
                myLCD.on()
                if hasattr(monitor,'warning'): problem += ' ' + monitor.warning
                    
        self.last_state = self.state
        self.state = new_state
        if self.state:
            if myLCD._activity_dot != ' ':    
                GPIO.output(mySpa['spa_buzzer'].pin, mySpa['spa_buzzer'].gpio_on)
            else:
                GPIO.output(mySpa['spa_buzzer'].pin, mySpa['spa_buzzer'].gpio_off)
            myLCD._statusmessage = problem
            myLCD.printstatusmessage()
        else:
            GPIO.output(mySpa['spa_buzzer'].pin, mySpa['spa_buzzer'].gpio_off)
            if self.state != self.last_state: myLCD.clearline(0)


class myLCDI2C(liquidcrystal_i2c.LiquidCrystal_I2C):
    def __init__(self, addr, port, numlines=4, numcolumns=20):
        print("ChildB init'ed")
        super().__init__(addr, port, numlines)
        self._activity_dot = ' '
        self._numcolumns = numcolumns # width of the display
        self._statusmessage = None

    # overloaded original function
        # add spaces to clear the rest of the line
        # display off if 
    def printline(self, linenr, value):
        if value is not None:
            _spaisactive = False
            _spaisproblem = False
            try:    _spaisactive = mySpa['spa_operation'].is_active()
            except: pass
            try:    _spaisproblem = mySpa['spa_status'].is_active()
            except:
                pass
            if _spaisactive or _spaisproblem:
                self.on()
                self.setCursor(0, linenr)
                spaces = ' ' * (self._numcolumns - len(value))
                self.printstr(value + spaces)
            else:
                self.off()
        

    # split the string over multiple lines starting at linenr
    def printmultiline(self, linenr, value):
        if value is not None:
            line = linenr
            # Clear all lines starting at the indicated linenr
            for i in range(self._numlines - linenr):
                self.clearline(i)
            # split the messaage and display on multiple lines
            while len(value) > 0:
                slice = value[0:self._numcolumns]
                value = value[self._numcolumns:]
                self.clearline(line)
                self.printline(line, slice)
                line += 1

    
    def clearline(self, linenr):
        # fills the indicated line with spaces
        self.printline(linenr,' '*self._numcolumns)   

    def printlinejustified(self, linenr, valueleft, valueright):
        if valueleft is not None and valueright is not None:
            spaces = ' ' * (self._numcolumns - len(valueleft) - len(valueright))
            message = valueleft + spaces + valueright
            self.printline(linenr, message)

    def off(self):
        if self._displaycontrol == self._LCD_DISPLAYON: 
            self.noDisplay()
        if self._backlightval == self._LCD_BACKLIGHT: 
            self.noBacklight()

    def on(self):
        if self._backlightval == self._LCD_NOBACKLIGHT: 
            self.backlight()
        if self._displaycontrol == self._LCD_DISPLAYOFF: 
            self.display()

    def clearstatusmessage(self):
        self._statusmessage = None

    def buildstatusmessage(self, value):
        if value is not None:
            self._statusmessage = value if self._statusmessage is None else self._statusmessage + ' ' + value
    
    def toggle_activity_dot(self):
        self._activity_dot = "*" if self._activity_dot == " " else " "

    def printstatusmessage(self):
        if self._statusmessage is not None:
            self.toggle_activity_dot()
            self.printlinejustified(0, self._statusmessage, self._activity_dot)
        

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
    mySpa[target].actor = "user"
    mySpa[target].write(value)
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

# Initialize LCD display
myLCD = myLCDI2C(addr=0x27, port=1, numlines=4)
myLCD.printline(0,"Program started!")

# Create MQTT Client instance
client = MQTT.Client(protocol=MQTT.MQTTv5)
client.username_pw_set(username=config['mqtt']['user'], password=config['mqtt']['password'])
client.will_set(config["mqtt"]["statustopic"],config["mqtt"]["statusoffline"],qos=config["mqtt"]["qos"],retain=True)
client.connected_flag=False

# Set the callback functions for the MQTT client
client.on_connect    = on_connect
client.on_publish    = on_publish
client.on_message    = on_message
client.on_disconnect = on_disconnect

# Connect to the MQTT broker
connect_mqtt_broker(client, 
                    config['mqtt']['server'], 
                    config['mqtt']['port'], 
                    config['mqtt']['keepalive']
                    )

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
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

# class Spa(object):
#     def __init__(self):
#         self.name = None
#         self.type = None
# mySpa = Spa()
# mySpa.name = "Test"
# mySpa.type = "gpio"

mySpa = {}

# Create object instances
for gpio    in config["gpios"]     : mySpa[gpio] = Gpio(gpio, config['gpios'][gpio])
for sensor  in config["sensors"]   : mySpa[sensor] = Sensor(sensor, config['sensors'][sensor])
for monitor in config["monitors"]  : mySpa[monitor] = Monitor(monitor, config['monitors'][monitor])

def get_list_by_type(sensorList, sensorType):
    myReturnList = []
    for item in sensorList:
        if type(sensorList[item]) == sensorType: myReturnList.append(sensorList[item])
    return myReturnList

# Initialize gpio input/output direction
for gpio in get_list_by_type(mySpa, Gpio): gpio.set_io_direction()

# Get initial monitor readings. This will automatically initialize underlying sensors values
for monitor in get_list_by_type(mySpa, Monitor): monitor.read()

# Publish HA autodiscovery information 
for item in mySpa.items(): publish_ha_discovery_info(item)
    
# Initialize problem_detection
problem_detection = Problem()

# Check for initial problems before setting gpio initial states
problem_detection.check()

# Set gpio initial_states (gpio_off if problem detected)
for gpio in get_list_by_type(mySpa, Gpio): 
    if gpio.direction == 'output': 
        gpio.actor = "automation"
        gpio.write(gpio.initial_state)

# Set the republished date/time
republished_on = datetime.now()

try:
    while True:
        myLCD.clearstatusmessage()
        for gpio in get_list_by_type(mySpa, Gpio): 
            gpio.read()
            if not problem_detection.state: gpio.schedule()
            myLCD.buildstatusmessage(gpio.status_message())
        myLCD.printstatusmessage()
        
        
        i=1 # Temperature messages on line 1 - 3
        for sensor  in get_list_by_type(mySpa, Sensor): 
            sensor.read()
            if sensor.device_class == 'temperature':
                myLCD.printlinejustified(linenr     = i,
                                            valueleft  = sensor.name + ':',
                                            valueright = str(sensor.value)
                                            )
                i += 1
        
        for monitor in get_list_by_type(mySpa, Monitor): monitor.read()
        
        
        # Check problem status and act accordingly
        problem_detection.check()
        if problem_detection.state != problem_detection.last_state:
            if problem_detection.state == True:
                # Problem is detected -> Switch all gpios off
                for gpio in get_list_by_type(mySpa, Gpio): 
                    gpio.actor = "automation"
                    gpio.write(gpio.payload_off)
            else:
                # Problem is solved -> Switch all gpios to initial_state
                for gpio in get_list_by_type(mySpa, Gpio): 
                    if gpio.direction == 'output': 
                        gpio.actor = "automation"
                        gpio.write(gpio.initial_state)
                
        
        # Republish all states/values if time has elapsed
        if datetime.now() > republished_on + timedelta(seconds=config['mqtt']['republish_sec']):
            republished_on = datetime.now()
            
            # Timer has elapsed. Republish HA autodiscovery messages
            if debug: print("\nTimer has elapsed. Republishing all HA autodiscovery messages")
            for gpio    in get_list_by_type(mySpa, Gpio)    : publish_ha_discovery_info(gpio)
            for sensor  in get_list_by_type(mySpa, Sensor)  : publish_ha_discovery_info(sensor)
            for monitor in get_list_by_type(mySpa, Monitor) : publish_ha_discovery_info(monitor)

            # Timer has elapsed. Republish all states/values
            if debug: print("\nTimer has elapsed. Republishing all states")
            for gpio    in get_list_by_type(mySpa, Gpio)    : gpio.publish()
            for sensor  in get_list_by_type(mySpa, Sensor)  : sensor.publish()
            for monitor in get_list_by_type(mySpa, Monitor) : monitor.publish()
            # Republish HA autodiscovery messages
            
        #if debug: print(f"Going to sleep for {config['mqtt']['sleep']} seconds...")
        time.sleep(config['mqtt']['sleep'])

except KeyboardInterrupt:
    if debug: print("\nSpa Controller: Script halted")
    mySpa['spa_status'].value = mySpa['spa_status'].payload_on # This will switch on display
    myLCD.clear()
    myLCD.printline(0, "Program stopped!")
    myLCD.printline(1, "CTRL-C pressed.")

except Exception as e:  
    # this catches ALL other exceptions including errors.  
    # You won't get any error messages for debugging  
    # so only use it once your code is working  
    if debug: print("Other error or exception occurred!")
    exception_type, exception_object, exception_traceback = sys.exc_info()
    #print(e.args[0])
    #print(exception_type)
    #print(exception_object)
    #print(exception_traceback)
    #message = "Line{line}:{error}".format(line=exception_traceback.tb_lineno,error=type(e).__name__)
    message = "Line{line}:{errortype}({error})".format(line=exception_traceback.tb_lineno,errortype=type(e).__name__,error=e)
    mySpa['spa_status'].value = mySpa['spa_status'].payload_on # This will switch on display
    myLCD.clear()
    myLCD.printmultiline(0, message)
        
finally:
    # Revert to initial gpio states before quitting
    for gpio in get_list_by_type(mySpa, Gpio):
        if gpio.direction == 'output': 
            gpio.actor = "automation"
            gpio.write(gpio.initial_state)
    
    # Stop the MQTT loop and disconnect from the MQTT Broker
    if debug: print("\nDisconnect from broker")
    client.loop_stop()
    #client.disconnect()
    client.disconnect(reasoncodes.ReasonCodes(packettypes.PacketTypes.DISCONNECT, "Disconnect", 4))
