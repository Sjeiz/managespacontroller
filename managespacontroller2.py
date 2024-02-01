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
    
# This script is run at boot using systemd
# https://www.dexterindustries.com/howto/run-a-program-on-your-raspberry-pi-at-startup/

# About logging to journal
# run python in unbuffered mode (python -u scriptname)
# To follow messages: journalctl -u managespacontroller.service -f
# To stop the messages, set the debug parameter False in the config file

# To restart the script: sudo systemctl managespacontroller.service



#TODO: interrupt driven GPIOS!!!!
#https://roboticsbackend.com/raspberry-pi-gpio-interrupts-tutorial/



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
        
        publish_ha_autodiscovery(config, client)
    else:
        client.connected_flag=False
        if debug: print(f"Connection failed with code {rc}")
    return


# Callback function when connection is disconnected gracefully
def on_disconnect(client, userdata, flags, rc):
    connected_flag=False
    if debug: print(f"Disconnected gracefully with code {rc}")

        
# Callback function when a message is sent
def on_publish(client,userdata,result):
    if debug: print("-->MQTT message sent")
    pass
    return


# Callback function when a message is received
def on_message(client, userdata, msg):
    target  = (msg.topic).split("/")[1]
    command = (msg.payload).decode('UTF-8')
    qos     = msg.qos
    
    if debug: print(f"\nMessage received: {target=}, {command=}, {qos=}")
    process_mqtt_message(target, command)
       
   
#Function to sent autodiscovery data to homeassistant
def publish_ha_autodiscovery(config, client):
    if debug: print("\nSending HA autodiscovery data")
        
    def publish_devices(device_type):
        devices = config[device_type]
        for device in devices:
            device_dict = {
                "device": config["mqtt"]["device"],
            }
            payload_dict = {
                "device_class": devices[device]["device_class"],
                "name": devices[device]["name"],
                "state_topic": devices[device]["state_topic"],
                "unique_id": device
            }
            if "command_topic" in devices[device]: payload_dict.update({"command_topic": devices[device]["command_topic"]})
            if "payload_off"   in devices[device]: payload_dict.update({"payload_off":   devices[device]["payload_off"]})
            if "payload_on"    in devices[device]: payload_dict.update({"payload_on":    devices[device]["payload_on"]})
            
            #Add payload_dict to device_dict
            device_dict.update(payload_dict)
            
            # Convert to JSON
            payload_json = json.dumps(device_dict)
            if debug: print("Publishing MQTT message to " + devices[device]["config_topic"])
            if debug: print(payload_json)
            #time.sleep(0.5)
            client.publish(devices[device]["config_topic"], payload=payload_json, qos=config["mqtt"]["qos"], retain=True)    
        
    publish_devices("gpios")
    publish_devices("sensors")
    return
    

def get_sensor_or_gpio_value(sensor):
    def read_w1sensor_file(device_file):
        f = open(device_file, 'r')
        lines = f.readlines()
        f.close()
        return lines

    def get_w1sensor_value(sensor):
        lines = read_w1sensor_file(get_json_value(sensor,"filename"))
        w1sensor_value=""
        while lines[0].strip()[-3:] != 'YES':
            time.sleep(0.2)
            lines = read_temp_raw()
        equals_pos = lines[1].find('t=')
        if equals_pos != -1:
            w1sensor_value = (lines[1][equals_pos+2:]).strip()
            if w1sensor_value.isnumeric():
                w1sensor_value = float(w1sensor_value)
                scale = get_json_value(sensor,"scale")
                round_digits = get_json_value(sensor,"round_digits")
                offset = get_json_value(sensor,"offset")
                if scale is not None: w1sensor_value = w1sensor_value * scale
                if offset is not None: w1sensor_value+=offset
                if round_digits is not None or round_digits==0:
                    if round_digits == 0: round_digits = None
                    w1sensor_value = round(w1sensor_value, round_digits) 

            return w1sensor_value
        return None
    
    # Determine sensor or gpio
    if sensor in config["sensors"]:
        sensor=config["sensors"][sensor]
        # Determine sensor type and read value
        sensor_type = sensor["sensor_type"]
        sensor_value = None
        match sensor_type:
            case "w1sensor":
                # Get the sensor value
                try:
                    sensor_value = get_w1sensor_value(sensor)
                except Exception as error:
                    if debug: print(f"ERROR: Sensor {sensor['name']} not found!")
                    if debug: print(error)
                    sensor_value = 0

            case "monitor":
                # Process sensor which will monitor other sensor values
                monitor = sensor["monitor"]
                for sensortocheck in monitor:
                    valuetocheck  = monitor[sensortocheck]
                    if valuetocheck.startswith('>') or valuetocheck.startswith('<'):
                        # We have to do a numeric treshold test
                        numvaluetocheck=int(valuetocheck[1:]) # Remove first character
                        #Get indicated sensor or gpio value
                        if sensortocheck in config["sensors"]:
                            # get current sensor value
                            valuechecked = my_sensorvalues[sensortocheck]
                        else:
                            # Get gpio value TODO
                            pass
                        if valuechecked is not None:
                            if (
                                    (valuetocheck.startswith('>') and valuechecked > numvaluetocheck)
                                or
                                    (valuetocheck.startswith('<') and valuechecked < numvaluetocheck)
                                ):
                                    # Sensor breached treshold!
                                    sensor_value = sensor["payload_on"]
                                    # Do not process other monitors
                                    break
                            else:
                                sensor_value = sensor["payload_off"]
                    else:
                        # We have to do check string status
                        #Get indicated sensor value
                        valuechecked = get_sensor_or_gpio_value(sensortocheck)
                        if valuechecked == valuetocheck:
                            # Sensor breached status check!
                            sensor_value = sensor["payload_on"]
                            # Do not process other monitors
                            break
                        else:
                            sensor_value = sensor["payload_off"]
                            
            case _:
                if debug: print("ERROR! Undefined sensor type!)")
                
        if debug: print("Checking " + sensor["name"] + ": " + str(sensor_value))
        
    else:
        gpio      = config["gpios"][sensor]
        state_on  = gpio["payload_on"]
        state_off = gpio["payload_off"]
        sensor_value = state_off if GPIO.input(gpio["pin"]) == gpio["gpio_off"] else state_on
        if debug: print("Checking " + gpio["name"] + ": " + str(sensor_value) + "(" + str(GPIO.input(gpio["pin"])) + ")")
    
    return sensor_value

# Function to read and publish sensors
def process_sensors(publish=True,republish=False):
    if debug: print("\nProcessing sensors, Publishing=" + str(publish))
    
    # Process all sensors
    sensors = config["sensors"]
    for sensor in sensors:
        # Get sensor value
        sensor_new_value = get_sensor_or_gpio_value(sensor)
        sensor_current_value = my_sensorvalues[sensor]
                
        if sensor == "spa_status" and sensor_current_value is not None:
            if sensor_new_value != sensor_current_value:
                if debug: print(f">>>>>>>> Spa status changed: {sensor_current_value} -> {sensor_new_value}")
                if sensor_new_value == sensors[sensor]["payload_off"]:
                    # Problem fixed. Set initial states
                    set_gpio_initial_states()
                else:
                    # Problem detected! Switch all gpios off.
                    set_gpio_initial_states(alloff=True)
        
        # Send MQTT state message and update my_sensorvalues
        if (publish and my_sensorvalues[sensor] != sensor_new_value) or republish == True:
            client.publish(sensors[sensor]["state_topic"], payload=sensor_new_value, qos=config["mqtt"]["qos"], retain=False)
            my_sensorvalues.update({sensor:sensor_new_value})
           
    # Get all gpio states
    gpios = config["gpios"]
    for gpio in gpios:
        # Get gpio state
        gpio_new_state = get_sensor_or_gpio_value(gpio)
        
        # Send MQTT state message and update my_sensorvalues
        if (publish and my_sensorvalues[gpio] != gpio_new_state) or republish == True: 
            client.publish(gpios[gpio]["state_topic"], payload=gpio_new_state, qos=config["mqtt"]["qos"], retain=False)
            my_sensorvalues.update({gpio:gpio_new_state})

    return

def get_json_value(json, entry):
    if entry in json:
        value = json[entry]
    else:
        value = None
    return value
    

# Function to initialize RPi.GPIO ports
def initialize_gpios():
    if debug: print("\nInitialize GPIO ports")
    
    # Extract gpio configuration
    gpios = config['gpios']
    
    # Generic GPIO settings
    GPIO.setmode(GPIO.BOARD)
    GPIO.setwarnings(False)
    
    # Enable 1 wire temperature sensors
    os.system('modprobe w1-gpio')
    os.system('modprobe w1-therm')
    
    # Setup GPIO ports
    for gpio in gpios:
        message = "Initialized: " + gpio + ", pin=" + str(gpios[gpio]["pin"]) + ", direction=" + gpios[gpio]["direction"]
        if gpios[gpio]["direction"] == 'output':
            #Determine initial_state
            initial_state = gpios[gpio]["initial_state"]
            payload_on    = gpios[gpio]["payload_on"]
            gpio_on       = gpios[gpio]["gpio_on"]
            gpio_off      = gpios[gpio]["gpio_off"]
            gpio_state    = gpio_on if initial_state == payload_on else gpio_off
            #TODO: Hoe kom ik hier achter de spa status??
            #Kip/ei probleem. om sensors uit te lezen moeten eerst de gpios uitgelezen worden...
            #spa_status    = sensors["xxx"]
            message += ", state=" + str(gpios[gpio]["initial_state"])
            GPIO.setup(gpios[gpio]["pin"], GPIO.OUT)
            GPIO.output(gpios[gpio]["pin"],gpio_state)
        else:
            message += ", pull_up_down=" + str(gpios[gpio]["pull_up_down"])
            GPIO.setup(gpios[gpio]["pin"], GPIO.IN, pull_up_down=gpios[gpio]["pull_up_down"])
        if debug: print(message)
    
    return


# Function to receive and process incoming MQTT messages
def process_mqtt_message(target, command):
    if debug: print(f"\nProcessing incoming MQTT message: {target}, {command}")
    
    def prevent_conflicts(target, command):
        # Prevent conflicts (gpios which should not be on together)
        # Command=on  -> Switch the conflicting gpio off if it is on
        # Command=off -> Switch the conflicting gpio to initial_state
        if "conflict" in gpios[target]:
            # Get the conflicting gpio
            conflict_gpio = gpios[target]["conflict"]
            if command == "on":
                if my_sensorvalues[conflict_gpio] == gpios[conflict_gpio]["payload_on"]:
                    #Set conflicting gpio to off
                    if debug: print(f"Switching off confliction gpio={conflict_gpio}")
                    set_and_publish_gpio_state(conflict_gpio, "off")
            else:
                #Set the conflicting gpio to the inital_state (if exists)
                if "initial_state" in gpios[conflict_gpio]:
                    conflict_gpio_initial_state = gpios[conflict_gpio]["initial_state"]
                    if my_sensorvalues[conflict_gpio] != conflict_gpio_initial_state:
                        if debug: print(f"Switching confliction gpio to initial state: {conflict_gpio}={conflict_gpio_initial_state}")
                        set_and_publish_gpio_state(conflict_gpio, conflict_gpio_initial_state)
        return
    
    gpios = config["gpios"]
    
    prevent_conflicts(target, command)
   
    # execute the command against the target
    set_and_publish_gpio_state(target, command)
    
    return    


# Set gpio states
def set_gpio_initial_states(alloff=False):
    if debug: print(f"\nSet GPIO initial states. spa_status={my_sensorvalues['spa_status']}, alloff={str(alloff)}")
    gpios   = config['gpios']
    sensors = config['sensors']
    spa_status    = my_sensorvalues['spa_status']
    spa_status_ok = sensors['spa_status']['payload_off']

    # Loop through all gpios marked for output
    for gpio in gpios:
        if gpios[gpio]["direction"] == "output":
            if spa_status == spa_status_ok and alloff == False:
                # spa_status = ok. Use the initial state
                command = gpios[gpio]["initial_state"]
            else:
                # spa_status is not ok or alloff = True. Switch gpio off
                command = gpios[gpio]["payload_off"]
            
            set_and_publish_gpio_state(gpio, command)
    
    return    


def set_and_publish_gpio_state(target, command):
    gpio = config["gpios"][target]
    spa_status = my_sensorvalues["spa_status"]
    spa_status_problem = config["sensors"]["spa_status"]["payload_on"]
    
    # Change 'on' command to 'off' if spa_status = problem
    if spa_status == spa_status_problem:
        command = gpio["payload_off"]

    # Set the target GPIO to the desired state
    gpio_pin   = gpio["pin"]
    gpio_state = gpio["gpio_on"] if command == "on" else gpio["gpio_off"]
    if debug: print(f"Setting GPIO: name={target}, state={gpio_state} ({command})")
    exec(f"GPIO.output({gpio_pin}, {gpio_state})")
    
    # Read the value of the target GPIO and translate it to state
    gpio_value = eval(f"GPIO.input({gpio_pin})")
    gpio_state = gpio["payload_on"] if gpio_value == gpio["gpio_on"] else gpio["payload_off"]
    
    # Publish target GPIO state
    my_sensorvalues.update({target:gpio_state})
    client.publish(gpio["state_topic"], gpio_state, qos=config["mqtt"]["qos"], retain=False)
    
    return


def str2bool(v):
  return v.lower() in ("yes", "true", "t", "1")


class Gpio(object):
    instanceArr = []
    def __init__(self, unique_id, config):
        # Welk comment hiero?
        self.__class__.instanceArr.append(weakref.proxy(self))
        # Add all attributes from config file
        setattr(self, "unique_id", unique_id)
        for key, value in config.items():
            if debug: print(f"Initializing Gpio[{config['name']}][{key}] = {value}")
            setattr(self, key, value)
        # Add value attributes
        setattr(self, 'value', 0)
        setattr(self, 'oldvalue', 0)

    def gpio_init(self):
        def get_initial_state(self):
            return self.gpio_on if hasattr(self, 'initial_state') and self.initial_state == 'on' else self.gpio_off

        if self.direction == 'output':
            GPIO.setup(self.pin, GPIO.OUT)
            self.switch(self.initial_state)
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
    
    def switch(self,state):
        # # Check monitors and switch off if one or more monitors have detected a problem (on)
        # for monitor in Monitor.instanceArr:
        #     if monitor.value == monitor.payload_on:
        #         print(f"Problem detected with {monitor.name}! Switching {self.name} off")
        #         GPIO.output(self.pin, self.gpio_off)
        #         exit
        #     else:
        #         #TODO: klopt nog niet
        #         GPIO.output(self.pin, self.gpio_on)
        pass

    def publish_ha_discovery_info(self):
        if hasattr(self, "config_topic"):
            # Build device message
            device_dict = {"device": config["mqtt"]["device"],} 
            
            # Build payload message
            payload_dict = {
                "device_class": self.device_class,
                "name": self.name,
                "state_topic": self.state_topic,
                "unique_id": self.unique_id
            }
            if hasattr(self, "command_topic"): payload_dict.update({"command_topic": self.command_topic})
            if hasattr(self, "payload_off"): payload_dict.update({"payload_off": self.payload_off})
            if hasattr(self, "payload_on"): payload_dict.update({"payload_on": self.payload_on})

            # Merge device_dict and payload_dict
            device_dict.update(payload_dict)

            # Convert to JSON
            payload_json = json.dumps(device_dict)
            
            # Publish discovery message to MQTT
            if debug: print("Publishing MQTT message to " + self.config_topic)
            if debug: print(payload_json)
            #time.sleep(0.5)
            client.publish(self.config_topic, payload=payload_json, qos=config["mqtt"]["qos"], retain=True)


class Sensor(object):
    instanceArr = []
    def __init__(self, unique_id, config):
        # Welk comment hiero?
        self.__class__.instanceArr.append(weakref.proxy(self))
        # Add all attributes from config file
        setattr(self, "unique_id", unique_id)
        for key, value in config.items():
            if debug: print(f"Initializing Sensor[{config['name']}][{key}] = {value}")
            setattr(self, key, value)
        # Add value attributes
        setattr(self, 'value', 0)
        setattr(self, 'oldvalue', 0)


    @property
    def value(self):
        try:    value = self._value
        except: value = None
        return value


    @value.setter
    def value(self,value): 
        if debug: print(f"Sensor[{self.name}].set({value})")
        self._oldvalue = self.value
        self._value = value


    def publish_ha_discovery_info(self):
        if hasattr(self, "config_topic"):
            # Build device message
            device_dict = {"device": config["mqtt"]["device"],} 
            
            # Build payload message
            payload_dict = {
                "device_class": self.device_class,
                "name": self.name,
                "state_topic": self.state_topic,
                "unique_id": self.unique_id
            }
            if hasattr(self, "command_topic"): payload_dict.update({"command_topic": self.command_topic})
            if hasattr(self, "payload_off"): payload_dict.update({"payload_off": self.payload_off})
            if hasattr(self, "payload_on"): payload_dict.update({"payload_on": self.payload_on})

            # Merge device_dict and payload_dict
            device_dict.update(payload_dict)

            # Convert to JSON
            payload_json = json.dumps(device_dict)
            
            # Publish discovery message to MQTT
            if debug: print("Publishing MQTT message to " + self.config_topic)
            if debug: print(payload_json)
            client.publish(self.config_topic, payload=payload_json, qos=config["mqtt"]["qos"], retain=True)


class Monitor(object):
    instanceArr = []
    def __init__(self, unique_id, config):
        # Welk comment hier?
        self.__class__.instanceArr.append(weakref.proxy(self))
        # Add all attributes from config file
        setattr(self, "unique_id", unique_id)
        for key, value in config.items():
            if debug: print(f"Initializing Gpio[{config['name']}][{key}] = {value}")
            setattr(self, key, value)
        self._value = None
        self._oldvalue = None
        
    @property
    def value(self):
        try:    return self._value
        except: return None
    
    @value.setter
    def value(self,value): 
        if debug: print(f"Monitor.set([{self.name}]: {value}")
        self._oldvalue = self.value
        self._value = value


    def publish_ha_discovery_info(self):
        if hasattr(self, "config_topic"):
            # Build device message
            device_dict = {"device": config["mqtt"]["device"],} 
            
            # Build payload message
            payload_dict = {
                "device_class": self.device_class,
                "name": self.name,
                "state_topic": self.state_topic,
                "unique_id": self.unique_id
            }
            if hasattr(self, "command_topic"): payload_dict.update({"command_topic": self.command_topic})
            if hasattr(self, "payload_off"): payload_dict.update({"payload_off": self.payload_off})
            if hasattr(self, "payload_on"): payload_dict.update({"payload_on": self.payload_on})

            # Merge device_dict and payload_dict
            device_dict.update(payload_dict)

            # Convert to JSON
            payload_json = json.dumps(device_dict)
            
            # Publish discovery message to MQTT
            if debug: print("Publishing MQTT message to " + self.config_topic)
            if debug: print(payload_json)
            client.publish(self.config_topic, payload=payload_json, qos=config["mqtt"]["qos"], retain=True)







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
    time.sleep(1)

# Initialize basic sensor and gpio settings
# Enable 1 wire temperature sensors
os.system('modprobe w1-gpio')
os.system('modprobe w1-therm')
# Generic GPIO settings
GPIO.setmode(GPIO.BOARD)
GPIO.setwarnings(False)

# Create object instances
# Gpio instances
for gpio in config["gpios"]:
    #print(config["gpios"][gpio])|
    exec(f"globals()[gpio] = Gpio(gpio, config['gpios'][gpio])")
    globals()[gpio].publish_ha_discovery_info()
    globals()[gpio].gpio_init()
    pass

# Sensor instances
for sensor in config["sensors"]:
    #print(config["sensors"][sensor])
    exec(f"globals()[sensor] = Sensor(sensor, config['sensors'][sensor])")
    globals()[sensor].publish_ha_discovery_info()

#Monitor instances
for monitor in config["monitors"]:
    #print(config["monitors"][monitor])
    exec(f"globals()[monitor] = Monitor(monitor, config['monitors'][monitor])")
    globals()[monitor].publish_ha_discovery_info()
    
    
    
e inspringen na een comment!quit()


# TODO: eerst alle sensoren uitlezen voordat er (initial) states worden gezet, of bouw het in de set function, maar dan worden states wel heel vaak uitgelezen?
spa_status.value=spa_status.payload_on
spa_heatpump.switch("on")

quit()





#initialize sensor/gpio value dictionary
my_sensorvalues = dict.fromkeys(config["sensors"], None) | dict.fromkeys(config["gpios"], None)

# Initialize RPi.GPIO ports
initialize_gpios()

# Initialize sensor and gpio values in dictionary
if debug: print("\nInitializing sensor values")
process_sensors(publish=False)

# Set initial gpio states
set_gpio_initial_states()

# Set time of laast republish all
republished_on = datetime.now()
republish_minutes = config["mqtt"]["republish_min"]


try:
    while True:
        process_sensors(publish=True)
        #if my_sensorvalues["spa_status"] == config["sensors"]["spa_status"]["payload_on"]:
        #    # Problem detected! Switch all gpios off.
        #    set_gpio_initial_states(alloff=True)
        if republished_on + timedelta(minutes=republish_minutes) < datetime.now():
            #Time to republish all sensors
            if debug: print(">>>>>> Time to republish all sensors")
            republished_on = datetime.now()
            process_sensors(republish=True)
        if debug: print(f"\n\n\nSleeping for {config['mqtt']['sleep']} seconds...")
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
    set_gpio_initial_states()
    
    # Stop the MQTT loop and disconnect from the MQTT Broker
    if debug: print("\nDisconnect from broker")
    client.loop_stop()
    #client.disconnect()
    client.disconnect(reasoncodes.ReasonCodes(packettypes.PacketTypes.DISCONNECT, "Disconnect", 4))

