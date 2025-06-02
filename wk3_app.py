"""
WK3 Device Interface - Python implementation with PyQt6

This application provides a GUI for communicating with WK3 Morse code keyer devices.
It supports all the features of the original Electron app including keyboard emulation.
"""

import sys
import time
import serial
import serial.tools.list_ports
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QComboBox, QPushButton, 
    QVBoxLayout, QHBoxLayout, QWidget, QLabel, QTextEdit, 
    QSlider, QCheckBox, QGroupBox, QLineEdit, QMenuBar, QMenu
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QKeyEvent, QAction
import pynput.keyboard as keyboard


class SerialThread(QThread):
    """Thread for handling serial communication without blocking the UI"""
    data_received = pyqtSignal(bytes)
    connection_error = pyqtSignal(str)
    
    def __init__(self, port, baud_rate):
        """Initialize the serial thread with port and baud rate"""
        super().__init__()
        self.port = port
        self.baud_rate = baud_rate
        self.running = False
        self.ser = None
        
    def run(self):
        """Main thread execution - reads data from serial port"""
        try:
            self.ser = serial.Serial(self.port, self.baud_rate, timeout=0.1)
            self.running = True
            
            while self.running:
                if self.ser.in_waiting:
                    data = self.ser.read(self.ser.in_waiting)
                    self.data_received.emit(data)
                self.msleep(10)  # Small delay to prevent CPU hogging
                
        except Exception as e:
            self.connection_error.emit(str(e))
            self.running = False
            
    def stop(self):
        """Stop the thread and close the serial port"""
        self.running = False
        if hasattr(self, 'ser') and self.ser and self.ser.is_open:
            self.ser.close()
            
    def send_data(self, data):
        """Send data to the serial port"""
        if hasattr(self, 'ser') and self.ser and self.ser.is_open:
            try:
                self.ser.write(data)
                return True
            except Exception as e:
                self.connection_error.emit(f"Send error: {str(e)}")
                return False
        return False


class WK3Interface(QMainWindow):
    """Main application window for WK3 device interface"""
    def __init__(self):
        """Initialize the main window and setup UI"""
        super().__init__()
        self.setWindowTitle("WK3 Device Interface")
        self.resize(800, 600)
        
        # Serial connection variables
        self.serial_thread = None
        self.keyboard_controller = keyboard.Controller()
        self.keyboard_emulation_active = False
        
        # WK3 state variables
        self.current_mode_register = 0x50  # Default: 01010000 - Iambic A mode
        self.current_pin_config = 0x06  # Default: 00000110 - Normal ult, 1ws+1dit hangtime, keyout2 on, sidetone on
        
        # WKMode register bits (from default 0x50 = 01010000)
        self.paddle_watchdog_disabled = False  # Bit 7: 0 = enabled
        self.paddle_echo_enabled = True        # Bit 6: 1 = enabled
        self.keyer_mode = 1                    # Bits 5,4: 01 = Iambic A
        self.paddle_swapped = False            # Bit 3: 0 = normal
        self.serial_echo_enabled = False       # Bit 2: 0 = disabled
        self.autospace_enabled = False         # Bit 1: 0 = disabled
        self.contest_spacing_enabled = False   # Bit 0: 0 = disabled
        
        # PinCFG register bits (from default 0x06 = 00000110)
        self.ultimatic_priority = 0  # Bits 7,6: 0=Normal, 1=Dah Priority, 2=Dit Priority
        self.hangtime_setting = 0    # Bits 5,4: 0=1ws+1dit, 1=1ws+2dit, 2=1ws+4dit, 3=1ws+8dit
        self.keyout1_enabled = False # Bit 3: 0 = disabled
        self.keyout2_enabled = True  # Bit 2: 1 = enabled
        self.sidetone_enabled = True # Bit 1: 1 = enabled
        self.ptt_enabled = False     # Bit 0: 0 = disabled
        
        self.host_mode_active = False
        self.current_wpm = 20  # Default 20 WPM
        self.current_key_comp = 50  # Default 50ms key compensation
        
        # Command tracking variables
        self.last_command_byte = None
        self.admin_open_sequence = False
        self.expecting_revision_code = False
        self.expecting_status_byte = False
        
        # Setup UI
        self.setup_ui()
        self.setup_menu_bar()
        
        # Setup periodic port refresh
        self.port_refresh_timer = QTimer()
        self.port_refresh_timer.timeout.connect(self.refresh_ports)
        self.port_refresh_timer.start(5000)  # Refresh every 5 seconds
        
    def setup_ui(self):
        """Set up the user interface"""
        # Main layout
        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        self.setCentralWidget(main_widget)
        
        # Create a nice font for the UI
        font = QFont("Arial", 10)
        self.setFont(font)
        
        # Warning/instructions box
        warning_box = QGroupBox("Requirements & Instructions")
        warning_layout = QVBoxLayout(warning_box)
        warning_text = QLabel(
            "<b>Requirements:</b> This application requires a WK3 device "
            "connected to your computer.<br><br>"
            "<b>Directions:</b> Connect WK3 device, click 'Connect to Device', "
            "then select COM Port. Open Host Mode to change settings and begin "
            "paddle echo."
        )
        warning_text.setWordWrap(True)
        warning_layout.addWidget(warning_text)
        main_layout.addWidget(warning_box)
        
        # Controls section
        controls_box = QGroupBox("Controls")
        controls_layout = QVBoxLayout(controls_box)
        
        # Connection controls
        conn_layout = QHBoxLayout()
        self.port_combo = QComboBox()
        self.refresh_ports()
        self.baud_combo = QComboBox()
        self.baud_combo.addItems(["1200", "9600"])
        self.connect_btn = QPushButton("Connect to Device")
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.setEnabled(False)
        
        conn_layout.addWidget(QLabel("Port:"))
        conn_layout.addWidget(self.port_combo)
        conn_layout.addWidget(QLabel("Baud:"))
        conn_layout.addWidget(self.baud_combo)
        conn_layout.addWidget(self.connect_btn)
        conn_layout.addWidget(self.disconnect_btn)
        controls_layout.addLayout(conn_layout)
        
        # Host mode controls
        host_layout = QHBoxLayout()
        self.open_host_btn = QPushButton("Open Host")
        self.close_host_btn = QPushButton("Close Host")
        self.open_host_btn.setEnabled(False)
        self.close_host_btn.setEnabled(False)
        
        host_layout.addWidget(self.open_host_btn)
        host_layout.addWidget(self.close_host_btn)
        controls_layout.addLayout(host_layout)
        
        # Keyer mode controls
        keyer_layout = QHBoxLayout()
        self.keyer_mode_combo = QComboBox()
        self.keyer_mode_combo.addItems(["Iambic B", "Iambic A", "Ultimatic", "Bug Mode"])
        self.keyer_mode_combo.setCurrentIndex(1)  # Default to Iambic A
        self.paddle_swap_btn = QPushButton("Toggle Paddle Swap")
        self.sidetone_btn = QPushButton("Toggle Sidetone")
        
        self.keyer_mode_combo.setEnabled(False)
        self.paddle_swap_btn.setEnabled(False)
        self.sidetone_btn.setEnabled(False)
        
        keyer_layout.addWidget(QLabel("Keyer Mode:"))
        keyer_layout.addWidget(self.keyer_mode_combo)
        keyer_layout.addWidget(self.paddle_swap_btn)
        keyer_layout.addWidget(self.sidetone_btn)
        controls_layout.addLayout(keyer_layout)
        
        # WPM controls
        wpm_layout = QHBoxLayout()
        self.wpm_slider = QSlider(Qt.Orientation.Horizontal)
        self.wpm_slider.setRange(5, 99)
        self.wpm_slider.setValue(20)
        self.wpm_label = QLabel("20 WPM")
        self.set_wpm_btn = QPushButton("Set Speed")
        
        self.wpm_slider.setEnabled(False)
        self.set_wpm_btn.setEnabled(False)
        
        wpm_layout.addWidget(QLabel("Speed (WPM):"))
        wpm_layout.addWidget(self.wpm_slider)
        wpm_layout.addWidget(self.wpm_label)
        wpm_layout.addWidget(self.set_wpm_btn)
        controls_layout.addLayout(wpm_layout)
        
        # Key Compensation controls
        keycomp_layout = QHBoxLayout()
        self.keycomp_slider = QSlider(Qt.Orientation.Horizontal)
        self.keycomp_slider.setRange(0, 50)
        self.keycomp_slider.setValue(50)
        self.keycomp_label = QLabel("50 ms")
        self.set_keycomp_btn = QPushButton("Set Compensation")
        
        self.keycomp_slider.setEnabled(False)
        self.set_keycomp_btn.setEnabled(False)
        
        keycomp_layout.addWidget(QLabel("Key Compensation (ms):"))
        keycomp_layout.addWidget(self.keycomp_slider)
        keycomp_layout.addWidget(self.keycomp_label)
        keycomp_layout.addWidget(self.set_keycomp_btn)
        controls_layout.addLayout(keycomp_layout)
        
        # Ultimatic controls (hidden by default)
        self.ultimatic_box = QGroupBox("Ultimatic Settings")
        self.ultimatic_box.setVisible(False)
        ultimatic_layout = QHBoxLayout(self.ultimatic_box)
        self.ultimatic_priority_combo = QComboBox()
        self.ultimatic_priority_combo.addItems(["Normal", "Dah Priority", "Dit Priority"])
        self.ultimatic_priority_combo.setEnabled(False)
        
        ultimatic_layout.addWidget(QLabel("Ultimatic Priority:"))
        ultimatic_layout.addWidget(self.ultimatic_priority_combo)
        controls_layout.addWidget(self.ultimatic_box)
        
        # PinCFG controls
        pincfg_box = QGroupBox("PinCFG Settings")
        pincfg_layout = QVBoxLayout(pincfg_box)
        
        # Hangtime controls
        hangtime_layout = QHBoxLayout()
        self.hangtime_combo = QComboBox()
        self.hangtime_combo.addItems(["1 wordspace + 1 dit", "1 wordspace + 2 dits", "1 wordspace + 4 dits", "1 wordspace + 8 dits"])
        self.hangtime_combo.setEnabled(False)
        
        hangtime_layout.addWidget(QLabel("Hangtime:"))
        hangtime_layout.addWidget(self.hangtime_combo)
        pincfg_layout.addLayout(hangtime_layout)
        
        # Key output controls
        keyout_layout = QHBoxLayout()
        self.keyout1_cb = QCheckBox("Key Out 1")
        self.keyout2_cb = QCheckBox("Key Out 2")
        self.keyout2_cb.setChecked(True)  # Default enabled
        self.ptt_cb = QCheckBox("PTT Enable")
        
        self.keyout1_cb.setEnabled(False)
        self.keyout2_cb.setEnabled(False)
        self.ptt_cb.setEnabled(False)
        
        keyout_layout.addWidget(self.keyout1_cb)
        keyout_layout.addWidget(self.keyout2_cb)
        keyout_layout.addWidget(self.ptt_cb)
        pincfg_layout.addLayout(keyout_layout)
        
        controls_layout.addWidget(pincfg_box)
        
        # Hide advanced settings by default
        pincfg_box.setVisible(False)
        
        # WKMode controls
        wkmode_box = QGroupBox("WKMode Settings")
        wkmode_layout = QVBoxLayout(wkmode_box)
        
        # First row: Paddle controls
        paddle_layout = QHBoxLayout()
        self.paddle_watchdog_cb = QCheckBox("Disable Paddle Watchdog")
        self.paddle_echo_cb = QCheckBox("Paddle Echo")
        self.paddle_echo_cb.setChecked(True)  # Default enabled
        
        self.paddle_watchdog_cb.setEnabled(False)
        self.paddle_echo_cb.setEnabled(False)
        
        paddle_layout.addWidget(self.paddle_watchdog_cb)
        paddle_layout.addWidget(self.paddle_echo_cb)
        wkmode_layout.addLayout(paddle_layout)
        
        # Second row: Communication controls
        comm_layout = QHBoxLayout()
        self.serial_echo_cb = QCheckBox("Serial Echo Back")
        self.autospace_cb = QCheckBox("Autospace")
        self.contest_spacing_cb = QCheckBox("Contest Spacing")
        
        self.serial_echo_cb.setEnabled(False)
        self.autospace_cb.setEnabled(False)
        self.contest_spacing_cb.setEnabled(False)
        
        comm_layout.addWidget(self.serial_echo_cb)
        comm_layout.addWidget(self.autospace_cb)
        comm_layout.addWidget(self.contest_spacing_cb)
        wkmode_layout.addLayout(comm_layout)
        
        controls_layout.addWidget(wkmode_box)
        
        # Hide advanced settings by default
        wkmode_box.setVisible(False)
        
        # Store references to advanced settings boxes
        self.pincfg_box = pincfg_box
        self.wkmode_box = wkmode_box
        
        main_layout.addWidget(controls_box)
        
        # Status display
        self.status_label = QLabel("Disconnected")
        self.status_label.setStyleSheet(
            "background-color: #fed7d7; color: #c53030; padding: 10px; border-radius: 5px;"
        )
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(self.status_label)
        
        # ASCII Monitor section
        monitor_box = QGroupBox("ASCII Monitor")
        monitor_layout = QVBoxLayout(monitor_box)
        
        # Keyboard emulation controls
        emulation_layout = QHBoxLayout()
        self.keyboard_emulation_cb = QCheckBox("Enable Keyboard Emulation")
        self.emulation_status = QLabel("")
        
        emulation_layout.addWidget(self.keyboard_emulation_cb)
        emulation_layout.addWidget(self.emulation_status)
        monitor_layout.addLayout(emulation_layout)
        
        # ASCII Monitor display
        monitor_display_layout = QHBoxLayout()
        self.ascii_monitor = QTextEdit()
        self.ascii_monitor.setReadOnly(True)
        self.ascii_monitor.setStyleSheet(
            "background-color: #2d3748; color: #48bb78; font-family: monospace;"
        )
        self.clear_ascii_btn = QPushButton("Clear")
        
        monitor_display_layout.addWidget(self.ascii_monitor)
        monitor_display_layout.addWidget(
            self.clear_ascii_btn, 
            alignment=Qt.AlignmentFlag.AlignTop
        )
        monitor_layout.addLayout(monitor_display_layout)
        
        main_layout.addWidget(monitor_box)
        
        # Debug panel (hidden by default)
        self.debug_box = QGroupBox("Debug Panel")
        self.debug_box.setVisible(False)
        debug_layout = QVBoxLayout(self.debug_box)
        
        # Command input
        command_layout = QHBoxLayout()
        self.command_input = QLineEdit()
        self.command_input.setPlaceholderText("Enter hex command (e.g., 48656C6C6F)")
        self.send_cmd_btn = QPushButton("Send Command")
        self.send_cmd_btn.setEnabled(False)
        
        command_layout.addWidget(self.command_input)
        command_layout.addWidget(self.send_cmd_btn)
        debug_layout.addLayout(command_layout)
        
        # Log display
        self.log_display = QTextEdit()
        self.log_display.setReadOnly(True)
        self.log_display.setStyleSheet(
            "background-color: #1a202c; color: #e2e8f0; font-family: monospace;"
        )
        self.clear_log_btn = QPushButton("Clear Log")
        
        debug_layout.addWidget(self.log_display)
        debug_layout.addWidget(self.clear_log_btn)
        
        # Test button
        self.test_wk3_btn = QPushButton("Run Basic WK3 Test")
        self.test_wk3_btn.setEnabled(False)
        debug_layout.addWidget(self.test_wk3_btn)
        
        main_layout.addWidget(self.debug_box)
        
        # Connect signals
        self.connect_btn.clicked.connect(self.connect_to_device)
        self.disconnect_btn.clicked.connect(self.disconnect_from_device)
        self.open_host_btn.clicked.connect(self.enter_host_mode)
        self.close_host_btn.clicked.connect(self.exit_host_mode)
        self.clear_ascii_btn.clicked.connect(self.clear_ascii_monitor)
        self.keyboard_emulation_cb.stateChanged.connect(self.toggle_keyboard_emulation)
        self.wpm_slider.valueChanged.connect(self.update_wpm_display)
        self.set_wpm_btn.clicked.connect(self.set_wpm)
        self.keycomp_slider.valueChanged.connect(self.update_keycomp_display)
        self.set_keycomp_btn.clicked.connect(self.set_keycomp)
        self.keyer_mode_combo.currentIndexChanged.connect(self.update_ultimatic_controls)
        self.ultimatic_priority_combo.currentIndexChanged.connect(self.set_keyer_mode)
        self.paddle_swap_btn.clicked.connect(self.toggle_paddle_swap)
        self.sidetone_btn.clicked.connect(self.toggle_sidetone)
        self.hangtime_combo.currentIndexChanged.connect(self.set_hangtime)
        self.keyout1_cb.stateChanged.connect(self.toggle_keyout1)
        self.keyout2_cb.stateChanged.connect(self.toggle_keyout2)
        self.ptt_cb.stateChanged.connect(self.toggle_ptt)
        self.paddle_watchdog_cb.stateChanged.connect(self.toggle_paddle_watchdog)
        self.paddle_echo_cb.stateChanged.connect(self.toggle_paddle_echo)
        self.serial_echo_cb.stateChanged.connect(self.toggle_serial_echo)
        self.autospace_cb.stateChanged.connect(self.toggle_autospace)
        self.contest_spacing_cb.stateChanged.connect(self.toggle_contest_spacing)
        self.send_cmd_btn.clicked.connect(self.send_command)
        self.clear_log_btn.clicked.connect(self.clear_log)
        self.test_wk3_btn.clicked.connect(self.test_wk3)
        
        # Initialize the log
        self.add_log_entry("Ready to connect to WK3 device...")
        self.add_log_entry(
            "WK3 Protocol Notes:\n"
            "• Status bytes: 0xC0-0xFF (unsolicited)\n"
            "• Speed pot: 0x80-0xBF (unsolicited)\n"
            "• Echo backs: 0x00-0x7F (responses to commands)\n\n"
            "WK3 Controls Available:\n"
            "• Keyer Modes: Iambic A/B, Ultimatic, Bug\n"
            "• WKMode (0x0E): Bits 7=PaddleWD, 6=PaddleEcho, 5,4=Mode, 3=PaddleSwap, 2=SerialEcho, 1=Autospace, 0=ContestSpace\n"
            "• PinCFG (0x09): Bits 7,6=Ult Pri, 5,4=Hangtime, 3=KeyOut1, 2=KeyOut2, 1=Sidetone, 0=PTT",
            style="color: #a0aec0; font-size: 12px;"
        )
        
    def setup_menu_bar(self):
        """Set up the menu bar"""
        menubar = self.menuBar()
        
        # File menu
        file_menu = menubar.addMenu('&File')
        
        # Connect action
        connect_action = QAction('&Connect to Device', self)
        connect_action.setShortcut('Ctrl+C')
        connect_action.setStatusTip('Connect to WK3 device')
        connect_action.triggered.connect(self.connect_to_device)
        file_menu.addAction(connect_action)
        
        # Disconnect action
        disconnect_action = QAction('&Disconnect', self)
        disconnect_action.setShortcut('Ctrl+D')
        disconnect_action.setStatusTip('Disconnect from WK3 device')
        disconnect_action.triggered.connect(self.disconnect_from_device)
        file_menu.addAction(disconnect_action)
        
        file_menu.addSeparator()
        
        # Host Mode actions
        open_host_action = QAction('&Open Host Mode', self)
        open_host_action.setShortcut('Ctrl+O')
        open_host_action.setStatusTip('Enter host mode on WK3 device')
        open_host_action.triggered.connect(self.enter_host_mode)
        file_menu.addAction(open_host_action)
        
        close_host_action = QAction('&Close Host Mode', self)
        close_host_action.setShortcut('Ctrl+Shift+O')
        close_host_action.setStatusTip('Exit host mode on WK3 device')
        close_host_action.triggered.connect(self.exit_host_mode)
        file_menu.addAction(close_host_action)
        
        file_menu.addSeparator()
        
        # Exit action
        exit_action = QAction('E&xit', self)
        exit_action.setShortcut('Ctrl+Q')
        exit_action.setStatusTip('Exit application')
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        
        # Edit menu
        edit_menu = menubar.addMenu('&Edit')
        
        # Clear ASCII Monitor action
        clear_ascii_action = QAction('Clear &ASCII Monitor', self)
        clear_ascii_action.setShortcut('Ctrl+A')
        clear_ascii_action.setStatusTip('Clear the ASCII monitor display')
        clear_ascii_action.triggered.connect(self.clear_ascii_monitor)
        edit_menu.addAction(clear_ascii_action)
        
        # Clear Log action
        clear_log_action = QAction('Clear &Log', self)
        clear_log_action.setShortcut('Ctrl+L')
        clear_log_action.setStatusTip('Clear the debug log')
        clear_log_action.triggered.connect(self.clear_log)
        edit_menu.addAction(clear_log_action)
        
        # View menu
        view_menu = menubar.addMenu('&View')
        
        # Debug Panel toggle action
        self.debug_panel_action = QAction('&Debug Panel', self)
        self.debug_panel_action.setCheckable(True)
        self.debug_panel_action.setShortcut('F12')
        self.debug_panel_action.setStatusTip('Show/hide debug panel')
        self.debug_panel_action.triggered.connect(self.toggle_debug_panel)
        view_menu.addAction(self.debug_panel_action)
        
        # Advanced Settings toggle action
        self.advanced_settings_action = QAction('&Advanced Settings', self)
        self.advanced_settings_action.setCheckable(True)
        self.advanced_settings_action.setShortcut('Ctrl+Shift+A')
        self.advanced_settings_action.setStatusTip('Show/hide advanced PinCFG and WKMode settings')
        self.advanced_settings_action.triggered.connect(self.toggle_advanced_settings)
        view_menu.addAction(self.advanced_settings_action)
        
        # Tools menu
        tools_menu = menubar.addMenu('&Tools')
        
        # Test action
        test_action = QAction('Run &Basic WK3 Test', self)
        test_action.setShortcut('Ctrl+T')
        test_action.setStatusTip('Run basic WK3 test sequence')
        test_action.triggered.connect(self.test_wk3)
        tools_menu.addAction(test_action)
        
        # Help menu
        help_menu = menubar.addMenu('&Help')
        
        # About action
        about_action = QAction('&About', self)
        about_action.setStatusTip('About WK3 Device Interface')
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)
        
    def toggle_debug_panel(self):
        """Toggle the visibility of the debug panel"""
        is_visible = not self.debug_box.isVisible()
        self.debug_box.setVisible(is_visible)
        self.debug_panel_action.setChecked(is_visible)
        
    def show_about(self):
        """Show the about dialog"""
        from PyQt6.QtWidgets import QMessageBox
        
        about_text = """
        <h3>WK3 Device Interface</h3>
        <p><b>Version:</b> 1.0</p>
        <p><b>Description:</b> Python GUI application for communicating with WK3 Morse code keyer devices.</p>
        <p><b>Features:</b></p>
        <ul>
        <li>Full WK3 protocol support</li>
        <li>Real-time paddle echo and keyboard emulation</li>
        <li>Complete control over WKMode and PinCFG registers</li>
        <li>Debug panel for advanced users</li>
        </ul>
        <p><b>Requirements:</b> WK3 device connected via serial port</p>
        """
        
        QMessageBox.about(self, "About WK3 Device Interface", about_text)
        
    def toggle_advanced_settings(self):
        """Toggle the visibility of advanced settings (PinCFG and WKMode)"""
        is_visible = not self.pincfg_box.isVisible()
        self.pincfg_box.setVisible(is_visible)
        self.wkmode_box.setVisible(is_visible)
        self.advanced_settings_action.setChecked(is_visible)
        
    def refresh_ports(self):
        """Refresh the list of available serial ports"""
        current_port = self.port_combo.currentText()
        self.port_combo.clear()
        ports = [port.device for port in serial.tools.list_ports.comports()]
        self.port_combo.addItems(ports)
        
        # Try to restore the previously selected port
        if current_port and current_port in ports:
            self.port_combo.setCurrentText(current_port)
        
    def connect_to_device(self):
        """Connect to the WK3 device"""
        port = self.port_combo.currentText()
        baud_rate = int(self.baud_combo.currentText())
        
        if not port:
            self.add_log_entry("❌ Error: No port selected", "error")
            return
            
        try:
            self.serial_thread = SerialThread(port, baud_rate)
            self.serial_thread.data_received.connect(self.process_received_data)
            self.serial_thread.connection_error.connect(self.handle_connection_error)
            self.serial_thread.start()
            
            self.update_connection_status(True)
            self.add_log_entry(f"✅ Connected to {port} at {baud_rate} baud", "connected")
            self.append_to_ascii_monitor(f"Connected to {port} at {baud_rate} baud\n")
            
        except Exception as e:
            self.add_log_entry(f"❌ Connection error: {str(e)}", "error")
            self.append_to_ascii_monitor(f"Connection error: {str(e)}\n")
            
    def handle_connection_error(self, error_msg):
        """Handle connection errors from the serial thread"""
        self.add_log_entry(f"❌ {error_msg}", "error")
        self.append_to_ascii_monitor(f"Error: {error_msg}\n")
        self.disconnect_from_device()
            
    def disconnect_from_device(self):
        """Disconnect from the WK3 device"""
        if self.serial_thread:
            self.serial_thread.stop()
            self.serial_thread = None
            
        self.update_connection_status(False)
        self.add_log_entry("🔌 Disconnected from device", "disconnected")
        self.append_to_ascii_monitor("Disconnected from device\n")
        
    def update_connection_status(self, connected):
        """Update the UI based on connection status"""
        if connected:
            self.status_label.setText("Connected")
            self.status_label.setStyleSheet(
                "background-color: #c6f6d5; color: #2f855a; "
                "padding: 10px; border-radius: 5px;"
            )
            self.connect_btn.setEnabled(False)
            self.disconnect_btn.setEnabled(True)
            self.open_host_btn.setEnabled(True)
            self.close_host_btn.setEnabled(True)
            self.send_cmd_btn.setEnabled(True)
            self.test_wk3_btn.setEnabled(True)
        else:
            self.status_label.setText("Disconnected")
            self.status_label.setStyleSheet(
                "background-color: #fed7d7; color: #c53030; "
                "padding: 10px; border-radius: 5px;"
            )
            self.connect_btn.setEnabled(True)
            self.disconnect_btn.setEnabled(False)
            self.open_host_btn.setEnabled(False)
            self.close_host_btn.setEnabled(False)
            self.send_cmd_btn.setEnabled(False)
            self.test_wk3_btn.setEnabled(False)
            
            # Also disable all host mode controls
            self.host_mode_active = False
            self.update_controls_for_host_mode()
            
    def process_received_data(self, data):
        """Process data received from the WK3 device"""
        for byte in data:
            # Process each byte according to WK3 protocol
            hex_val = f"{byte:02X}"
            
            if (byte & 0xC0) == 0xC0:
                # Status byte (0xC0-0xFF)
                self.process_status_byte(byte)
            elif (byte & 0xC0) == 0x80:
                # Speed pot byte (0x80-0xBF)
                pot_value = byte & 0x3F  # Lower 6 bits
                self.add_log_entry(
                    f"🎛️ Speed Pot: 0x{hex_val} (Value: {pot_value}/63)", 
                    "received"
                )
                self.add_log_entry(f"    Speed pot change detected", "received")
            else:
                # Echo back byte (0x00-0x7F)
                self.add_log_entry(f"🔄 Echo Back: 0x{hex_val}", "received")
                if 32 <= byte <= 126:
                    self.add_log_entry(f"    ASCII: '{chr(byte)}'", "received")
                self.process_echo_back_byte(byte)
                
    def process_status_byte(self, byte):
        """Process a status byte from the WK3 device"""
        hex_val = f"{byte:02X}"
        bin_val = f"{byte:08b}"
        
        # Status byte processing logic
        busy = (byte & 0x04) != 0
        break_in = (byte & 0x02) != 0
        buffer_xoff = (byte & 0x01) != 0
        
        # Special handling for 0xC8 status byte which some WK3 devices send
        if byte == 0xC8:
            self.add_log_entry(f"📊 Status Byte: 0x{hex_val} (0b{bin_val})", "received")
            self.add_log_entry(
                f"    Special status byte 0xC8 received - WK3 is ready", 
                "received"
            )
        else:
            self.add_log_entry(f"📊 Status Byte: 0x{hex_val} (0b{bin_val})", "received")
            self.add_log_entry(
                f"    BUSY: {'YES' if busy else 'NO'}, "
                f"BREAKIN: {'YES' if break_in else 'NO'}, "
                f"Buffer XOFF: {'YES' if buffer_xoff else 'NO'}", 
                "received"
            )
            
            # If this is paddle input (BREAKIN is true), update UI accordingly
            if break_in:
                self.add_log_entry(f"    Paddle input detected", "received")
        
        if self.expecting_status_byte:
            self.expecting_status_byte = False
            self.add_log_entry(
                f"    Received status byte in response to status request", 
                "received"
            )
        
    def process_echo_back_byte(self, byte):
        """Process an echo back byte from the WK3 device"""
        hex_val = f"{byte:02X}"
        bin_val = f"{byte:08b}"
        ascii_val = chr(byte) if 32 <= byte <= 126 else '.'
        
        self.add_log_entry(
            f"📥 Raw byte: 0x{hex_val} ({byte}, binary: 0b{bin_val}, ASCII: '{ascii_val}')", 
            "received"
        )
        
        # Add printable ASCII characters to the ASCII monitor
        if 32 <= byte <= 126:
            self.append_to_ascii_monitor(chr(byte))
        elif byte in (13, 10):  # CR or LF
            self.append_to_ascii_monitor('\n')
            
        # Special handling for specific Admin command responses
        if self.last_command_byte == 0x21:
            # Response to Read Back Vcc command
            voltage = (26214 / byte) / 100
            self.add_log_entry(
                f"    VCC Voltage: {voltage:.2f}V (response to Read Back Vcc)", 
                "received"
            )
            self.last_command_byte = None
            return
        
        if self.last_command_byte == 0x22:
            # Response to Load X2MODE command
            self.add_log_entry(
                f"    X2MODE register value: 0x{hex_val} (response to Load X2MODE)", 
                "received"
            )
            self.last_command_byte = None
            return
        
        if self.last_command_byte == 0x24:
            # Response to Get IC Type command
            ic_type = "SMT" if byte == 0x01 else "DIP"
            self.add_log_entry(
                f"    IC Type: {ic_type} (response to Get IC Type)", 
                "received"
            )
            self.last_command_byte = None
            return
        
        # Simple handling for Admin:Open response
        if self.admin_open_sequence and self.expecting_revision_code:
            self.admin_open_sequence = False
            self.expecting_revision_code = False
            
            # Any response to Admin:Open should be the revision code
            self.add_log_entry(f"    Received revision code: 0x{hex_val} ({byte})", "received")
            
            # Now that we've received a response, we can enable host mode
            self.host_mode_active = True
            self.update_controls_for_host_mode()
            
            # Set default values for registers
            self.current_mode_register = 0x50  # Default to Iambic A mode (01010000)
            self.current_pin_config = 0x06     # Default: 00000110
            
            # Parse the default mode register 0x50 (01010000)
            self.paddle_watchdog_disabled = bool(self.current_mode_register & 0x80)  # Bit 7
            self.paddle_echo_enabled = bool(self.current_mode_register & 0x40)       # Bit 6
            self.keyer_mode = (self.current_mode_register >> 4) & 0x03               # Bits 5,4
            self.paddle_swapped = bool(self.current_mode_register & 0x08)            # Bit 3
            self.serial_echo_enabled = bool(self.current_mode_register & 0x04)       # Bit 2
            self.autospace_enabled = bool(self.current_mode_register & 0x02)         # Bit 1
            self.contest_spacing_enabled = bool(self.current_mode_register & 0x01)   # Bit 0
            
            # Parse the default pin config 0x06 (00000110)
            self.ultimatic_priority = (self.current_pin_config >> 6) & 0x03  # Bits 7,6
            self.hangtime_setting = (self.current_pin_config >> 4) & 0x03    # Bits 5,4
            self.keyout1_enabled = bool(self.current_pin_config & 0x08)      # Bit 3
            self.keyout2_enabled = bool(self.current_pin_config & 0x04)      # Bit 2
            self.sidetone_enabled = bool(self.current_pin_config & 0x02)     # Bit 1
            self.ptt_enabled = bool(self.current_pin_config & 0x01)          # Bit 0
            
            # Send these default values to the device to ensure sync
            self.send_bytes([0x0E, self.current_mode_register])
            self.add_log_entry(
                f"    Sent default mode register: 0x{self.current_mode_register:02X} (Iambic A)", 
                "sent"
            )
            
            # Update UI based on default mode register
            self.keyer_mode_combo.setCurrentIndex(self.keyer_mode)
            self.paddle_watchdog_cb.setChecked(self.paddle_watchdog_disabled)
            self.paddle_echo_cb.setChecked(self.paddle_echo_enabled)
            self.serial_echo_cb.setChecked(self.serial_echo_enabled)
            self.autospace_cb.setChecked(self.autospace_enabled)
            self.contest_spacing_cb.setChecked(self.contest_spacing_enabled)
            
            # Send default pin config: 0x06
            self.send_bytes([0x09, self.current_pin_config])
            self.add_log_entry(
                f"    Sent default pin config: 0x{self.current_pin_config:02X} "
                f"(Ult:{self.ultimatic_priority}, Hang:{self.hangtime_setting}, "
                f"K1:{self.keyout1_enabled}, K2:{self.keyout2_enabled}, "
                f"ST:{self.sidetone_enabled}, PTT:{self.ptt_enabled})", 
                "sent"
            )
            
            # Update UI controls to match the parsed values
            self.ultimatic_priority_combo.setCurrentIndex(self.ultimatic_priority)
            self.hangtime_combo.setCurrentIndex(self.hangtime_setting)
            self.keyout1_cb.setChecked(self.keyout1_enabled)
            self.keyout2_cb.setChecked(self.keyout2_enabled)
            self.ptt_cb.setChecked(self.ptt_enabled)
            
            # Send default WPM (20 WPM = 0x14 in hex)
            self.send_bytes([0x02, 0x14])
            self.add_log_entry(f"    Sent default speed: 20 WPM (0x14)", "sent")
            
            # Send default Key Compensation (50ms = 0x32 in hex)
            self.send_bytes([0x11, 0x32])
            self.add_log_entry(f"    Sent default key compensation: 50 ms (0x32)", "sent")
            
            self.update_paddle_swap_display()
            self.update_sidetone_display()
            self.update_ultimatic_controls()
            
            self.add_log_entry(f"    Host mode activated with default settings", "received")
            
            return
        
        elif byte == 0x0E:
            self.add_log_entry(f"    Mode register set acknowledged", "received")
        elif byte == 0x09:
            self.add_log_entry(f"    Pin config set acknowledged", "received")
        elif byte == 0x15:
            self.add_log_entry(f"    Status request acknowledged", "received")
            self.expecting_status_byte = True
        elif byte == 0x03:
            # Host mode exited
            self.host_mode_active = False
            self.update_controls_for_host_mode()
            self.add_log_entry(f"    Host mode exited successfully", "received")
            
    def append_to_ascii_monitor(self, text):
        """Append text to the ASCII monitor"""
        self.ascii_monitor.moveCursor(self.ascii_monitor.textCursor().MoveOperation.End)
        self.ascii_monitor.insertPlainText(text)
        self.ascii_monitor.moveCursor(self.ascii_monitor.textCursor().MoveOperation.End)
        
        # If keyboard emulation is active, send the character
        if self.keyboard_emulation_active and text.strip():
            self.emulate_key(text)
            
    def emulate_key(self, char):
        """Emulate keyboard input for the given character"""
        try:
            # Use pynput to type the character system-wide
            self.keyboard_controller.type(char)
            self.emulation_status.setText(f"Last key: {char}")
        except Exception as e:
            self.emulation_status.setText(f"Error: {e}")
            
    def toggle_keyboard_emulation(self, state):
        """Toggle keyboard emulation on/off"""
        from PyQt6.QtCore import Qt
        self.keyboard_emulation_active = (state == Qt.CheckState.Checked.value)
        if self.keyboard_emulation_active:
            self.emulation_status.setText("Active - Paddle input will be typed system-wide")
            self.keyboard_emulation_cb.setText("Disable Keyboard Emulation")
        else:
            self.emulation_status.setText("")
            self.keyboard_emulation_cb.setText("Enable Keyboard Emulation")
            
    def clear_ascii_monitor(self):
        """Clear the ASCII monitor"""
        self.ascii_monitor.clear()
        
    def enter_host_mode(self):
        """Enter host mode on the WK3 device"""
        if self.send_bytes([0x00, 0x02]):
            self.admin_open_sequence = True
            self.expecting_revision_code = True
            self.add_log_entry("🔓 Sent host mode entry command (Admin:Open)", "sent")
            self.add_log_entry("    Waiting for response...", "sent")
            
    def exit_host_mode(self):
        """Exit host mode on the WK3 device"""
        if self.send_bytes([0x00, 0x03]):
            self.host_mode_active = False
            self.add_log_entry("🔒 Exited host mode", "sent")
            self.update_controls_for_host_mode()
            
    def update_controls_for_host_mode(self):
        """Update UI controls based on host mode status"""
        # Enable/disable controls based on host mode
        self.keyer_mode_combo.setEnabled(self.host_mode_active)
        self.paddle_swap_btn.setEnabled(self.host_mode_active)
        self.sidetone_btn.setEnabled(self.host_mode_active)
        self.ultimatic_priority_combo.setEnabled(self.host_mode_active)
        self.hangtime_combo.setEnabled(self.host_mode_active)
        self.keyout1_cb.setEnabled(self.host_mode_active)
        self.keyout2_cb.setEnabled(self.host_mode_active)
        self.ptt_cb.setEnabled(self.host_mode_active)
        self.paddle_watchdog_cb.setEnabled(self.host_mode_active)
        self.paddle_echo_cb.setEnabled(self.host_mode_active)
        self.serial_echo_cb.setEnabled(self.host_mode_active)
        self.autospace_cb.setEnabled(self.host_mode_active)
        self.contest_spacing_cb.setEnabled(self.host_mode_active)
        self.wpm_slider.setEnabled(self.host_mode_active)
        self.set_wpm_btn.setEnabled(self.host_mode_active)
        self.keycomp_slider.setEnabled(self.host_mode_active)
        self.set_keycomp_btn.setEnabled(self.host_mode_active)
        
        # Update UI to reflect host mode status
        self.add_log_entry(
            f"Host mode is now {'ACTIVE' if self.host_mode_active else 'INACTIVE'}", 
            "connected" if self.host_mode_active else "disconnected"
        )
        
        # Visual feedback
        if self.host_mode_active:
            self.status_label.setText("Connected (Host Mode)")
            
            # Make the buttons more visibly enabled
            for btn in [self.paddle_swap_btn, self.sidetone_btn]:
                btn.setStyleSheet("")
            self.keyer_mode_combo.setStyleSheet("")
            for cb in [self.keyout1_cb, self.keyout2_cb, self.ptt_cb, 
                      self.paddle_watchdog_cb, self.paddle_echo_cb, 
                      self.serial_echo_cb, self.autospace_cb, self.contest_spacing_cb]:
                cb.setStyleSheet("")
        else:
            self.status_label.setText("Connected")
            
            # Make the buttons visibly disabled
            for btn in [self.paddle_swap_btn, self.sidetone_btn]:
                btn.setStyleSheet("opacity: 0.5;")
            self.keyer_mode_combo.setStyleSheet("opacity: 0.5;")
            for cb in [self.keyout1_cb, self.keyout2_cb, self.ptt_cb,
                      self.paddle_watchdog_cb, self.paddle_echo_cb,
                      self.serial_echo_cb, self.autospace_cb, self.contest_spacing_cb]:
                cb.setStyleSheet("opacity: 0.5;")
            
    def update_wpm_display(self):
        """Update the WPM display when the slider changes"""
        wpm = self.wpm_slider.value()
        self.wpm_label.setText(f"{wpm} WPM")
        
    def set_wpm(self):
        """Set the WPM on the WK3 device"""
        if not self.host_mode_active:
            self.add_log_entry("⚠️ Must be in host mode to change settings", "error")
            return
            
        wpm = self.wpm_slider.value()
        self.current_wpm = wpm
        
        # Convert decimal WPM to hex
        wpm_hex = format(wpm, '02x')
        
        if self.send_bytes([0x02, int(wpm_hex, 16)]):
            self.add_log_entry(f"🔢 Set speed to {wpm} WPM (0x{wpm_hex.upper()})", "sent")
            
    def update_keycomp_display(self):
        """Update the key compensation display when the slider changes"""
        keycomp = self.keycomp_slider.value()
        self.keycomp_label.setText(f"{keycomp} ms")
        
    def set_keycomp(self):
        """Set the key compensation on the WK3 device"""
        if not self.host_mode_active:
            self.add_log_entry("⚠️ Must be in host mode to change settings", "error")
            return
            
        keycomp = self.keycomp_slider.value()
        self.current_key_comp = keycomp
        
        # Convert decimal ms to hex
        keycomp_hex = format(keycomp, '02x')
        
        if self.send_bytes([0x11, int(keycomp_hex, 16)]):
            self.add_log_entry(
                f"⚙️ Set key compensation to {keycomp} ms (0x{keycomp_hex.upper()})", 
                "sent"
            )
            
    def update_ultimatic_controls(self):
        """Update the ultimatic controls based on keyer mode"""
        is_ultimatic = self.keyer_mode_combo.currentIndex() == 2  # Ultimatic is index 2
        self.ultimatic_box.setVisible(is_ultimatic)
        
        if self.host_mode_active:
            self.set_keyer_mode()
            
    def set_keyer_mode(self):
        """Set the keyer mode on the WK3 device"""
        if not self.host_mode_active:
            self.add_log_entry("⚠️ Must be in host mode to change settings", "error")
            return
            
        mode = self.keyer_mode_combo.currentIndex()
        
        # Update the keyer mode in our state
        self.keyer_mode = mode
        
        # For Ultimatic mode, handle the priority setting in pin config
        if mode == 2:  # Ultimatic mode
            self.ultimatic_priority = self.ultimatic_priority_combo.currentIndex()
            self.update_pin_config()
            self.add_log_entry(
                f"    Ultimatic priority: {self.ultimatic_priority_combo.currentText()}", 
                "sent"
            )
        
        # Update the full WKMode register
        self.update_wkmode_register()
        
        self.add_log_entry(
            f"🔧 Set keyer mode: {self.keyer_mode_combo.currentText()}", 
            "sent"
        )
            
    def update_wkmode_register(self):
        """Update and send the WKMode register based on current settings"""
        # Build the mode register byte from individual settings
        mode_register = 0
        
        # Bit 7: Disable paddle watchdog
        if self.paddle_watchdog_disabled:
            mode_register |= 0x80
            
        # Bit 6: Paddle echo
        if self.paddle_echo_enabled:
            mode_register |= 0x40
            
        # Bits 5,4: Keyer mode (00=Iambic B, 01=Iambic A, 10=Ultimatic, 11=Bug)
        mode_register |= (self.keyer_mode & 0x03) << 4
        
        # Bit 3: Paddle swap
        if self.paddle_swapped:
            mode_register |= 0x08
            
        # Bit 2: Serial echo back
        if self.serial_echo_enabled:
            mode_register |= 0x04
            
        # Bit 1: Autospace
        if self.autospace_enabled:
            mode_register |= 0x02
            
        # Bit 0: Contest spacing
        if self.contest_spacing_enabled:
            mode_register |= 0x01
            
        self.current_mode_register = mode_register
        
        if self.send_bytes([0x0E, self.current_mode_register]):
            self.add_log_entry(
                f"    WKMode register: 0x{self.current_mode_register:02X} "
                f"({self.current_mode_register:08b})", 
                "sent"
            )

    def toggle_paddle_swap(self):
        """Toggle paddle swap on the WK3 device"""
        if not self.host_mode_active:
            self.add_log_entry("⚠️ Must be in host mode to change settings", "error")
            return
            
        self.paddle_swapped = not self.paddle_swapped
        self.update_wkmode_register()
        self.add_log_entry(
            f"🔄 Paddle swap: {'ON' if self.paddle_swapped else 'OFF'}", 
            "sent"
        )
        self.update_paddle_swap_display()
            
    def toggle_sidetone(self):
        """Toggle sidetone on the WK3 device"""
        if not self.host_mode_active:
            self.add_log_entry("⚠️ Must be in host mode to change settings", "error")
            return
            
        self.sidetone_enabled = not self.sidetone_enabled
        
        self.update_pin_config()
        self.add_log_entry(
            f"🔊 Sidetone: {'ON' if self.sidetone_enabled else 'OFF'}", 
            "sent"
        )
            
        self.update_sidetone_display()
            
    def update_paddle_swap_display(self):
        """Update the paddle swap button display"""
        self.paddle_swap_btn.setText(
            "Unswap Paddles" if self.paddle_swapped else "Swap Paddles"
        )
        if self.paddle_swapped:
            self.paddle_swap_btn.setStyleSheet(
                "background: linear-gradient(135deg, #f56565 0%, #e53e3e 100%);"
            )
        else:
            self.paddle_swap_btn.setStyleSheet("")
            
    def update_sidetone_display(self):
        """Update the sidetone button display"""
        self.sidetone_btn.setText(
            "Disable Sidetone" if self.sidetone_enabled else "Enable Sidetone"
        )
        if self.sidetone_enabled:
            self.sidetone_btn.setStyleSheet(
                "background: linear-gradient(135deg, #f56565 0%, #e53e3e 100%);"
            )
        else:
            self.sidetone_btn.setStyleSheet("")
            
    def update_pin_config(self):
        """Update and send the PinCFG register based on current settings"""
        # Build the pin config byte from individual settings
        pin_config = 0
        
        # Bits 7,6: Ultimatic Priority (00=Normal, 01=Dah Pri, 10=Dit Pri)
        pin_config |= (self.ultimatic_priority & 0x03) << 6
        
        # Bits 5,4: Hangtime (00=1ws+1dit, 01=1ws+2dit, 10=1ws+4dit, 11=1ws+8dit)
        pin_config |= (self.hangtime_setting & 0x03) << 4
        
        # Bit 3: Key Out 1
        if self.keyout1_enabled:
            pin_config |= 0x08
            
        # Bit 2: Key Out 2
        if self.keyout2_enabled:
            pin_config |= 0x04
            
        # Bit 1: Sidetone
        if self.sidetone_enabled:
            pin_config |= 0x02
            
        # Bit 0: PTT
        if self.ptt_enabled:
            pin_config |= 0x01
            
        self.current_pin_config = pin_config
        
        if self.send_bytes([0x09, self.current_pin_config]):
            self.add_log_entry(
                f"    Pin config: 0x{self.current_pin_config:02X} "
                f"({self.current_pin_config:08b})", 
                "sent"
            )
            
    def set_hangtime(self):
        """Set the hangtime setting"""
        if not self.host_mode_active:
            self.add_log_entry("⚠️ Must be in host mode to change settings", "error")
            return
            
        self.hangtime_setting = self.hangtime_combo.currentIndex()
        self.update_pin_config()
        self.add_log_entry(
            f"⏱️ Hangtime: {self.hangtime_combo.currentText()}", 
            "sent"
        )
        
    def toggle_keyout1(self, state):
        """Toggle Key Out 1"""
        if not self.host_mode_active:
            self.add_log_entry("⚠️ Must be in host mode to change settings", "error")
            self.keyout1_cb.setChecked(self.keyout1_enabled)  # Revert checkbox
            return
            
        from PyQt6.QtCore import Qt
        self.keyout1_enabled = (state == Qt.CheckState.Checked.value)
        self.update_pin_config()
        self.add_log_entry(
            f"🔑 Key Out 1: {'ON' if self.keyout1_enabled else 'OFF'}", 
            "sent"
        )
        
    def toggle_keyout2(self, state):
        """Toggle Key Out 2"""
        if not self.host_mode_active:
            self.add_log_entry("⚠️ Must be in host mode to change settings", "error")
            self.keyout2_cb.setChecked(self.keyout2_enabled)  # Revert checkbox
            return
            
        from PyQt6.QtCore import Qt
        self.keyout2_enabled = (state == Qt.CheckState.Checked.value)
        self.update_pin_config()
        self.add_log_entry(
            f"🔑 Key Out 2: {'ON' if self.keyout2_enabled else 'OFF'}", 
            "sent"
        )
        
    def toggle_ptt(self, state):
        """Toggle PTT enable"""
        if not self.host_mode_active:
            self.add_log_entry("⚠️ Must be in host mode to change settings", "error")
            self.ptt_cb.setChecked(self.ptt_enabled)  # Revert checkbox
            return
            
        from PyQt6.QtCore import Qt
        self.ptt_enabled = (state == Qt.CheckState.Checked.value)
        self.update_pin_config()
        self.add_log_entry(
            f"📡 PTT: {'ON' if self.ptt_enabled else 'OFF'}", 
            "sent"
        )
        
    def toggle_paddle_watchdog(self, state):
        """Toggle paddle watchdog disable"""
        if not self.host_mode_active:
            self.add_log_entry("⚠️ Must be in host mode to change settings", "error")
            self.paddle_watchdog_cb.setChecked(self.paddle_watchdog_disabled)  # Revert checkbox
            return
            
        from PyQt6.QtCore import Qt
        self.paddle_watchdog_disabled = (state == Qt.CheckState.Checked.value)
        self.update_wkmode_register()
        self.add_log_entry(
            f"⏰ Paddle Watchdog: {'DISABLED' if self.paddle_watchdog_disabled else 'ENABLED'}", 
            "sent"
        )
        
    def toggle_paddle_echo(self, state):
        """Toggle paddle echo"""
        if not self.host_mode_active:
            self.add_log_entry("⚠️ Must be in host mode to change settings", "error")
            self.paddle_echo_cb.setChecked(self.paddle_echo_enabled)  # Revert checkbox
            return
            
        from PyQt6.QtCore import Qt
        self.paddle_echo_enabled = (state == Qt.CheckState.Checked.value)
        self.update_wkmode_register()
        self.add_log_entry(
            f"🔊 Paddle Echo: {'ON' if self.paddle_echo_enabled else 'OFF'}", 
            "sent"
        )
        
    def toggle_serial_echo(self, state):
        """Toggle serial echo back"""
        if not self.host_mode_active:
            self.add_log_entry("⚠️ Must be in host mode to change settings", "error")
            self.serial_echo_cb.setChecked(self.serial_echo_enabled)  # Revert checkbox
            return
            
        from PyQt6.QtCore import Qt
        self.serial_echo_enabled = (state == Qt.CheckState.Checked.value)
        self.update_wkmode_register()
        self.add_log_entry(
            f"📡 Serial Echo Back: {'ON' if self.serial_echo_enabled else 'OFF'}", 
            "sent"
        )
        
    def toggle_autospace(self, state):
        """Toggle autospace"""
        if not self.host_mode_active:
            self.add_log_entry("⚠️ Must be in host mode to change settings", "error")
            self.autospace_cb.setChecked(self.autospace_enabled)  # Revert checkbox
            return
            
        from PyQt6.QtCore import Qt
        self.autospace_enabled = (state == Qt.CheckState.Checked.value)
        self.update_wkmode_register()
        self.add_log_entry(
            f"📝 Autospace: {'ON' if self.autospace_enabled else 'OFF'}", 
            "sent"
        )
        
    def toggle_contest_spacing(self, state):
        """Toggle contest spacing"""
        if not self.host_mode_active:
            self.add_log_entry("⚠️ Must be in host mode to change settings", "error")
            self.contest_spacing_cb.setChecked(self.contest_spacing_enabled)  # Revert checkbox
            return
            
        from PyQt6.QtCore import Qt
        self.contest_spacing_enabled = (state == Qt.CheckState.Checked.value)
        self.update_wkmode_register()
        self.add_log_entry(
            f"🏆 Contest Spacing: {'ON' if self.contest_spacing_enabled else 'OFF'}", 
            "sent"
        )
            
    def send_command(self):
        """Send a raw command to the WK3 device"""
        command = self.command_input.text().strip()
        if not command:
            return
            
        try:
            # Parse hex string to bytes
            bytes_to_send = self.hex_string_to_bytes(command)
            if self.send_bytes(bytes_to_send):
                self.command_input.clear()
        except Exception as e:
            self.add_log_entry(f"❌ Send error: {str(e)}", "error")
            
    def send_bytes(self, bytes_to_send):
        """Send bytes to the WK3 device"""
        if not self.serial_thread:
            self.add_log_entry("❌ Not connected to device", "error")
            return False
            
        try:
            # Store the last command bytes for tracking responses
            if len(bytes_to_send) >= 2 and bytes_to_send[0] == 0x00:
                self.last_command_byte = bytes_to_send[1]  # Store the Admin command code
                self.add_log_entry(
                    f"    Tracking Admin command: 0x{bytes_to_send[1]:02X}", 
                    "sent"
                )
            elif len(bytes_to_send) > 0:
                self.last_command_byte = bytes_to_send[-1]
                
            # Convert to hex for display
            hex_str = ' '.join([f"{b:02X}" for b in bytes_to_send])
            self.add_log_entry(f"📤 Sent: {hex_str}", "sent")
            
            # Try to interpret as text if printable
            try:
                text = bytes(bytes_to_send).decode('ascii')
                if text.isprintable():
                    self.add_log_entry(f"    Text: \"{text}\"", "sent")
            except:
                pass  # Not valid ASCII
                
            # Send the bytes
            return self.serial_thread.send_data(bytes(bytes_to_send))
            
        except Exception as e:
            self.add_log_entry(f"❌ Send error: {str(e)}", "error")
            return False
            
    def hex_string_to_bytes(self, hex_string):
        """Convert a hex string to bytes"""
        # Remove spaces and validate
        hex_string = hex_string.replace(" ", "")
        if not all(c in "0123456789ABCDEFabcdef" for c in hex_string):
            raise ValueError("Invalid hex string. Use only 0-9 and A-F.")
        if len(hex_string) % 2 != 0:
            raise ValueError("Hex string must have even length.")
            
        # Convert to bytes
        return [int(hex_string[i:i+2], 16) for i in range(0, len(hex_string), 2)]
            
    def add_log_entry(self, message, entry_type="", style=""):
        """Add an entry to the log display"""
        from PyQt6.QtCore import QTime
        
        # Create HTML for the log entry
        time_str = QTime.currentTime().toString("hh:mm:ss")
        html = f'<div class="log-entry log-{entry_type}">'
        html += f'<span style="color: #a0aec0;">[{time_str}]</span> '
        
        if style:
            html += f'<span style="{style}">{message}</span>'
        else:
            html += message
            
        html += '</div>'
        
        # Add to the log
        self.log_display.append(html)
        
    def clear_log(self):
        """Clear the log display"""
        self.log_display.clear()
        self.add_log_entry("Log cleared...")
        
    def test_wk3(self):
        """Run a basic test sequence on the WK3 device"""
        self.clear_log()
        self.add_log_entry("🧪 Starting minimal WK3 test sequence", "sent")
        
        # Use a QTimer to sequence the commands with delays
        QTimer.singleShot(0, lambda: self.send_bytes([0x00, 0x01]) and 
                         self.add_log_entry("🔄 Reset sent (Admin:Reset)", "sent"))
        
        QTimer.singleShot(1000, lambda: self.send_bytes([0x00, 0x02]) and 
                         self.add_log_entry("🔓 Admin:Open sent", "sent"))
        
        QTimer.singleShot(2000, lambda: self.send_bytes([0x00, 0x04]) and 
                         self.add_log_entry("🔤 Echo Test command sent", "sent"))
        
        QTimer.singleShot(2100, lambda: self.send_bytes([0x41]) and  # ASCII 'A'
                         self.add_log_entry("    Sending 'A' for echo test", "sent"))


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = WK3Interface()
    window.show()
    sys.exit(app.exec())
