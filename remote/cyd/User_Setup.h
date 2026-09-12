// TFT_eSPI User_Setup.h for the ESP32-2432S028 "Cheap Yellow Display" (2.8" ILI9341, resistive touch).
// Copy over  <Arduino libraries>/TFT_eSPI/User_Setup.h
#define USER_SETUP_INFO "CYD 2.8 ILI9341"

#define ILI9341_2_DRIVER          // if the picture is inverted/garbled on your unit, try ST7789_DRIVER + TFT_INVERSION_OFF
#define TFT_WIDTH  240
#define TFT_HEIGHT 320
#define TFT_BL   21
#define TFT_BACKLIGHT_ON HIGH

#define TFT_MISO 12
#define TFT_MOSI 13
#define TFT_SCLK 14
#define TFT_CS   15
#define TFT_DC    2
#define TFT_RST  -1

// The CYD's display pins are the HSPI set; using HSPI leaves VSPI free for the XPT2046 touch bus.
#define USE_HSPI_PORT

#define LOAD_GLCD
#define LOAD_FONT2
#define LOAD_FONT4
#define LOAD_FONT6
#define LOAD_FONT7
#define LOAD_GFXFF
#define SMOOTH_FONT

#define SPI_FREQUENCY       40000000
#define SPI_READ_FREQUENCY  20000000
#define SPI_TOUCH_FREQUENCY  2500000
