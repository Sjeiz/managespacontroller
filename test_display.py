from LCDI2C_backpack import LCDI2C_backpack
import time

lcd = LCDI2C_backpack(0x27)
print('I2C Adapter test script!')
print('[Press CTRL + C to end the script!]')

try:
    while True:
        lcd.lcd_string("AZ-Delivery",lcd.LCD_LINE_1)
        for i in range(0,11):
            lcd.lcd_string("Count seconds:"+str(i),lcd.LCD_LINE_2)
        time.sleep(1)
        lcd.clear()
        time.sleep(1)

except KeyboardInterrupt:
    print('\nScript end!')

finally:
    lcd.clear()