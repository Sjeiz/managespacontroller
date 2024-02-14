# requires liquidcrystal_i2c (https://github.com/pl31/python-liquidcrystal_i2c/tree/master)
import liquidcrystal_i2c
from time import *

 
cols = 20
rows = 4

lcd = liquidcrystal_i2c.LiquidCrystal_I2C(0x27, 1, numlines=rows)
lcd.printline(0, 'LCM2004 IIC V2'.center(cols))
lcd.printline(1, 'and'.center(cols))
lcd.printline(2, 'python-')
lcd.printline(3, 'liquidcrystal_i2c'.rjust(cols))
lcd.on()
