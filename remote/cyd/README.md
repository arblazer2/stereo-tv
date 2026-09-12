# stereo-tv wired remote (ESP32 CYD)

Board: ESP32-2432S028 "Cheap Yellow Display" (2.8" 320×240 ILI9341, XPT2046 touch, CH340 USB serial).
One USB cable to the Pi carries power and the serial link. No Wi-Fi involved.

## Flash (Arduino IDE)
1. Boards Manager → install **esp32** by Espressif. Board: **ESP32 Dev Module**. Upload speed 921600 is fine.
2. Library Manager → **TFT_eSPI** (Bodmer) and **XPT2046_Touchscreen** (Paul Stoffregen).
3. Copy `User_Setup.h` from this folder over `Documents/Arduino/libraries/TFT_eSPI/User_Setup.h`.
4. Open `stereotv_remote.ino`, select the CYD's COM port, upload.
   - If the screen stays white or colours are inverted, your CYD is the ST7789 variant: in `User_Setup.h`
     swap `ILI9341_2_DRIVER` for `ST7789_DRIVER` and add `#define TFT_INVERSION_OFF`.
   - If taps land off-target, tweak `TS_MIN*/TS_MAX*` at the top of the sketch.

## Plug into the Pi
- Any USB port. It shows up as `/dev/ttyUSB0` (`/dev/serial/by-id/usb-1a86_USB_Serial-…`).
- The service auto-detects it (`[remote] port = "auto"`) and reconnects if it's unplugged.
- The service must be in the `dialout` group: `deploy/stereo-tv.service` has it; reapply with
  `sudo cp ~/stereo-tv/deploy/stereo-tv.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl restart stereo-tv`.

## Protocol (115200, newline-terminated)
remote → Pi: `CH 5` · `KEY SPACE` (any keyboard key name or single char) · `SAVER [mode]` · `PING`
Pi → remote: `S {"ch":5,"name":"GUIDE","np":"Artist · Title","st":"LOCKED","sv":0}` once a second, `OK`/`ERR …` replies.

Test from a laptop with a serial terminal: send `CH 2` and watch the Pi change channel.
