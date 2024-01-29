import json
    
#class MyMeta(type):
#    def __call__(cls, config): #*args, **kwargs):
#        instance = super().__call__(config)
#
#        return instance

#class MyClass(metaclass=MyMeta):
class MyClass(object):
    def __init__(self, config):
        for key, value in config.items():
            setattr(self, key, value)

        #self._value = None
        #self._oldvalue = None

        
    @property
    def value(self):
        print('called getter')
        try:
            return self._value
        except:
            return None
    
    @value.setter
    def value(self,value): 
        print('called setter')
        self._oldvalue = self.value
        self._value = value




config = json.loads("""{ 
            "name"          : "Spa HeatPump", 
            "direction"     : "output", 
            "pin"           : 11, 
            "initial_state" : "on", 
            "conflict"      : "spa_heater", 
            "device_class"  : "switch", 
            "config_topic"  : "homeassistant/switch/spa-controller/spa_heatpump/config", 
            "command_topic" : "spa-controller/spa_heatpump/set", 
            "state_topic"   : "spa-controller/spa_heatpump", 
			"payload_on"    : "on", 
			"payload_off"   : "off", 
			"gpio_on"       : 0, 
			"gpio_off"      : 1 
        }""")

print("config")

# Creating an instance of MyClass with extra arguments
obj = MyClass(config)
    #10, 20, extra_arg="Hello")

obj.value = 10
obj.value += 90
obj.value = -1

# Accessing attributes
#print(obj.x)  # Output: 10
#print(obj.y)  # Output: 20
#print(obj.extra_arg)  # Output: Hello

quit()