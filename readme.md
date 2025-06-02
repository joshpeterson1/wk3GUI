# WKUSB GUI

🔗 **Live site:** [https://wkusb-gui.glitch.me/](https://wkusb-gui.glitch.me/)

## Overview

**WKUSB GUI** is a cross-platform application for controlling and configuring a **WKUSB Morse keyer** device, either through a web interface or a local Python GUI.

It supports nearly all commonly used WKUSB settings and commands, and includes several built-in features for Morse practice. More advanced functions are planned for future releases.

## Features

- Local Python GUI via PyQt6
- Web interface (Glitch-hosted)
- Sidetone, WPM, weighting, paddle mode, and more
- Practice mode with character echo and speed settings
- EEPROM access and device diagnostics

## Getting Started (Local; WIN)

1. Download latest .exe from releases
2. Run file, select click connect, select COM Port, then Open Host mode.
3. *BEFORE* reporting any issues, close the program, unplug your WKUSB device, plug it back in and try again. Then email me :)

## Getting Started (Local; MAC/LIN)
_Authored and tested on Python 3.12.10; Your mileage may very. Check out [pyenv](https://github.com/pyenv/pyenv) to try 3.12.10_
1. Clone repo
2. Extract and navigate to folder
3. Run `python3 -m pip install -r requirements.txt`
4. Run `python3 ./wk3_app.py`
