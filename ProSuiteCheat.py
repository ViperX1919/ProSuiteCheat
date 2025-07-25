import sys
import os
import threading
import time
import json
import math
import logging
import ctypes

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtWidgets import (QApplication, QColorDialog, QComboBox, QFileDialog, QFrame,
                               QListWidget, QListWidgetItem, QLabel, QWidget, QHBoxLayout, QVBoxLayout,
                               QSlider, QPushButton, QLayout)
from PySide6.QtGui import (QPixmap, QPainter, QColor, QIcon, QCursor, QPolygonF)
from PySide6.QtCore import (Qt, QPoint, QRect, QEvent, QSize, QPropertyAnimation, QEasingCurve, QPointF)

# Type hints for Qt constants to help the linter
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLayout

import numpy as np
import mss
import cv2
from pynput import mouse, keyboard


# --- Global Style ---
NEW_RED = "#881122"
SIDEBAR_COLOR = "#111111"
CONTENT_BOX_COLOR = "#111111"
SELECTION_GRAY = "#2C2C2C"
MAIN_BACKGROUND = "#1A1A1A"
LIGHT_SIDEBAR_COLOR = "#F0F0F0"
LIGHT_CONTENT_BOX_COLOR = "#F0F0F0"
LIGHT_SELECTION_GRAY = "#DCDCDC"
LIGHT_MAIN_BACKGROUND = "#FFFFFF"


# --- Configuration & Platform Specifics ---
log_format = '%(asctime)s - %(levelname)s - %(message)s'
logging.basicConfig(level=logging.INFO, format=log_format, handlers=[logging.FileHandler("cheat_log.txt"), logging.StreamHandler()])

if sys.platform != "win32":
    logging.error("This script uses Windows-specific APIs for raw mouse input and is not cross-platform.")

PUL = ctypes.POINTER(ctypes.c_ulong)

class MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", PUL)
    ]

class Input_I(ctypes.Union):
    _fields_ = [("mi", MouseInput)]

class Input(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_ulong),
        ("ii", Input_I)
    ]

# --- Helper function for portable asset paths ---
def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS  # type: ignore
    except Exception:
        # Running as script, use the directory of the script
        base_path = os.path.dirname(os.path.abspath(__file__))
    
    # Handle both cases: assets folder and direct file
    asset_path = os.path.join(base_path, "assets", relative_path)
    if os.path.exists(asset_path):
        return asset_path
    
    # Fallback: try direct path (for when assets are in root)
    direct_path = os.path.join(base_path, relative_path)
    if os.path.exists(direct_path):
        return direct_path
    
    # Final fallback: return the expected path and let the caller handle missing files
    return asset_path

# --- Core Logic Classes ---
class SmoothAimer:
    def __init__(self, smoothness=10.0, speed=1.0):
        self.smoothness = smoothness
        self.speed = speed

    def _send_input(self, c):
        if sys.platform == "win32":
            ctypes.windll.user32.SendInput(1, ctypes.pointer(c), ctypes.sizeof(c))

    def move(self, dx, dy):
        mx = (dx / self.smoothness) * (self.speed / 10.0)
        my = (dy / self.smoothness) * (self.speed / 10.0)
        if abs(mx) > 0.5 or abs(my) > 0.5:
            e = ctypes.c_ulong(0)
            i = Input_I()
            i.mi = MouseInput(int(mx), int(my), 0, 1, 0, ctypes.pointer(e))
            c = Input(ctypes.c_ulong(0), i)
            self._send_input(c)

    def click(self):
        e = ctypes.c_ulong(0)
        i = Input_I()
        i.mi = MouseInput(0, 0, 0, 2, 0, ctypes.pointer(e))
        c_d = Input(ctypes.c_ulong(0), i)
        self._send_input(c_d)
        time.sleep(0.01)
        i.mi = MouseInput(0, 0, 0, 4, 0, ctypes.pointer(e))
        c_u = Input(ctypes.c_ulong(0), i)
        self._send_input(c_u)

class KalmanFilter:
    """Simple 2D Kalman filter for target prediction"""
    def __init__(self):
        # State: [x, y, vx, vy] (position and velocity)
        self.state = np.array([0., 0., 0., 0.])
        
        # State transition matrix (constant velocity model)
        self.F = np.array([
            [1, 0, 1, 0],  # x = x + vx
            [0, 1, 0, 1],  # y = y + vy
            [0, 0, 1, 0],  # vx = vx (constant)
            [0, 0, 0, 1]   # vy = vy (constant)
        ])
        
        # Measurement matrix (we only measure position)
        self.H = np.array([
            [1, 0, 0, 0],
            [0, 1, 0, 0]
        ])
        
        # Process noise covariance (higher for more responsive tracking)
        self.Q = np.eye(4) * 0.2
        
        # Measurement noise covariance (lower for more trust in measurements)
        self.R = np.eye(2) * 0.2
        
        # State covariance
        self.P = np.eye(4) * 2.0
        
        self.initialized = False
    
    def predict(self, dt=1.0):
        """Predict next state"""
        if not self.initialized:
            return self.state[:2]
        
        # Update state transition matrix for dt
        F_dt = np.array([
            [1, 0, dt, 0],  # x = x + vx*dt
            [0, 1, 0, dt],  # y = y + vy*dt
            [0, 0, 1, 0],   # vx = vx
            [0, 0, 0, 1]    # vy = vy
        ])
        
        # Predict state
        self.state = F_dt @ self.state
        
        # Predict covariance
        self.P = F_dt @ self.P @ F_dt.T + self.Q
        
        return self.state[:2]
    
    def update(self, measurement):
        """Update with new measurement"""
        if not self.initialized:
            self.state[:2] = measurement
            self.initialized = True
            self.last_measurement = measurement.copy()
            return
        
        # Calculate velocity from position change for better initialization
        if hasattr(self, 'last_measurement'):
            velocity = measurement - self.last_measurement
            
            # For 3D FPS games, we need to be more careful about velocity estimation
            # Only update velocity if the movement is significant enough
            velocity_mag = np.linalg.norm(velocity)
            if velocity_mag > 0.5:  # Only update for significant movements
                # Use velocity to improve initial velocity estimate
                if not hasattr(self, 'velocity_initialized'):
                    self.state[2:4] = velocity
                    self.velocity_initialized = True
                else:
                    # Smooth velocity updates for more stable prediction
                    self.state[2:4] = self.state[2:4] * 0.8 + velocity * 0.2
        
        self.last_measurement = measurement.copy()
        
        # Kalman gain - use solve instead of inv for better performance
        S = self.H @ self.P @ self.H.T + self.R
        K = self.P @ self.H.T @ np.linalg.solve(S, np.eye(S.shape[0]))
        
        # Update state
        y = measurement - self.H @ self.state
        self.state = self.state + K @ y
        
        # Update covariance
        I = np.eye(4)
        self.P = (I - K @ self.H) @ self.P
    
    def get_velocity(self):
        """Get current velocity estimate"""
        return self.state[2:4] if self.initialized else np.array([0., 0.])
    
    def get_prediction_confidence(self):
        """Get prediction confidence based on covariance"""
        if not self.initialized:
            return 0.0
        # Use the trace of position covariance as confidence measure
        return 1.0 / (1.0 + np.trace(self.P[:2, :2]))
    
    def reset(self):
        """Reset the filter"""
        self.state = np.array([0., 0., 0., 0.])
        self.P = np.eye(4) * 2.0
        self.initialized = False
        if hasattr(self, 'last_measurement'):
            delattr(self, 'last_measurement')
        if hasattr(self, 'velocity_initialized'):
            delattr(self, 'velocity_initialized')
    
    def handle_target_loss(self):
        """Handle target loss by increasing uncertainty"""
        if self.initialized:
            # Increase position uncertainty when target is lost
            self.P[:2, :2] *= 2.0

class InputListener(QtCore.QObject, threading.Thread):
    input_pressed_signal = QtCore.Signal(str, bool)
    input_set_signal = QtCore.Signal(str, str)
    
    def __init__(self):
        super().__init__()
        self.daemon = True
        self.keyboard_listener = None
        self.mouse_listener = None
        self._is_listening_for_bind = False
        self._current_hack_to_bind = None
        self.keybinds = {}

    def run(self):
        self.keyboard_listener = keyboard.Listener(on_press=self.on_press, on_release=self.on_release)
        self.mouse_listener = mouse.Listener(on_click=self.on_click)
        self.keyboard_listener.start()
        self.mouse_listener.start()
        self.keyboard_listener.join()
        self.mouse_listener.join()

    def _get_key_str(self, key):
        if hasattr(key, 'char') and key.char:
            return key.char.upper()
        return key.name.upper()

    def _get_button_str(self, button):
        return f"{button.name.upper()} CLICK"
    
    def _handle_input(self, input_str, pressed):
        if self._is_listening_for_bind and pressed and self._current_hack_to_bind:
            key_to_set = input_str
            self.input_set_signal.emit(self._current_hack_to_bind, key_to_set)
            
            for hack, key in list(self.keybinds.items()):
                if key == key_to_set and hack != self._current_hack_to_bind:
                    self.keybinds.pop(hack, None)
                    self.input_set_signal.emit(hack, "Not Set")

            self.keybinds[self._current_hack_to_bind] = key_to_set
            self._is_listening_for_bind = False
            self._current_hack_to_bind = None
            return

        for hack_name, key in self.keybinds.items():
            if key == input_str:
                self.input_pressed_signal.emit(hack_name, pressed)

    def on_press(self, key):
        self._handle_input(self._get_key_str(key), True)

    def on_release(self, key):
        self._handle_input(self._get_key_str(key), False)

    def on_click(self, x, y, button, pressed):
        if pressed: # Only handle press events for clicks
            self._handle_input(self._get_button_str(button), True)
        else:
            self._handle_input(self._get_button_str(button), False)


    def set_new_bind(self, hack_name):
        self._is_listening_for_bind = True
        self._current_hack_to_bind = hack_name

    def stop(self):
        if self.keyboard_listener:
            self.keyboard_listener.stop()
        if self.mouse_listener:
            self.mouse_listener.stop()

# --- GUI Classes ---
class FOVOverlay(QtWidgets.QWidget):
    def __init__(self, color=QtGui.QColor(NEW_RED)):
        super().__init__()
        self.radius = 100
        self.color = color
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

    def set_fov_radius(self, radius):
        self.radius = radius
        self.update()

    def set_color(self, color):
        self.color = color
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        try:
            # Get current screen center dynamically
            center = QtGui.QGuiApplication.primaryScreen().geometry().center()
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            painter.setPen(QtGui.QPen(self.color, 2))
            painter.drawEllipse(center, self.radius, self.radius)
        finally:
            painter.end()

class RenderOverlay(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.render_data = None
        self.opacity = 100
        self.glow = False
        self.tracers = False
        self.color = QtGui.QColor(NEW_RED)
        self.esp_mode = "Box"

    def update_render_data(self, data):
        self.render_data = data
        self.update()

    def clear_render_data(self):
        self.render_data = None
        self.update()

    def paintEvent(self, event):
        if not self.render_data:
            return
            
        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            offset = self.render_data.get('offset', QPoint(0, 0))
            
            if self.tracers and 'screen_pos' in self.render_data and self.render_data['screen_pos'] is not None:
                geom = QtGui.QGuiApplication.primaryScreen().geometry()
                start = QPoint(geom.width() // 2, geom.height())
                painter.setPen(QtGui.QPen(self.color, 1))
                painter.drawLine(start, self.render_data['screen_pos'])
                
            painter.save()
            painter.translate(offset)
            
            if self.glow:
                painter.setBrush(Qt.NoBrush)
                for i in range(5):
                    gc = QColor(self.color)
                    gc.setAlpha(int(self.opacity / ((i + 1) * 2.5)))
                    painter.setPen(QtGui.QPen(gc, 2 + (i * 2), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                    if 'bbox' in self.render_data:
                        x,y,w,h = self.render_data['bbox']
                        bbox = QtCore.QRectF(x,y,w,h)
                        painter.drawRoundedRect(bbox.adjusted(-i * 2, -i * 2, i * 2, i * 2), 5, 5)

            painter.setPen(QtGui.QPen(self.color, 2))
            painter.setBrush(Qt.NoBrush)

            if 'bbox' in self.render_data:
                x, y, w, h = self.render_data['bbox']
                bbox = QtCore.QRectF(x, y, w, h)
                
                if self.esp_mode == "Box":
                    painter.drawRect(bbox)
                elif self.esp_mode == "Corner Box":
                    corner_size = min(w, h) / 4
                    painter.drawLine(bbox.topLeft(), bbox.topLeft() + QPoint(corner_size, 0))
                    painter.drawLine(bbox.topLeft(), bbox.topLeft() + QPoint(0, corner_size))
                    painter.drawLine(bbox.topRight(), bbox.topRight() - QPoint(corner_size, 0))
                    painter.drawLine(bbox.topRight(), bbox.topRight() + QPoint(0, corner_size))
                    painter.drawLine(bbox.bottomLeft(), bbox.bottomLeft() + QPoint(corner_size, 0))
                    painter.drawLine(bbox.bottomLeft(), bbox.bottomLeft() - QPoint(0, corner_size))
                    painter.drawLine(bbox.bottomRight(), bbox.bottomRight() - QPoint(corner_size, 0))
                    painter.drawLine(bbox.bottomRight(), bbox.bottomRight() - QPoint(0, corner_size))
                elif self.esp_mode == "Head Circle":
                    head_pos = QPoint(int(x + w / 2), int(y))
                    radius = w / 4
                    painter.drawEllipse(head_pos, radius, radius)
                    
            elif 'px_mask' in self.render_data and self.esp_mode == "Pixel":
                mask = self.render_data['px_mask']
                h, w = mask.shape
                img = QtGui.QImage(w, h, QtGui.QImage.Format_ARGB32)
                img.fill(Qt.transparent)
                color = QColor(self.color)
                color.setAlpha(self.opacity)
                
                ptr = img.bits()
                arr = np.frombuffer(ptr, dtype=np.uint32).reshape((h, w))
                arr[mask > 0] = color.rgba()
                painter.drawImage(QPoint(0, 0), img)
                
            painter.restore()
        finally:
            painter.end()

class AnimatedArrow(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(15, 15)
        self._rotation = 0
        self.color = QColor("#FFFFFF")

    @QtCore.Property(float)
    def rotation(self):
        return self._rotation

    @rotation.setter
    def rotation(self, value):
        self._rotation = value
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(self.color)
        
        center = QPointF(self.width() / 2, self.height() / 2)
        
        painter.translate(center)
        painter.rotate(self._rotation)
        painter.translate(-center)

        arrow_poly = QPolygonF([ QPointF(5, 5), QPointF(10, 7.5), QPointF(5, 10)])
        painter.drawPolygon(arrow_poly)

class CollapsibleSection(QWidget):
    def __init__(self, title_widget, header_controls=None, parent=None):
        super().__init__(parent)
        self.title_label = title_widget.findChild(QLabel)
        self.setObjectName(self.title_label.text().replace(" ", ""))

        self.arrow = AnimatedArrow()
        self.arrow_animation = QPropertyAnimation(self.arrow, b"rotation")
        self.arrow_animation.setDuration(150)
        self.arrow_animation.setEasingCurve(QEasingCurve.InOutQuad)

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setSpacing(0)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        
        self.header_widget = QWidget()
        self.header_widget.setCursor(QCursor(Qt.PointingHandCursor))
        header_layout = QHBoxLayout(self.header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.addWidget(self.arrow)
        header_layout.addWidget(title_widget, 1)
        
        if header_controls:
            for control in header_controls:
                header_layout.addWidget(control)

        self.content_widget = QWidget()
        self.content_widget.setStyleSheet("background-color: transparent; border-left: 2px solid #2C2C2C; margin-left: 7px; padding-left: 10px; padding-top: 10px;")
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(10, 5, 5, 10)
        self.content_layout.setSpacing(10)

        self.main_layout.addWidget(self.header_widget)
        self.main_layout.addWidget(self.content_widget)
        
        self.header_widget.mouseReleaseEvent = self.header_clicked
        self.content_widget.hide()
        self._is_collapsed = True

    def add_widget(self, widget):
        self.content_layout.addWidget(widget)

    def set_collapsed(self, collapsed):
        if self._is_collapsed == collapsed: return
        self._is_collapsed = collapsed

        if collapsed:
            self.arrow_animation.setEndValue(0)
            self.content_widget.hide()
        else:
            self.arrow_animation.setEndValue(90)
            self.content_widget.show()
        
        self.arrow_animation.start()
        QtCore.QTimer.singleShot(0, self.parentWidget().layout().activate)

    def header_clicked(self, event):
        if event.button() == Qt.LeftButton:
            toggle_switch = self.header_widget.findChild(ToggleSwitch)
            if toggle_switch:
                mapped_pos = toggle_switch.mapFrom(self.header_widget, event.position().toPoint())
                if not toggle_switch.rect().contains(mapped_pos):
                    self.set_collapsed(not self._is_collapsed)
            else:
                self.set_collapsed(not self._is_collapsed)

    def is_collapsed(self):
        return self._is_collapsed

# --- Helper Functions ---
def create_toggle(text, theme_color):
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0,0,0,0)
    l = QLabel(text)
    t = ToggleSwitch(theme_color)
    h.addWidget(l, 1)
    h.addWidget(t)
    return w, t, l

def create_slider(text, min_val, max_val, val):
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0,0,0,0)
    l = QLabel(text)
    s = QSlider(Qt.Horizontal)
    s.setRange(min_val, max_val)
    s.setValue(val)
    v = QLabel(str(val))
    if "Y Offset" in text:
        s.valueChanged.connect(lambda value, lbl=v: lbl.setText(f"{value:+}"))
        v.setText(f"{val:+}")
    else:
        s.valueChanged.connect(lambda value, lbl=v: lbl.setText(str(value / 10.0 if "Factor" in text else value)))
        v.setText(str(val / 10.0 if "Factor" in text else val))

    h.addWidget(l)
    h.addWidget(s, 1)
    h.addWidget(v)
    return w, s, l, v

def create_color_picker(text):
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0,0,0,0)
    l = QLabel(text)
    b = QPushButton()
    b.setFixedSize(30, 30)
    b.setStyleSheet("background-color:#FF0000;border-radius:5px;")
    h.addWidget(l, 1)
    h.addWidget(b)
    return w, b, l

def create_dropdown(text, items):
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0,0,0,0)
    l = QLabel(text)
    d = QComboBox()
    d.addItems(items)
    h.addWidget(l, 1)
    h.addWidget(d)
    return w, d, l

def create_keybind_button(default_text):
    w = QWidget()
    w.setContentsMargins(0,0,0,0)
    h = QHBoxLayout(w)
    h.setContentsMargins(5,0,0,0)
    b = QPushButton(default_text)
    b.setFixedWidth(110)
    b.setStyleSheet("background:#333;color:#FFF;border:1px solid #555;border-radius:3px;padding:5px;")
    h.addWidget(b)
    return w, b


class AnimatedStackedWidget(QtWidgets.QStackedWidget):
    def __init__(self, p=None):
        super().__init__(p)
        self._a = QPropertyAnimation(self, b"pos", self)
        self._a.setDuration(300)
        self._a.setEasingCurve(QEasingCurve.InOutQuad)

    def setCurrentIndex(self, i):
        if self.currentIndex() == i or self._a.state() == QPropertyAnimation.Running: return
        o, n = self.currentWidget(), self.widget(i)
        if o != n:
            n.move(self.width(), 0)
            super().setCurrentIndex(i)
            self._a.setTargetObject(n)
            self._a.setStartValue(n.pos())
            self._a.setEndValue(QPoint(0, 0))
            self._a.start()

class CheatMenuPage(QWidget):
    def __init__(self, t, p=None):
        super().__init__(p)
        self.sections = []
        self.themeable_labels = []
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)

        self.content_box = QWidget()
        self.box_layout = QVBoxLayout(self.content_box)
        self.box_layout.setContentsMargins(20, 30, 20, 20) 

        self.lbl = QLabel(t)
        self.lbl.setStyleSheet("font-size:24px; background: transparent;")
        self.box_layout.addWidget(self.lbl)
        self.box_layout.addSpacing(10)

        main_layout.addWidget(self.content_box)
        self.box_layout.addStretch()

    def add_widget(self, widget, add_spacer=True):
        self.box_layout.insertWidget(self.box_layout.count() - 1, widget)
        if add_spacer:
            self.box_layout.insertSpacing(self.box_layout.count() - 1, 10)

    def add_separator(self):
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        self.add_widget(line)

    def add_section(self, section):
        self.sections.append(section)
        self.add_widget(section)
    
    def add_themeable_label(self, label_text):
        label = QLabel(label_text)
        label.setStyleSheet("font-weight: bold;")
        self.themeable_labels.append(label)
        self.add_widget(label, add_spacer=False)
        return label

    def update_theme(self, content_color, text_color, border_color):
        self.content_box.setStyleSheet(f"background-color: {content_color}; border-radius: 15px;")
        self.lbl.setStyleSheet(f"color:{text_color};font-size:24px; background: transparent;")
        
        for label in self.themeable_labels:
            label.setStyleSheet(f"color:{text_color}; font-weight: bold;")

        for section in self.sections:
            section.content_widget.setStyleSheet(f"background-color: transparent; border-left: 2px solid {border_color}; margin-left: 7px; padding-left: 10px; padding-top: 10px;")
            section.arrow.color = QColor(text_color)
            if section.title_label:
                section.title_label.setStyleSheet(f"color:{text_color};")
            for label in section.content_widget.findChildren(QLabel):
                 if not label.objectName() == "value_label":
                    label.setStyleSheet(f"color:{text_color};")


class ToggleSwitch(QWidget):
    toggled = QtCore.Signal(bool)

    def __init__(self, theme_color, p=None):
        super().__init__(p)
        self.setCursor(Qt.PointingHandCursor)
        self._c = False
        self._p = 0.0
        self._a = QPropertyAnimation(self, b"p", self)
        self._a.setDuration(200)
        self._a.setEasingCurve(QEasingCurve.InOutCubic)
        self.setFixedSize(50, 28)
        self.theme_color = theme_color

    def mousePressEvent(self, e):
        self.setChecked(not self._c)

    def setChecked(self, c):
        if self._c != c:
            self._c = c
            self.toggled.emit(c)
            self._a.setStartValue(self.p)
            self._a.setEndValue(self.width() - self.height() if c else 0)
            self._a.start()

    def isChecked(self):
        return self._c

    def paintEvent(self, e):
        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            r = self.rect()
            painter.setBrush(QColor(self.theme_color) if self._c else QColor("#555"))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(r, r.height() / 2, r.height() / 2)
            c = QtCore.QRectF(self._p, 2, r.height() - 4, r.height() - 4)
            painter.setBrush(QColor("#FFF"))
            painter.drawEllipse(c)
        finally:
            painter.end()

    @QtCore.Property(float)
    def p(self):
        return self._p

    @p.setter
    def p(self, pos):
        self._p = pos
        self.update()

class IconNav(QWidget):
    ITEM_HEIGHT = 130 

    def __init__(self, theme_color, parent=None):
        super().__init__(parent)
        self.theme_color = theme_color
        self.setFixedWidth(100)
        self.current_index = -1
        self.hovered_item = None
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.selection_background = QWidget(self)
        self.selection_background.setFixedSize(self.width(), self.ITEM_HEIGHT)
        self.selection_background.hide()

        logo = QLabel()
        logo_path = resource_path("Logo.png")
        logo_pixmap = QPixmap(logo_path)
        if not logo_pixmap.isNull():
             logo.setPixmap(logo_pixmap.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            # Create a placeholder if logo is missing
            placeholder = QPixmap(64, 64)
            placeholder.fill(QColor(100, 100, 100))
            logo.setPixmap(placeholder)
        logo.setAlignment(Qt.AlignCenter)
        logo.setContentsMargins(0, 20, 0, 20)
        layout.addWidget(logo)

        self.nav_list = QListWidget()
        self.nav_list.setFocusPolicy(Qt.NoFocus)
        self.nav_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.nav_list.setStyleSheet("QListWidget, QListWidget::item { border: none; background: transparent; padding: 0px; outline: 0; }")
        self.nav_list.setIconSize(QSize(48, 48))
        self.nav_list.setFlow(QListWidget.TopToBottom)
        self.nav_list.setMovement(QListWidget.Static)
        self.nav_list.setResizeMode(QListWidget.Adjust)
        self.nav_list.setUniformItemSizes(True)

        self.item_keys = ["aim", "esp", "settings"]
        self.filenames = {"aim": "Aim.png", "esp": "Render-ESP.png", "settings": "Settings.png"}
        self.icons = {}

        for key in self.item_keys:
            icon_path = resource_path(self.filenames[key])
            pixmap = QPixmap(icon_path)
            if pixmap.isNull(): 
                pixmap = self._create_dummy_pixmap()
                logging.warning(f"Could not load icon: {icon_path}")
            self.icons[key] = { "normal": pixmap, "red": self.colorize(pixmap, self.theme_color.name()) }
            
            item_widget = QWidget()
            item_layout = QHBoxLayout(item_widget)
            item_layout.setContentsMargins(0,0,0,0)
            item_layout.setAlignment(Qt.AlignCenter)
            icon_label = QLabel()
            icon_label.setPixmap(self.icons[key]["normal"])
            item_layout.addWidget(icon_label)
            
            item = QListWidgetItem()
            item.setSizeHint(QSize(self.width(), self.ITEM_HEIGHT))
            self.nav_list.addItem(item)
            self.nav_list.setItemWidget(item, item_widget)
        
        self.nav_list.setFixedHeight(self.ITEM_HEIGHT * len(self.item_keys))
        self.nav_list.setMouseTracking(True)
        self.nav_list.itemEntered.connect(self.on_item_entered)
        self.nav_list.viewport().installEventFilter(self)
        
        layout.addWidget(self.nav_list)
        layout.addStretch()

        self.animation = QPropertyAnimation(self.selection_background, b"pos")
        self.animation.setDuration(250)
        self.animation.setEasingCurve(QEasingCurve.OutCubic)
        
        self.update_all_icon_states()

    def update_theme(self, sidebar_color, selection_color, theme_color):
        self.setStyleSheet(f"background:{sidebar_color};")
        self.selection_background.setStyleSheet(f"background: {selection_color}; border-radius: 8px;")
        self.theme_color = theme_color
        for key in self.item_keys:
            self.icons[key]["red"] = self.colorize(self.icons[key]["normal"], self.theme_color.name())
        self.update_all_icon_states()

    def _create_dummy_pixmap(self):
        pixmap = QPixmap(48, 48)
        pixmap.fill(QColor(80, 80, 80))  # Dark gray instead of magenta
        return pixmap

    def colorize(self, pixmap, color_str):
        if pixmap.isNull(): return QPixmap()
        new_pixmap = pixmap.copy()
        painter = QPainter(new_pixmap)
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(new_pixmap.rect(), QColor(color_str))
        painter.end()
        return new_pixmap

    def update_all_icon_states(self):
        hover_row = self.nav_list.row(self.hovered_item) if self.hovered_item else -1
        for row in range(self.nav_list.count()):
            item = self.nav_list.item(row)
            key = self.item_keys[row]
            icon_label = self.nav_list.itemWidget(item).findChild(QLabel)
            if row == self.current_index or row == hover_row:
                icon_label.setPixmap(self.icons[key]["red"])
            else:
                icon_label.setPixmap(self.icons[key]["normal"])
    
    def on_item_entered(self, item):
        if self.hovered_item != item:
            self.hovered_item = item
            self.update_all_icon_states()

    def eventFilter(self, source, event):
        if source == self.nav_list.viewport() and event.type() == QEvent.Leave:
            self.hovered_item = None
            self.update_all_icon_states()
        return super().eventFilter(source, event)

    def update_selection(self, row):
        is_first_run = self.selection_background.isHidden()
        self.current_index = row
        self.hovered_item = None 
        self.update_all_icon_states()
        
        item = self.nav_list.item(row)
        if not item: return

        rect = self.nav_list.visualItemRect(item)
        target_pos = self.nav_list.mapTo(self, rect.topLeft())

        if self.animation.state() == QPropertyAnimation.Running: self.animation.stop()
        
        if is_first_run:
            self.selection_background.move(target_pos)
        else:
            self.animation.setStartValue(self.selection_background.pos())
            self.animation.setEndValue(target_pos)
            self.animation.start()
            
        self.selection_background.show()
        self.nav_list.raise_()

class PredictionAimOverlay(QtWidgets.QWidget):
    def __init__(self, color=QtGui.QColor(NEW_RED)):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.color = color
        self.velocity_lines = []  # List of (start_pos, end_pos, velocity_magnitude)

    def set_color(self, color):
        self.color = color
        self.update()

    def update_velocity_lines(self, velocity_lines):
        self.velocity_lines = velocity_lines
        self.update()

    def clear_position(self):
        self.velocity_lines = []
        self.update()

    def paintEvent(self, event):
        if not self.velocity_lines:
            return
        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            
            for start_pos, end_pos, velocity_mag in self.velocity_lines:
                # Calculate line color and thickness based on velocity magnitude
                alpha = min(255, int(velocity_mag * 15))  # Scale velocity to alpha
                line_thickness = max(1, min(5, int(velocity_mag / 2)))  # Thicker lines for faster movement
                line_color = QColor(self.color)
                line_color.setAlpha(alpha)
                
                # Draw velocity line with thickness based on speed
                painter.setPen(QtGui.QPen(line_color, line_thickness, Qt.SolidLine))
                painter.drawLine(start_pos, end_pos)
                
                # Draw arrow at the end
                painter.setPen(QtGui.QPen(line_color, line_thickness))
                painter.setBrush(line_color)
                
                # Calculate arrow direction
                dx = end_pos.x() - start_pos.x()
                dy = end_pos.y() - start_pos.y()
                if dx != 0 or dy != 0:
                    angle = math.atan2(dy, dx)
                    arrow_length = max(8, min(15, int(velocity_mag / 2)))  # Bigger arrows for faster movement
                    arrow_angle = math.pi / 6  # 30 degrees
                    
                    # Arrow points
                    arrow_x1 = end_pos.x() - arrow_length * math.cos(angle - arrow_angle)
                    arrow_y1 = end_pos.y() - arrow_length * math.sin(angle - arrow_angle)
                    arrow_x2 = end_pos.x() - arrow_length * math.cos(angle + arrow_angle)
                    arrow_y2 = end_pos.y() - arrow_length * math.sin(angle + arrow_angle)
                    
                    arrow_polygon = QPolygonF([
                        end_pos,
                        QPointF(arrow_x1, arrow_y1),
                        QPointF(arrow_x2, arrow_y2)
                    ])
                    painter.drawPolygon(arrow_polygon)
        finally:
            painter.end()

class RadarWidget(QtWidgets.QWidget):
    def __init__(self, parent=None, radius=150, esp_color=QColor(NEW_RED), main_window=None):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.radius = radius
        self.esp_color = esp_color
        self.main_window = main_window  # Reference to main window for FOV slider
        self.setFixedSize(radius * 2 + 20, radius * 2 + 20)
        self.center = QPoint(self.width() // 2, self.height() // 2)
        self.targets = {}
        self.player_angle = 0.0
        self.move_mode = False
        self.drag_position = QPoint()
        self.setMouseTracking(True)

    def toggle_move_mode(self):
        self.move_mode = not self.move_mode
        self.setWindowFlag(Qt.WindowTransparentForInput, not self.move_mode)
        # Ensure the essential flags are preserved
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.show()
        return self.move_mode

    def mousePressEvent(self, event):
        if self.move_mode and event.button() == Qt.LeftButton:
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.move_mode and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def update_player_angle(self, angle):
        self.player_angle = angle
        self.update()

    def update_targets(self, targets):
        current_time = time.time()
        current_ids = {t['id'] for t in targets}
        
        for t in targets:
            tid = t['id']
            if tid not in self.targets: 
                self.targets[tid] = {}
                self.targets[tid]['first_seen'] = current_time
            self.targets[tid].update({
                'angle': t['angle'], 'distance': t['distance'],
                'visible': t['visible'], 'last_seen': current_time,
                'off_screen': False  # Reset off-screen status when target is detected
            })
        
        # Simple tracking - only keep targets that are currently detected
        for tid in self.targets:
            if tid not in current_ids:
                # Target not detected, will be removed below
                pass
        
        # Remove targets instantly when not detected
        to_remove = [tid for tid, data in self.targets.items() if current_time - data.get('last_seen', 0) > 0]
        for tid in to_remove:
            if tid in self.targets: del self.targets[tid]
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            
            if self.move_mode:
                painter.setPen(QtGui.QPen(self.esp_color, 2, Qt.DashLine))
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(self.rect().adjusted(1, 1, -1, -1))

            painter.setPen(QtGui.QPen(QtGui.QColor(200, 200, 200, 180), 2))
            painter.setBrush(QtGui.QBrush(QtGui.QColor(50, 50, 50, 150)))
            painter.drawEllipse(self.center, self.radius, self.radius)
            
            painter.save()
            painter.translate(self.center)
            painter.rotate(-self.player_angle)
            
            # Calculate FOV cone angle based on the FOV slider's value
            fov_slider = self.main_window.fov_slider
            current_fov = fov_slider.value()
            min_fov = fov_slider.minimum()
            max_fov = fov_slider.maximum()

            # Define the visual angle range for the radar cone
            min_cone_angle = 15.0  # The angle for the smallest FOV
            max_cone_angle = 170.0 # "Almost half" the radar for the largest FOV

            # Linearly interpolate the cone angle based on the FOV slider's value
            fov_range = max_fov - min_fov
            slider_percentage = (current_fov - min_fov) / fov_range if fov_range > 0 else 0
            cone_angle = min_cone_angle + slider_percentage * (max_cone_angle - min_cone_angle)

            # Draw FOV cone (facing forward)
            cone_color = QColor(self.esp_color); cone_color.setAlpha(30)
            painter.setBrush(cone_color); painter.setPen(Qt.NoPen)
            start_angle = int((90 - cone_angle/2) * 16)
            span_angle = int(cone_angle * 16)
            painter.drawPie(QRect(-self.radius, -self.radius, self.radius * 2, self.radius * 2), 
                          start_angle, span_angle)
            
            for tid, data in self.targets.items():
                angle = data.get('angle', 0)
                distance = data.get('distance', 0)
                
                # Only show targets within the FOV cone (left/right only)
                # Ignore up/down movement, only consider horizontal position
                if abs(angle) > cone_angle/2:
                    continue  # Target is outside FOV cone
                    
                angle_rad = math.radians(angle)
                # Calculate distance based on target size (closer = bigger = closer to center)
                target_size = data.get('size', 1.0)  # Default size
                dist_ratio = max(0.1, min(1.0 - (target_size / 100.0), 0.9))  # Closer targets appear closer to center
                x = math.sin(angle_rad) * self.radius * dist_ratio
                y = -math.cos(angle_rad) * self.radius * dist_ratio
                pos = QPointF(x, y)
                # Always use normal color - no grey dots
                color = self.esp_color
                painter.setBrush(color); painter.setPen(Qt.NoPen)
                painter.drawEllipse(pos, 5, 5)
            painter.restore()

            # Draw bigger player arrow
            painter.setBrush(self.esp_color); painter.setPen(Qt.NoPen)
            arrow_size = 15
            painter.drawPolygon(QPolygonF([
                QPointF(self.center.x(), self.center.y() - arrow_size), 
                QPointF(self.center.x() + arrow_size//2, self.center.y() + arrow_size//2), 
                QPointF(self.center.x() - arrow_size//2, self.center.y() + arrow_size//2)
            ]))
        finally:
            painter.end()

# --- Main Application Window ---
class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pro Suite")
        # Try to load window icon, fallback gracefully if missing
        icon_path = resource_path("Logo.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        else:
            logging.warning(f"Could not load window icon: {icon_path}")
        self.resize(1000, 750)
        
        # Get screen resolution for resolution-independent calculations
        self.screen_geometry = QtGui.QGuiApplication.primaryScreen().geometry()
        self.screen_center = self.screen_geometry.center()
        self.screen_width = self.screen_geometry.width()
        self.screen_height = self.screen_geometry.height()
        logging.info(f"Detected screen resolution: {self.screen_width}x{self.screen_height}")
        
        self.theme_color = QColor(NEW_RED)
        self.overlays_shown = False

        main_layout = QHBoxLayout(self)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0,0,0,0)

        self.smoother = SmoothAimer(smoothness=50, speed=10)
        self.input_listener = InputListener()
        self.key_press_states = {}
        self.last_target_pos = None
        self.kalman_filter = KalmanFilter()
        self.priority_mode = "Proximity"
        self.detection_color = QColor(255, 0, 0)
        self.prediction_color = QColor(0, 255, 255)
        self.esp_color = QColor(self.theme_color)
        self.array_list_color = QColor(self.theme_color)

        self.fov_overlay = FOVOverlay(self.esp_color)
        self.render_overlay = RenderOverlay()
        self.array_list = ArrayListWidget(self)
        self.prediction_aim_overlay = PredictionAimOverlay(self.prediction_color)
        self.radar_widget = RadarWidget(esp_color=self.esp_color, main_window=self)
        self.radar_widget.hide()
        
        self.nav = IconNav(self.theme_color)
        self.stack = AnimatedStackedWidget()
        
        self.pages = {}
        self.sections = []
        self.hack_widgets = {}
        self.all_controllable_widgets = []

        self.setup_pages()
        main_layout.addWidget(self.nav)
        main_layout.addWidget(self.stack, 1)
        self.setup_connections()
        
        # Connect FOV slider to radar updates
        self.fov_slider.valueChanged.connect(self.radar_widget.update)
        
        self.input_listener.start()
        self.scan_timer = QtCore.QTimer(self)
        self.scan_timer.timeout.connect(self.run_scan_and_aim)
        self.scan_timer.start(1)
        
        self.update_ui_theme("Dark")
        QtCore.QTimer.singleShot(0, lambda: self.nav.nav_list.setCurrentRow(0))
        
        # Ensure arraylist visibility is set correctly on startup
        QtCore.QTimer.singleShot(100, lambda: self.on_arraylist_toggled(self.arraylist_toggle.isChecked()))
        
        # Update FOV range and set default FOV after a short delay to ensure screen info is available
        QtCore.QTimer.singleShot(200, lambda: (self.update_fov_range(), self.set_default_fov()))
        
        # Connect to screen change events
        app = QApplication.instance()
        if app:
            app.primaryScreenChanged.connect(self.on_screen_changed)

    def get_screen_info(self):
        """Get current screen resolution and center - updates dynamically"""
        self.screen_geometry = QtGui.QGuiApplication.primaryScreen().geometry()
        self.screen_center = self.screen_geometry.center()
        self.screen_width = self.screen_geometry.width()
        self.screen_height = self.screen_geometry.height()
        logging.debug(f"Current screen info: {self.screen_width}x{self.screen_height}, center: ({self.screen_center.x()}, {self.screen_center.y()})")
        return self.screen_center, self.screen_width, self.screen_height

    def get_fov_center(self):
        """Get the center of the FOV area for resolution-independent calculations"""
        center, width, height = self.get_screen_info()
        fov = self.fov_slider.value()
        return fov, fov  # FOV center is always (fov, fov) in the captured image
    
    def update_fov_range(self):
        """Update FOV slider range based on current screen resolution"""
        center, width, height = self.get_screen_info()
        min_dimension = min(width, height)
        max_fov = min_dimension // 2  # Maximum FOV is 1/2 of the smaller screen dimension (much bigger!)
        min_fov = 50  # Minimum FOV (much larger minimum)
        
        current_fov = self.fov_slider.value()
        self.fov_slider.setRange(min_fov, max_fov)
        
        # Adjust current FOV if it's outside the new range
        if current_fov > max_fov:
            self.fov_slider.setValue(max_fov)
        elif current_fov < min_fov:
            self.fov_slider.setValue(min_fov)
    
    def set_default_fov(self):
        """Set a reasonable default FOV based on screen resolution"""
        center, width, height = self.get_screen_info()
        min_dimension = min(width, height)
        # Set FOV to about 1/6 of the smaller screen dimension (larger default)
        default_fov = min_dimension // 6
        # Ensure it's within the current range
        min_fov = self.fov_slider.minimum()
        max_fov = self.fov_slider.maximum()
        default_fov = max(min_fov, min(default_fov, max_fov))
        self.fov_slider.setValue(default_fov)
    
    def on_screen_changed(self):
        """Handle screen resolution changes"""
        # Update screen information
        self.get_screen_info()
        # Update FOV range for new resolution
        self.update_fov_range()
        # Set a reasonable default FOV for the new resolution
        self.set_default_fov()
        # Update overlays if they're visible
        if self.fov_overlay.isVisible():
            self.fov_overlay.update()
        if self.render_overlay.isVisible():
            self.render_overlay.update()
        if self.prediction_aim_overlay.isVisible():
            self.prediction_aim_overlay.update()
        logging.info(f"Screen resolution changed to {self.screen_width}x{self.screen_height}")

    def on_radar_toggled(self, checked):
        if checked: self.radar_widget.show()
        else: self.radar_widget.hide()

    def open_radar_color_dialog(self):
        c = QColorDialog.getColor(self.radar_widget.esp_color, self)
        if c.isValid():
            self.radar_widget.esp_color = c
            self.style_color_button(self.radar_color_button, c)
            self.radar_widget.update()

    def on_show_fov_toggled(self, checked):
        if checked: self.fov_overlay.showFullScreen()
        else: self.fov_overlay.hide()

    def showEvent(self, event):
        super().showEvent(event)
        # Show overlays for the first time
        if not self.overlays_shown:
            if self.hack_widgets['esp']['toggle'].isChecked(): self.render_overlay.showFullScreen()
            if self.hack_widgets['show_fov']['toggle'].isChecked(): self.fov_overlay.showFullScreen()
            if self.arraylist_toggle.isChecked(): self.array_list.show()
            if self.hack_widgets['radar']['toggle'].isChecked(): self.radar_widget.show()
            self.overlays_shown = True

    def create_and_store_toggle(self, text):
        toggle_widget, toggle_switch, label = create_toggle(text, self.theme_color)
        self.all_controllable_widgets.append({'widget':toggle_widget, 'controls':[toggle_switch, label], 'type':'toggle'})
        return toggle_widget, toggle_switch

    def create_and_store_slider(self, text, min_val, max_val, val):
        slider_widget, slider, label, value_label = create_slider(text, min_val, max_val, val)
        value_label.setObjectName("value_label")
        self.style_slider(slider)
        self.all_controllable_widgets.append({'widget':slider_widget, 'controls':[slider, label, value_label], 'type':'slider'})
        return slider_widget, slider
    
    def create_and_store_color_picker(self, text, initial_color_str="#FF0000"):
        picker_widget, button, label = create_color_picker(text)
        self.style_color_button(button, QColor(initial_color_str))
        self.all_controllable_widgets.append({'widget':picker_widget, 'controls':[button, label], 'type':'picker'})
        return picker_widget, button

    def create_and_store_dropdown(self, text, items):
        dd_widget, dropdown, label = create_dropdown(text, items)
        self.all_controllable_widgets.append({'widget':dd_widget, 'controls':[dropdown, label], 'type':'dropdown'})
        return dd_widget, dropdown

    def create_and_store_keybind_button(self, default_text):
        kb_widget, button = create_keybind_button(default_text)
        self.all_controllable_widgets.append({'widget':kb_widget, 'controls':[button], 'type':'keybind'})
        return kb_widget, button

    def style_color_button(self, button, color):
        """Helper method to style color buttons consistently"""
        button.setStyleSheet(f"background-color:{color.name()};border-radius:5px;")
    
    def style_slider(self, slider):
        """Helper method to style sliders consistently"""
        slider.setStyleSheet(f"QSlider::groove:horizontal{{height:6px;background:#333;border-radius:3px}}QSlider::handle:horizontal{{width:12px;background:{self.theme_color.name()};margin:-4px 0;border-radius:6px}}")
    
    def apply_slider_setting(self, slider, settings, key, default):
        """Helper method to apply slider settings safely"""
        slider.setValue(settings.get(key, default))
    
    def apply_dropdown_setting(self, dropdown, settings, key, default):
        """Helper method to apply dropdown settings safely"""
        dropdown.setCurrentText(settings.get(key, default))

    def setup_pages(self):
        # --- Aim Page ---
        self.pages["aim"] = CheatMenuPage("Aimbot")
        
        aim_toggle_w, aim_toggle_s = self.create_and_store_toggle("Enable Smooth Aim")
        aim_hold_key_w, aim_hold_key_b = self.create_and_store_keybind_button("Set Hold Key")
        self.hack_widgets['aim'] = {'toggle': aim_toggle_s, 'display_name': "Smooth Aim", 'keybind_button': aim_hold_key_b}
        
        aim_section = CollapsibleSection(aim_toggle_w, header_controls=[aim_hold_key_w])
        aim_toggle_key_w, aim_toggle_key_b = self.create_and_store_keybind_button("Set Toggle Key")
        self.hack_widgets['aim_toggle'] = {'keybind_button': aim_toggle_key_b}
        det_color_w, self.det_color_button = self.create_and_store_color_picker("Detection Color")
        pred_color_w, self.pred_color_button = self.create_and_store_color_picker("Prediction Color", self.prediction_color.name())

        priority_dd, self.priority_dropdown = self.create_and_store_dropdown("Aim Priority", ["Proximity", "Size"])
        aim_pos_w, self.aim_pos_dropdown = self.create_and_store_dropdown("Aiming Position", ["Body", "Head", "Custom"])
        self.y_offset_widget, self.y_offset_slider = self.create_and_store_slider("Y Offset", -50, 50, 0)
        self.y_offset_widget.hide()
        tolerance_w, self.tolerance_slider = self.create_and_store_slider("Color Tolerance", 0, 255, 80)
        smooth_w, self.smooth_slider = self.create_and_store_slider("Smoothness", 1, 100, 50)
        speed_w, self.speed_slider = self.create_and_store_slider("Speed", 1, 100, 10)
        predict_toggle_w, predict_toggle_s = self.create_and_store_toggle("Prediction Aim")
        predict_factor_w, self.predict_slider = self.create_and_store_slider("Prediction Factor", 1, 50, 5)
        prediction_visual_toggle_w, prediction_visual_toggle_s = self.create_and_store_toggle("Show Prediction Aim Visual")
        self.hack_widgets['prediction'] = {'toggle': predict_toggle_s, 'display_name': "Prediction"}
        self.hack_widgets['prediction_visual'] = {'toggle': prediction_visual_toggle_s, 'display_name': "Prediction Visual"}
        
        aim_section.add_widget(aim_toggle_key_w)
        aim_section.add_widget(det_color_w)
        aim_section.add_widget(priority_dd)
        aim_section.add_widget(aim_pos_w)
        aim_section.add_widget(self.y_offset_widget)
        aim_section.add_widget(tolerance_w)
        aim_section.add_widget(smooth_w)
        aim_section.add_widget(speed_w)
        
        separator = QFrame(); separator.setFrameShape(QFrame.HLine); separator.setFrameShadow(QFrame.Sunken)
        aim_section.add_widget(separator)
        
        aim_section.add_widget(predict_toggle_w)
        aim_section.add_widget(predict_factor_w)
        aim_section.add_widget(prediction_visual_toggle_w)
        aim_section.add_widget(pred_color_w)
        self.pages["aim"].add_section(aim_section)
        self.sections.append(aim_section)

        self.pages["aim"].add_separator()
        
        trigger_row = QWidget()
        trigger_layout = QHBoxLayout(trigger_row)
        trigger_layout.setContentsMargins(0,0,0,0)
        trigger_toggle_w, trigger_toggle_s = self.create_and_store_toggle("Enable Triggerbot")
        trigger_key_w, trigger_key_b = self.create_and_store_keybind_button("Set Toggle Key")
        self.hack_widgets['triggerbot'] = {'toggle': trigger_toggle_s, 'keybind_button': trigger_key_b, 'display_name': "Triggerbot"}
        trigger_layout.addWidget(trigger_toggle_w, 1)
        trigger_layout.addWidget(trigger_key_w)
        self.pages["aim"].add_widget(trigger_row)

        self.stack.addWidget(self.pages["aim"])

        # --- Visuals Page ---
        self.pages["esp"] = CheatMenuPage("Visuals")
        
        esp_toggle_w, esp_toggle_s = self.create_and_store_toggle("Enable ESP")
        esp_key_w, esp_key_b = self.create_and_store_keybind_button("Set Toggle Key")
        self.hack_widgets['esp'] = {'toggle': esp_toggle_s, 'keybind_button': esp_key_b, 'display_name': "ESP"}
        esp_section = CollapsibleSection(esp_toggle_w, header_controls=[esp_key_w])
        esp_color_w, self.esp_color_button = self.create_and_store_color_picker("ESP Color", self.esp_color.name())
        glow_toggle_w, glow_toggle_s = self.create_and_store_toggle("Enable Glow")
        tracers_toggle_w, tracers_toggle_s = self.create_and_store_toggle("Enable Tracers")
        opacity_w, self.opacity_slider = self.create_and_store_slider("ESP Opacity", 10, 255, 100)
        esp_mode_w, self.esp_mode_dropdown = self.create_and_store_dropdown("ESP Mode", ["Box", "Corner Box", "Head Circle", "Pixel"])
        self.hack_widgets['glow'] = {'toggle': glow_toggle_s, 'display_name': "Glow"}
        self.hack_widgets['tracers'] = {'toggle': tracers_toggle_s, 'display_name': "Tracers"}
        esp_section.add_widget(esp_color_w)
        esp_section.add_widget(glow_toggle_w)
        esp_section.add_widget(tracers_toggle_w)
        esp_section.add_widget(opacity_w)
        esp_section.add_widget(esp_mode_w)
        self.pages["esp"].add_section(esp_section)
        self.sections.append(esp_section)
        
        self.pages["esp"].add_separator()
        
        radar_toggle_w, radar_toggle_s = self.create_and_store_toggle("Enable Radar")
        self.hack_widgets['radar'] = {'toggle': radar_toggle_s, 'display_name': "Radar"}
        radar_section = CollapsibleSection(radar_toggle_w)
        radar_color_w, self.radar_color_button = self.create_and_store_color_picker("Radar Color", self.esp_color.name())
        self.radar_reposition_button = QPushButton("Reposition Radar")
        radar_section.add_widget(radar_color_w)
        radar_section.add_widget(self.radar_reposition_button)
        self.pages["esp"].add_section(radar_section)
        self.sections.append(radar_section)

        self.pages["esp"].add_separator()

        fov_toggle_w, fov_toggle_s = self.create_and_store_toggle("Show FOV")
        self.hack_widgets['show_fov'] = {'toggle': fov_toggle_s, 'display_name': "Show FOV"}
        fov_toggle_s.setChecked(False)
        fov_section = CollapsibleSection(fov_toggle_w)
        fov_w, self.fov_slider = self.create_and_store_slider("FOV Radius", 50, 1000, 200)
        
        # Update FOV range based on current screen resolution
        self.update_fov_range()
        
        fov_section.add_widget(fov_w)
        self.pages["esp"].add_section(fov_section)
        self.sections.append(fov_section)

        self.pages["esp"].add_separator()

        array_toggle_w, self.arraylist_toggle = self.create_and_store_toggle("Enable Arraylist")
        self.hack_widgets['arraylist'] = {'toggle': self.arraylist_toggle, 'display_name': 'Arraylist'}
        arraylist_section = CollapsibleSection(array_toggle_w)
        arraylist_color_w, self.arraylist_color_button = self.create_and_store_color_picker("Arraylist Color", self.array_list_color.name())
        arraylist_size_w, self.arraylist_size_slider = self.create_and_store_slider("Arraylist Size", 8, 32, 14)
        arraylist_style_w, self.arraylist_style_dropdown = self.create_and_store_dropdown("Arraylist Style", ["Default", "Classic", "Edged"])
        self.arraylist_reposition_button = QPushButton("Reposition Arraylist")
        arraylist_section.add_widget(arraylist_color_w)
        arraylist_section.add_widget(arraylist_size_w)
        arraylist_section.add_widget(arraylist_style_w)
        arraylist_section.add_widget(self.arraylist_reposition_button)
        self.pages["esp"].add_section(arraylist_section)
        self.sections.append(arraylist_section)

        self.stack.addWidget(self.pages["esp"])
        
        self.pages["settings"] = CheatMenuPage("Settings")
        self.save_button = QPushButton("Save Settings")
        self.load_button = QPushButton("Load Settings")
        theme_color_w, self.theme_color_button = self.create_and_store_color_picker("UI Theme Color", self.theme_color.name())
        theme_select_w, self.theme_select_dropdown = self.create_and_store_dropdown("UI Theme", ["Dark", "Light"])
        self.reset_ui_button = QPushButton("Reset UI to Default")
        
        self.pages["settings"].add_themeable_label("Config")
        self.pages["settings"].add_widget(self.save_button)
        self.pages["settings"].add_widget(self.load_button)
        
        self.pages["settings"].add_separator()
        
        self.pages["settings"].add_themeable_label("UI Customization")
        self.pages["settings"].add_widget(theme_color_w)
        self.pages["settings"].add_widget(theme_select_w)
        self.pages["settings"].add_widget(self.reset_ui_button)

        self.stack.addWidget(self.pages["settings"])


    def on_esp_toggled(self, checked):
        if checked: self.render_overlay.showFullScreen()
        else: self.render_overlay.hide()

    def setup_connections(self):
        self.nav.nav_list.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.nav_list.currentRowChanged.connect(self.nav.update_selection)
        
        self.input_listener.input_pressed_signal.connect(self.on_input_pressed)
        self.input_listener.input_set_signal.connect(self.on_input_set)

        for name, widgets in self.hack_widgets.items():
            if 'toggle' in widgets and 'display_name' in widgets:
                widgets['toggle'].toggled.connect(lambda state, n=name, dn=widgets['display_name']: self.on_arraylist_feature_toggled(n, state, dn))
            if 'keybind_button' in widgets:
                widgets['keybind_button'].clicked.connect(lambda checked=False, n=name: self.input_listener.set_new_bind(n))
        
        self.hack_widgets['aim']['toggle'].toggled.connect(lambda: (setattr(self, 'last_target_pos', None), self.kalman_filter.reset()))
        self.det_color_button.clicked.connect(self.open_detection_color_dialog)
        self.pred_color_button.clicked.connect(self.open_prediction_color_dialog)
        self.smooth_slider.valueChanged.connect(lambda v: setattr(self.smoother, 'smoothness', v))
        self.speed_slider.valueChanged.connect(lambda v: setattr(self.smoother, 'speed', v))
        self.priority_dropdown.currentTextChanged.connect(lambda t: setattr(self, 'priority_mode', t))
        self.aim_pos_dropdown.currentTextChanged.connect(self.on_aim_pos_changed)
        
        self.fov_slider.valueChanged.connect(self.fov_overlay.set_fov_radius)
        self.hack_widgets['show_fov']['toggle'].toggled.connect(self.on_show_fov_toggled)
        self.hack_widgets['esp']['toggle'].toggled.connect(self.on_esp_toggled)
        self.esp_mode_dropdown.currentTextChanged.connect(lambda mode: setattr(self.render_overlay, 'esp_mode', mode))
        self.esp_color_button.clicked.connect(self.open_esp_color_dialog)
        self.hack_widgets['glow']['toggle'].toggled.connect(lambda c: setattr(self.render_overlay, 'glow', c))
        self.hack_widgets['tracers']['toggle'].toggled.connect(lambda c: setattr(self.render_overlay, 'tracers', c))
        self.opacity_slider.valueChanged.connect(lambda v: setattr(self.render_overlay, 'opacity', v))

        self.hack_widgets['radar']['toggle'].toggled.connect(self.on_radar_toggled)
        self.radar_color_button.clicked.connect(self.open_radar_color_dialog)
        self.radar_reposition_button.clicked.connect(self.toggle_radar_move_mode)

        self.arraylist_toggle.toggled.connect(self.on_arraylist_toggled)
        self.arraylist_color_button.clicked.connect(self.open_arraylist_color_dialog)
        self.arraylist_size_slider.valueChanged.connect(self.array_list.set_font_size)
        self.arraylist_style_dropdown.currentTextChanged.connect(self.array_list.set_style)
        self.arraylist_reposition_button.clicked.connect(self.toggle_arraylist_move_mode)
        
        self.theme_color_button.clicked.connect(self.open_theme_color_dialog)
        self.theme_select_dropdown.currentTextChanged.connect(self.update_ui_theme)
        self.reset_ui_button.clicked.connect(self.reset_ui_theme)
        self.save_button.clicked.connect(self.save_settings)
        self.load_button.clicked.connect(self.load_settings)
        
        # Create default config directory if it doesn't exist
        # For standalone exe, use user's documents folder
        if getattr(sys, 'frozen', False):
            # Running as compiled exe
            import tempfile
            self.config_dir = os.path.join(tempfile.gettempdir(), "ProSuiteCheat")
        else:
            # Running as script
            self.config_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")
        
        if not os.path.exists(self.config_dir):
            os.makedirs(self.config_dir)
    
    def toggle_arraylist_move_mode(self):
        is_moving = self.array_list.toggle_move_mode()
        self.arraylist_reposition_button.setText("Stop Repositioning" if is_moving else "Reposition Arraylist")

    def toggle_radar_move_mode(self):
        is_moving = self.radar_widget.toggle_move_mode()
        self.radar_reposition_button.setText("Stop Repositioning" if is_moving else "Reposition Radar")

    def on_arraylist_feature_toggled(self, name, state, display_name):
        if state:
            self.array_list.add_feature(name, display_name)
        else:
            self.array_list.remove_feature(name)

    def on_arraylist_toggled(self, checked):
        if checked:
            self.array_list.show()
            self.array_list.raise_()
        else:
            self.array_list.hide()

    def on_aim_pos_changed(self, text):
        self.y_offset_widget.setVisible(text == "Custom")

    def on_input_set(self, hack_name, key_str):
        button = None
        if hack_name in self.hack_widgets and 'keybind_button' in self.hack_widgets[hack_name]:
            button = self.hack_widgets[hack_name]['keybind_button']
        if button:
            button.setText(f"{key_str}")
            logging.info(f"Bind UI updated for {hack_name} to {key_str}")

    def on_input_pressed(self, hack_name, pressed):
        # Special case for aim - it should be a hold key, not a toggle
        if hack_name == 'aim':
            self.key_press_states[hack_name] = pressed
            return
            
        is_toggle = 'keybind_button' in self.hack_widgets[hack_name]
        
        if is_toggle:
             if pressed: # Only trigger on key down for toggles
                self.key_press_states[hack_name] = pressed
                if hack_name in self.hack_widgets and 'toggle' in self.hack_widgets[hack_name]:
                    toggle = self.hack_widgets[hack_name]['toggle']
                    toggle.setChecked(not toggle.isChecked())
        else: # Is a hold key
            self.key_press_states[hack_name] = pressed
            
    def save_settings(self):
        """Save all settings to a JSON file"""
        settings = {
            # Aim settings
            'fov': self.fov_slider.value(),
            'detection_color': self.detection_color.name(),
            'prediction_color': self.prediction_color.name(),
            'tolerance': self.tolerance_slider.value(),
            'smoothness': self.smooth_slider.value(),
            'speed': self.speed_slider.value(),
            'priority': self.priority_dropdown.currentText(),
            'aim_pos': self.aim_pos_dropdown.currentText(),
            'y_offset': self.y_offset_slider.value(),
            'prediction_factor': self.predict_slider.value(),
            
            # ESP settings
            'esp_color': self.esp_color.name(),
            'opacity': self.opacity_slider.value(),
            'esp_mode': self.esp_mode_dropdown.currentText(),
            
            # Arraylist settings
            'arraylist_enabled': self.arraylist_toggle.isChecked(),
            'arraylist_color': self.array_list_color.rgba(),
            'arraylist_size': self.arraylist_size_slider.value(),
            'arraylist_pos': [self.array_list.x(), self.array_list.y()],
            'arraylist_style': self.arraylist_style_dropdown.currentText(),
            
            # Radar settings
            'radar_pos': [self.radar_widget.x(), self.radar_widget.y()],
            'radar_color': self.radar_widget.esp_color.name(),
            
            # UI settings
            'ui_theme_color': self.theme_color.name(),
            'ui_theme': self.theme_select_dropdown.currentText(),
            'window_pos': [self.x(), self.y()],
            'window_size': [self.width(), self.height()],
            
            # Toggle states
            'toggles': {name: w['toggle'].isChecked() for name, w in self.hack_widgets.items() if 'toggle' in w},
            
            # Keybinds
            'keybinds': self.input_listener.keybinds,
            
            # Section collapsed states
            'sections_collapsed': {s.objectName(): s.is_collapsed() for s in self.sections}
        }
        
        filePath, _ = QFileDialog.getSaveFileName(self, "Save Config", "", "JSON Files (*.json)")
        if filePath:
            try:
                with open(filePath, 'w') as f:
                    json.dump(settings, f, indent=4)
                logging.info(f"Settings saved to {filePath}")
            except Exception as e:
                logging.error(f"Error saving settings: {e}")

    def load_settings(self):
        """Load all settings from a JSON file"""
        filePath, _ = QFileDialog.getOpenFileName(self, "Load Config", "", "JSON Files (*.json)")
        if not filePath:
            return
            
        try:
            with open(filePath, 'r') as f:
                settings = json.load(f)
            self.apply_settings(settings)
            logging.info(f"Settings loaded from {filePath}")
        except Exception as e:
            logging.error(f"Error loading settings: {e}")

    def apply_settings(self, settings):
        """Apply settings from a dictionary"""
        try:
            # Load aim settings
            # Update FOV range first, then load the saved value
            self.update_fov_range()
            saved_fov = settings.get('fov', None)
            if saved_fov is not None:
                # Ensure the saved FOV is within the new range
                min_fov = self.fov_slider.minimum()
                max_fov = self.fov_slider.maximum()
                safe_fov = max(min_fov, min(saved_fov, max_fov))
                self.fov_slider.setValue(safe_fov)
            else:
                # Set default FOV if no saved value
                self.set_default_fov()
                
            self.apply_slider_setting(self.tolerance_slider, settings, 'tolerance', 80)
            self.apply_slider_setting(self.smooth_slider, settings, 'smoothness', 50)
            self.apply_slider_setting(self.speed_slider, settings, 'speed', 10)
            self.apply_dropdown_setting(self.priority_dropdown, settings, 'priority', 'Proximity')
            self.apply_dropdown_setting(self.aim_pos_dropdown, settings, 'aim_pos', 'Body')
            self.apply_slider_setting(self.y_offset_slider, settings, 'y_offset', 0)
            self.apply_slider_setting(self.predict_slider, settings, 'prediction_factor', 5)
            
            # Load colors
            det_color = QColor(settings.get('detection_color', '#FF0000'))
            self.detection_color = det_color
            self.style_color_button(self.det_color_button, det_color)
            
            pred_color = QColor(settings.get('prediction_color', '#00FFFF'))
            self.prediction_color = pred_color
            self.style_color_button(self.pred_color_button, pred_color)
            self.prediction_aim_overlay.set_color(pred_color)
            
            # Load ESP settings
            esp_color = QColor(settings.get('esp_color', self.theme_color.name()))
            self.esp_color = esp_color
            self.style_color_button(self.esp_color_button, esp_color)
            self.fov_overlay.set_color(esp_color)
            self.render_overlay.color = esp_color
            self.apply_slider_setting(self.opacity_slider, settings, 'opacity', 100)
            self.apply_dropdown_setting(self.esp_mode_dropdown, settings, 'esp_mode', 'Box')
            
            # Load Arraylist settings
            self.arraylist_toggle.setChecked(settings.get('arraylist_enabled', False))
            al_color_value = settings.get('arraylist_color')
            if isinstance(al_color_value, int):
                al_color = QColor.fromRgba(al_color_value)
            else:
                al_color = QColor(al_color_value or self.theme_color.name())
            
            self.array_list_color = al_color
            self.style_color_button(self.arraylist_color_button, al_color)
            self.array_list.set_text_color(al_color)
            self.apply_slider_setting(self.arraylist_size_slider, settings, 'arraylist_size', 14)
            
            al_pos = settings.get('arraylist_pos')
            if al_pos and len(al_pos) == 2:
                self.array_list.move(al_pos[0], al_pos[1])
                
            self.apply_dropdown_setting(self.arraylist_style_dropdown, settings, 'arraylist_style', 'Default')
            
            # Load Radar settings
            radar_color = QColor(settings.get('radar_color', self.theme_color.name()))
            self.radar_widget.esp_color = radar_color
            self.style_color_button(self.radar_color_button, radar_color)
            
            radar_pos = settings.get('radar_pos')
            if radar_pos and len(radar_pos) == 2:
                self.radar_widget.move(radar_pos[0], radar_pos[1])
            
            # Load UI settings
            ui_theme_color = settings.get('ui_theme_color', NEW_RED)
            self.theme_color = QColor(ui_theme_color)
            self.apply_dropdown_setting(self.theme_select_dropdown, settings, 'ui_theme', 'Dark')
            
            # Load window position and size
            window_pos = settings.get('window_pos')
            if window_pos and len(window_pos) == 2:
                self.move(window_pos[0], window_pos[1])
                
            window_size = settings.get('window_size')
            if window_size and len(window_size) == 2:
                self.resize(window_size[0], window_size[1])
            
            # Load toggle states
            toggles = settings.get('toggles', {})
            for name, state in toggles.items():
                if name in self.hack_widgets and 'toggle' in self.hack_widgets[name]:
                    self.hack_widgets[name]['toggle'].setChecked(state)
            
            # Load keybinds
            keybinds = settings.get('keybinds', {})
            self.input_listener.keybinds = keybinds
            for name, key in keybinds.items():
                self.on_input_set(name, key if key else "Not Set")
            
            # Load section collapsed states
            sections_collapsed = settings.get('sections_collapsed', {})
            for section in self.sections:
                is_collapsed = sections_collapsed.get(section.objectName(), True)
                section.set_collapsed(is_collapsed)
            
            # Update FOV range and set default if needed
            self.update_fov_range()
            
            # Update UI theme after loading colors
            self.update_ui_theme(self.theme_select_dropdown.currentText())
            
            # Ensure overlay visibility matches toggle states after loading
            if 'esp' in toggles:
                self.on_esp_toggled(self.hack_widgets['esp']['toggle'].isChecked())
            if 'show_fov' in toggles:
                self.on_show_fov_toggled(self.hack_widgets['show_fov']['toggle'].isChecked())
            if 'radar' in toggles:
                self.on_radar_toggled(self.hack_widgets['radar']['toggle'].isChecked())
            if 'arraylist' in toggles:
                self.on_arraylist_toggled(self.arraylist_toggle.isChecked())
                
        except Exception as e:
            logging.error(f"Error applying settings: {e}")
            # Try to recover by updating UI theme
            self.update_ui_theme(self.theme_select_dropdown.currentText())
    
    def dist_from_center(self, c):
        M = cv2.moments(c)
        if M['m00'] != 0:
            center_x, center_y = M['m10'] / M['m00'], M['m01'] / M['m00']
            fov_center_x, fov_center_y = self.get_fov_center()
            return math.sqrt((center_x - fov_center_x)**2 + (center_y - fov_center_y)**2)
        return float('inf')

    def group_nearby_contours(self, contours, max_distance=60):
        """Group nearby contours into single hitboxes"""
        if not contours:
            return []
        
        # Calculate centers and areas for all contours
        contour_data = []
        for i, c in enumerate(contours):
            M = cv2.moments(c)
            if M['m00'] > 0:
                center_x = M['m10'] / M['m00']
                center_y = M['m01'] / M['m00']
                area = cv2.contourArea(c)
                contour_data.append({
                    'index': i,
                    'contour': c,
                    'center': (center_x, center_y),
                    'area': area
                })
        
        if not contour_data:
            return []
        
        # Sort contours by area (largest first) to prioritize bigger targets
        contour_data.sort(key=lambda x: x['area'], reverse=True)
        
        # Group contours that are close to each other
        grouped_contours = []
        used_indices = set()
        
        for i, data1 in enumerate(contour_data):
            if i in used_indices:
                continue
                
            # Start a new group with this contour
            group = [data1['contour']]
            used_indices.add(i)
            
            # Find nearby contours
            for j, data2 in enumerate(contour_data):
                if j in used_indices or j == i:
                    continue
                    
                # Calculate distance between centers
                dx = data1['center'][0] - data2['center'][0]
                dy = data1['center'][1] - data2['center'][1]
                distance = math.sqrt(dx*dx + dy*dy)
                
                # If contours are close, add to group
                if distance <= max_distance:
                    group.append(data2['contour'])
                    used_indices.add(j)
            
            # Create a single contour from the group
            if len(group) > 1:
                # Merge contours by combining all points
                all_points = []
                for contour in group:
                    all_points.extend(contour.reshape(-1, 2))
                
                # Create a convex hull to get a single contour
                if len(all_points) >= 3:
                    all_points = np.array(all_points, dtype=np.float32)
                    hull = cv2.convexHull(all_points)
                    grouped_contours.append(hull)
                else:
                    # Fallback: use the largest contour in the group
                    largest_contour = max(group, key=cv2.contourArea)
                    grouped_contours.append(largest_contour)
            else:
                # Single contour, add as is
                grouped_contours.append(group[0])
        
        return grouped_contours

    def run_scan_and_aim(self):
        try:
            is_aim_enabled = self.hack_widgets['aim']['toggle'].isChecked()
            is_aim_pressed = self.key_press_states.get('aim', False)
            is_esp_on = self.hack_widgets['esp']['toggle'].isChecked()
            is_trigger_on = self.hack_widgets['triggerbot']['toggle'].isChecked()
            is_radar_on = self.hack_widgets['radar']['toggle'].isChecked()
            prediction_enabled = self.hack_widgets['prediction']['toggle'].isChecked()

            should_scan = is_trigger_on or is_esp_on or is_radar_on or (is_aim_enabled and is_aim_pressed)
            
            # Performance monitoring (only log occasionally)
            if hasattr(self, '_frame_count'):
                self._frame_count += 1
            else:
                self._frame_count = 1
            
            # Log performance every 1000 frames
            if self._frame_count % 1000 == 0:
                logging.info(f"Performance: Frame {self._frame_count}, Prediction: {prediction_enabled}")
            
            if not should_scan:
                if self.render_overlay.render_data: self.render_overlay.clear_render_data()
                self.prediction_aim_overlay.clear_position()
                if is_radar_on: self.radar_widget.update_targets([])
                return

            # Get current screen information dynamically
            center, screen_width, screen_height = self.get_screen_info()
            fov = self.fov_slider.value()
            fov_mon = {"left": center.x() - fov, "top": center.y() - fov, "width": 2 * fov, "height": 2 * fov}

            with mss.mss() as sct:
                img_bgr = np.frombuffer(sct.grab(fov_mon).rgb, dtype=np.uint8).reshape((2 * fov, 2 * fov, 3))
                img_hsv = cv2.cvtColor(img_bgr, cv2.COLOR_RGB2HSV)

            h_tol = max(1, self.tolerance_slider.value() // 4)
            target_color = np.array([[[self.detection_color.red(), self.detection_color.green(), self.detection_color.blue()]]], dtype=np.uint8)
            target_hsv = cv2.cvtColor(target_color, cv2.COLOR_RGB2HSV)[0][0]
            l_target, u_target = np.array([max(0, target_hsv[0] - h_tol), 100, 100]), np.array([min(179, target_hsv[0] + h_tol), 255, 255])
            target_mask = cv2.inRange(img_hsv, l_target, u_target)
            
            if is_trigger_on:
                # Only trigger if aim key is held
                if is_aim_pressed:
                    # Get FOV center coordinates in the captured image
                    fov_center_x, fov_center_y = self.get_fov_center()
                    # Create a small region around the crosshair (center of FOV)
                    crosshair_size = 4  # 4x4 pixel region around crosshair
                    start_x = max(0, fov_center_x - crosshair_size//2)
                    end_x = min(2 * fov, fov_center_x + crosshair_size//2)
                    start_y = max(0, fov_center_y - crosshair_size//2)
                    end_y = min(2 * fov, fov_center_y + crosshair_size//2)
                    crosshair_region = target_mask[start_y:end_y, start_x:end_x]
                    if np.any(crosshair_region): self.smoother.click()

            contours, _ = cv2.findContours(target_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            valid_contours = [c for c in contours if cv2.contourArea(c) > 10]

            # Group nearby contours into single hitboxes (more aggressive grouping)
            grouped_contours = self.group_nearby_contours(valid_contours, max_distance=80)

            if not grouped_contours:
                self.last_target_pos = None
                if prediction_enabled:
                    self.kalman_filter.handle_target_loss()  # Only update Kalman if prediction is enabled
                    # Clear smoothed velocity when target is lost
                    if hasattr(self, 'smoothed_velocity'):
                        delattr(self, 'smoothed_velocity')
                    # Clear aim smoothing when target is lost
                    if hasattr(self, 'last_aim_pos'):
                        delattr(self, 'last_aim_pos')
                if self.render_overlay.render_data: self.render_overlay.clear_render_data()
                self.prediction_aim_overlay.clear_position()
                if is_radar_on: self.radar_widget.update_targets([])
                return

            if is_radar_on:
                radar_targets = []
                # Add one dot per grouped contour (one per target)
                for i, c in enumerate(grouped_contours):
                    M = cv2.moments(c)
                    if M["m00"] > 0:
                        cx, cy = M["m10"] / M["m00"], M["m01"] / M["m00"]
                        fov_center_x, fov_center_y = self.get_fov_center()
                        dx, dy = cx - fov_center_x, cy - fov_center_y
                        distance = math.sqrt(dx**2 + dy**2)
                        
                        # Only add to radar if target is within FOV circle
                        fov_radius = self.fov_slider.value()
                        if distance <= fov_radius:
                            # Calculate angle based ONLY on horizontal position (left/right only)
                            # For radar, we only care about left/right movement, ignore up/down
                            # Convert horizontal position to angle (-90 to 90 degrees)
                            angle = (dx / fov_radius) * 90  # Convert horizontal position to angle
                            # Clamp angle to reasonable range
                            angle = max(-90, min(90, angle))
                            # Calculate target size based on contour area
                            area = cv2.contourArea(c)
                            size = min(area / 100.0, 50.0)  # Normalize size
                            radar_targets.append({'id': i, 'angle': angle, 'distance': distance, 'size': size, 'visible': True})
                self.radar_widget.update_targets(radar_targets)

            if not (is_esp_on or (is_aim_enabled and is_aim_pressed)):
                return

            target_contour = min(grouped_contours, key=self.dist_from_center) if self.priority_mode == 'Proximity' else max(grouped_contours, key=cv2.contourArea)

            M = cv2.moments(target_contour)
            if M["m00"] > 0:
                cx, cy = M["m10"] / M["m00"], M["m01"] / M["m00"]
                x_box, y_box, w_box, h_box = cv2.boundingRect(target_contour)
                
                aim_x, aim_y = cx, cy
                
                aim_pos = self.aim_pos_dropdown.currentText()
                if aim_pos == "Head": aim_y = y_box + (h_box * 0.15)
                elif aim_pos == "Custom": aim_y += self.y_offset_slider.value()

                # Calculate current screen position
                current_screen_pos = QPoint(int(fov_mon["left"] + cx), int(fov_mon["top"] + cy))
                
                # Calculate velocity lines for prediction visualization 
                velocity_lines = []
                
                if prediction_enabled:
                    # Update Kalman filter with current measurement
                    current_pos = np.array([cx, cy])
                    self.kalman_filter.update(current_pos)
                    
                    # Get velocity and confidence from Kalman filter
                    velocity = self.kalman_filter.get_velocity()
                    velocity_mag = np.linalg.norm(velocity)
                    confidence = self.kalman_filter.get_prediction_confidence()
                    
                    # Initialize scaled_factor
                    scaled_factor = 0.0
                    
                    # Only predict if we have sufficient confidence and velocity
                    if confidence > 0.1 and velocity_mag > 1.0:  # More reasonable thresholds
                        # Calculate prediction factor (more reasonable scaling)
                        prediction_factor = self.predict_slider.value() / 10.0  # 0.1 to 5.0 (reasonable)
                        scaled_factor = prediction_factor * confidence
                        
                        # Apply velocity smoothing for more stable predictions
                        if hasattr(self, 'smoothed_velocity'):
                            self.smoothed_velocity = self.smoothed_velocity * 0.7 + velocity * 0.3
                        else:
                            self.smoothed_velocity = velocity.copy()
                        
                        # For 3D FPS games, we need to be more careful about prediction direction
                        # Only predict if the velocity is in a clear direction
                        velocity_direction = velocity / (velocity_mag + 1e-6)  # Normalize velocity
                        smoothed_direction = self.smoothed_velocity / (np.linalg.norm(self.smoothed_velocity) + 1e-6)
                        
                        # Check if velocity direction is consistent
                        direction_similarity = np.dot(velocity_direction, smoothed_direction)
                        
                        if direction_similarity > 0.5:  # Only predict if direction is consistent
                            predicted_pos = current_pos + self.smoothed_velocity * scaled_factor
                        else:
                            # If direction is inconsistent, use current velocity directly
                            predicted_pos = current_pos + velocity * scaled_factor
                        

                    elif velocity_mag > 1.0 and self.last_target_pos:
                        # Fallback: simple velocity-based prediction when Kalman is uncertain
                        last_pos = np.array(self.last_target_pos)
                        simple_velocity = current_pos - last_pos
                        simple_factor = self.predict_slider.value() / 20.0  # More reasonable fallback
                        scaled_factor = simple_factor  # Use simple factor for fallback
                        predicted_pos = current_pos + simple_velocity * simple_factor
                    else:
                        predicted_pos = current_pos  # No prediction if uncertain
                    
                    # Clamp predicted position to FOV bounds
                    predicted_pos = np.clip(predicted_pos, 0, 2 * fov)
                    
                    # Convert to screen coordinates
                    predicted_screen_pos = QPoint(
                        int(fov_mon["left"] + predicted_pos[0]),
                        int(fov_mon["top"] + predicted_pos[1])
                    )
                    
                    velocity_lines.append((current_screen_pos, predicted_screen_pos, velocity_mag))
                    
                    # Use predicted position for aiming with smoothness
                    # Blend between current position and predicted position for smooth aiming
                    prediction_blend = min(0.5, scaled_factor * 0.05)  # Limit prediction influence
                    
                    # Apply prediction smoothing to work with main aiming
                    if hasattr(self, 'last_aim_pos'):
                        # Smooth the aim position to prevent jitter
                        aim_x = self.last_aim_pos[0] * 0.8 + (cx * (1 - prediction_blend) + predicted_pos[0] * prediction_blend) * 0.2
                        aim_y = self.last_aim_pos[1] * 0.8 + (cy * (1 - prediction_blend) + predicted_pos[1] * prediction_blend) * 0.2
                    else:
                        aim_x = cx * (1 - prediction_blend) + predicted_pos[0] * prediction_blend
                        aim_y = cy * (1 - prediction_blend) + predicted_pos[1] * prediction_blend
                    
                    # Store current aim position for next frame
                    self.last_aim_pos = (aim_x, aim_y)
                    
                    # Update arraylist to show prediction is active
                    if velocity_mag > 1.0:
                        self.array_list.update_feature('prediction', f"Prediction [{prediction_blend:.2f}]")
                    else:
                        self.array_list.update_feature('prediction', "Prediction")
                else:
                    # Skip all prediction calculations when disabled
                    aim_x, aim_y = cx, cy
                    # Clear smoothed velocity when prediction is disabled
                    if hasattr(self, 'smoothed_velocity'):
                        delattr(self, 'smoothed_velocity')
                    # Clear aim smoothing when prediction is disabled
                    if hasattr(self, 'last_aim_pos'):
                        delattr(self, 'last_aim_pos')
                    
                self.last_target_pos = (cx, cy)

                if is_aim_enabled and is_aim_pressed:
                    fov_center_x, fov_center_y = self.get_fov_center()
                    self.smoother.move(aim_x - fov_center_x, aim_y - fov_center_y)
                
                if self.hack_widgets['prediction_visual']['toggle'].isChecked() and self.hack_widgets['prediction']['toggle'].isChecked():
                    self.prediction_aim_overlay.update_velocity_lines(velocity_lines)
                    if not self.prediction_aim_overlay.isVisible(): self.prediction_aim_overlay.showFullScreen()
                else:
                    if self.prediction_aim_overlay.isVisible(): self.prediction_aim_overlay.hide()

                if is_esp_on:
                    render_data = {'screen_pos': current_screen_pos, 'offset': QPoint(fov_mon['left'], fov_mon['top'])}
                    if self.render_overlay.esp_mode == 'Pixel': 
                        render_data['px_mask'] = target_mask
                    else: 
                        render_data['bbox'] = (x_box, y_box, w_box, h_box)
                    self.render_overlay.update_render_data(render_data)
        except KeyboardInterrupt:
            # Gracefully handle Ctrl+C interruption
            logging.info("Scan interrupted by user")
        except Exception as e:
            # Log any other errors but don't crash the application
            logging.error(f"Error in run_scan_and_aim: {e}")

    def open_detection_color_dialog(self):
        c = QColorDialog.getColor(self.detection_color, self)
        if c.isValid(): 
            self.style_color_button(self.det_color_button, c)
            self.detection_color = c

    def open_prediction_color_dialog(self, color=None):
        if color is None:
            color = QColorDialog.getColor(self.prediction_color, self)
        if isinstance(color, QColor) and color.isValid():
            self.prediction_color = color
            self.style_color_button(self.pred_color_button, color)
            self.prediction_aim_overlay.set_color(color)

    def open_esp_color_dialog(self):
        c = QColorDialog.getColor(self.esp_color, self)
        if c.isValid():
            self.esp_color = c
            self.style_color_button(self.esp_color_button, c)
            self.fov_overlay.set_color(c); self.render_overlay.color = c

    def open_arraylist_color_dialog(self):
        c = QColorDialog.getColor(self.array_list_color, self)
        if c.isValid():
            self.array_list_color = c
            self.style_color_button(self.arraylist_color_button, c)
            self.array_list.set_text_color(c)
            
    def open_theme_color_dialog(self):
        c = QColorDialog.getColor(self.theme_color, self)
        if c.isValid():
            self.theme_color = c
            self.update_ui_theme(self.theme_select_dropdown.currentText())
            
    def reset_ui_theme(self):
        self.theme_color = QColor(NEW_RED)
        self.theme_select_dropdown.setCurrentText("Dark")
        self.update_ui_theme("Dark")

    def update_ui_theme(self, theme_name):
        is_dark = theme_name == "Dark"
        bg = MAIN_BACKGROUND if is_dark else LIGHT_MAIN_BACKGROUND
        content_bg = CONTENT_BOX_COLOR if is_dark else LIGHT_CONTENT_BOX_COLOR
        sidebar_bg = SIDEBAR_COLOR if is_dark else LIGHT_SIDEBAR_COLOR
        selection_bg = SELECTION_GRAY if is_dark else LIGHT_SELECTION_GRAY
        text_color = "#FFFFFF" if is_dark else "#000000"
        border_color = "#2C2C2C" if is_dark else "#DCDCDC"
        
        self.setStyleSheet(f"background:{bg};")
        self.style_color_button(self.theme_color_button, self.theme_color)
        
        self.nav.update_theme(sidebar_bg, selection_bg, self.theme_color)

        for page in self.pages.values():
            if isinstance(page, CheatMenuPage):
                page.update_theme(content_bg, text_color, border_color)
        
        for w_data in self.all_controllable_widgets:
            widget_type = w_data['type']
            controls = w_data['controls']
            if widget_type == 'toggle':
                controls[0].theme_color = self.theme_color
                controls[1].setStyleSheet(f"color:{text_color};")
            elif widget_type == 'slider':
                self.style_slider(controls[0])
                controls[1].setStyleSheet(f"color:{text_color};")
                controls[2].setStyleSheet(f"color:{text_color};")
            elif widget_type in ['picker', 'dropdown']:
                 controls[1].setStyleSheet(f"color:{text_color};")

        self.array_list.set_text_color(self.array_list_color)
    
    def closeEvent(self, e):
        logging.info("Closing...")
        self.input_listener.stop()
        self.scan_timer.stop()
        for w in [self.array_list, self.render_overlay, self.fov_overlay, self.radar_widget, self.prediction_aim_overlay]: 
            w.close()
        e.accept()

# --- GUI Classes ---
class ArrayListWidget(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setStyleSheet("background: transparent;")
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(8, 8, 8, 8)
        self.layout.setSpacing(6)
        self.layout.setAlignment(Qt.AlignTop)
        self.features = {}  # name: QLabel
        self._font_size = 14
        self._color = QColor(NEW_RED)
        self._style = "Default"
        self.is_move_mode = False
        self.is_dragging = False
        self.drag_position = QPoint()
        self._has_positioned = False

    def showEvent(self, event):
        super().showEvent(event)
        if not self._has_positioned:
            screen = QtGui.QGuiApplication.primaryScreen().geometry()
            margin = 40
            self.move(screen.width() - self.sizeHint().width() - margin, margin)
            self._has_positioned = True

    def sizeHint(self):
        # Calculate width based on the widest label (visual width, not letter count)
        min_width = 100
        max_width = 0
        font = self.font()
        font.setPointSize(self._font_size)
        metrics = QtGui.QFontMetrics(font)
        for label in self.features.values():
            width = metrics.horizontalAdvance(label.text())
            max_width = max(max_width, width)
        # Add padding for margins and style
        total_width = max(max_width + 32, min_width)
        return QtCore.QSize(total_width, self.layout.sizeHint().height())

    def add_feature(self, name, display_name):
        if name in self.features:
            self.update_feature(name, display_name)
            return
        label = QLabel(display_name, self)
        self._apply_style(label)
        label.setGraphicsEffect(None)
        label.setVisible(True)
        label.setAttribute(Qt.WA_TranslucentBackground, True)
        label.setAttribute(Qt.WA_ShowWithoutActivating, True)
        label.setFixedHeight(label.sizeHint().height())
        label.setFixedWidth(max(self.sizeHint().width() - 16, 80))
        self.layout.insertWidget(len(self.features), label)
        self.features[name] = label
        self.adjustSize()
        try:
            self._animate_slide_in(label)
        except Exception as e:
            label.move(self.layout.contentsMargins().left(), label.pos().y())
            label.setWindowOpacity(1.0)
        self._update_positions_animated()

    def remove_feature(self, name):
        label = self.features.pop(name, None)
        if label:
            self._animate_slide_out(label, lambda: self._finalize_remove(label))
            self._update_positions_animated()

    def _finalize_remove(self, label):
        self.layout.removeWidget(label)
        label.deleteLater()
        self.adjustSize()
        self._update_positions_animated()

    def _animate_slide_in(self, label):
        # Start label off to the right and transparent
        end_pos = label.pos()
        start_x = max(self.width(), 0)
        start_pos = QtCore.QPoint(start_x, end_pos.y())
        label.move(start_pos)
        label.setWindowOpacity(0.0)
        anim_pos = QtCore.QPropertyAnimation(label, b"pos")
        anim_pos.setStartValue(start_pos)
        anim_pos.setEndValue(end_pos)
        anim_pos.setDuration(250)
        anim_pos.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        anim_opacity = QtCore.QPropertyAnimation(label, b"windowOpacity")
        anim_opacity.setStartValue(0.0)
        anim_opacity.setEndValue(1.0)
        anim_opacity.setDuration(250)
        group = QtCore.QParallelAnimationGroup()
        group.addAnimation(anim_pos)
        group.addAnimation(anim_opacity)
        group.start(QtCore.QAbstractAnimation.DeleteWhenStopped)

    def _animate_slide_out(self, label, on_finished):
        start_pos = label.pos()
        end_x = max(self.width(), 0)
        end_pos = QtCore.QPoint(end_x, start_pos.y())
        anim_pos = QtCore.QPropertyAnimation(label, b"pos")
        anim_pos.setStartValue(start_pos)
        anim_pos.setEndValue(end_pos)
        anim_pos.setDuration(250)
        anim_pos.setEasingCurve(QtCore.QEasingCurve.InCubic)
        anim_opacity = QtCore.QPropertyAnimation(label, b"windowOpacity")
        anim_opacity.setStartValue(1.0)
        anim_opacity.setEndValue(0.0)
        anim_opacity.setDuration(250)
        group = QtCore.QParallelAnimationGroup()
        group.addAnimation(anim_pos)
        group.addAnimation(anim_opacity)
        group.finished.connect(on_finished)
        group.start(QtCore.QAbstractAnimation.DeleteWhenStopped)

    def _update_positions_animated(self):
        # Animate all labels to their new positions (slide down/up)
        y = self.layout.contentsMargins().top()
        anim_group = QtCore.QParallelAnimationGroup()
        for i in range(self.layout.count()):
            item = self.layout.itemAt(i)
            if not item:
                continue
            widget = item.widget()
            if not widget:
                continue
            target_pos = QtCore.QPoint(self.layout.contentsMargins().left(), y)
            anim = QtCore.QPropertyAnimation(widget, b"pos")
            anim.setDuration(250)
            anim.setEndValue(target_pos)
            anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
            anim_group.addAnimation(anim)
            y += widget.height() + self.layout.spacing()
        anim_group.start(QtCore.QAbstractAnimation.DeleteWhenStopped)

    def _apply_style(self, label):
        base = f"color: {self._color.name()}; font-size: {self._font_size}pt;"
        if self._style == "Classic":
            label.setStyleSheet(f"{base} background: rgba(0,0,0,0.6); border-radius: 4px; padding: 4px 10px;")
        elif self._style == "Edged":
            label.setStyleSheet(f"{base} background: rgba(0,0,0,0.6); border-radius: 4px; border-left: 3px solid {self._color.name()}; padding: 4px 10px;")
        else:
            label.setStyleSheet(f"{base} background: transparent; padding: 4px 10px;")

    def toggle_move_mode(self):
        self.is_move_mode = not self.is_move_mode
        self.setWindowFlag(Qt.WindowTransparentForInput, not self.is_move_mode)
        self.setStyleSheet(
            "background: rgba(30,30,30,0.5); border: 2px dashed %s;" % self._color.name() if self.is_move_mode else "background: transparent; border: none;"
        )
        self.show()
        return self.is_move_mode

    def mousePressEvent(self, event):
        if self.is_move_mode and event.button() == Qt.LeftButton:
            self.is_dragging = True
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.is_dragging and self.is_move_mode and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.is_dragging = False
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self.is_move_mode:
            self.toggle_move_mode()
            event.accept()
        else:
            super().keyPressEvent(event)

    def update_feature(self, name, new_display_name):
        label = self.features.get(name)
        if label:
            old_width = label.width()
            label.setText(new_display_name)
            self._apply_style(label)
            label.adjustSize()
            new_width = self.sizeHint().width() - 16
            # Animate width change if needed
            if old_width != new_width:
                anim = QtCore.QPropertyAnimation(label, b"minimumWidth")
                anim.setStartValue(old_width)
                anim.setEndValue(new_width)
                anim.setDuration(200)
                anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
                anim.start(QtCore.QAbstractAnimation.DeleteWhenStopped)
            label.setFixedWidth(new_width)
            self.adjustSize()
            self._update_positions_animated()

    def set_font_size(self, size):
        self._font_size = size
        for label in self.features.values():
            self._apply_style(label)
            label.adjustSize()
            label.setFixedWidth(self.sizeHint().width() - 16)
        self.adjustSize()
        self._update_positions_animated()

    def set_style(self, style_name):
        self._style = style_name
        for label in self.features.values():
            self._apply_style(label)
            label.adjustSize()
            label.setFixedWidth(self.sizeHint().width() - 16)
        self.adjustSize()
        self._update_positions_animated()

    def set_text_color(self, color):
        self._color = color
        for label in self.features.values():
            self._apply_style(label)
            label.adjustSize()
            label.setFixedWidth(self.sizeHint().width() - 16)
        self.adjustSize()
        self._update_positions_animated()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # Only create assets directory if running as script (not compiled)
    if not getattr(sys, 'frozen', False):
        assets_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
        if not os.path.exists(assets_dir):
            os.makedirs(assets_dir)

    def create_dummy_pixmap(color, size=(64,64), name="dummy.png"):
        file_path = resource_path(name)
        if not os.path.exists(file_path):
            try:
                # Ensure assets directory exists
                assets_dir = os.path.dirname(file_path)
                if not os.path.exists(assets_dir):
                    os.makedirs(assets_dir)
                
                pixmap = QPixmap(size[0], size[1])
                pixmap.fill(QColor(color))
                pixmap.save(file_path)
                logging.info(f"Created dummy asset: {file_path}")
            except Exception as e:
                logging.warning(f"Could not create dummy asset {file_path}: {e}")

    # Only create dummy assets if running as script (not compiled)
    if not getattr(sys, 'frozen', False):
        create_dummy_pixmap("#FFFFFF", name="Logo.png")
        create_dummy_pixmap("#CCCCCC", name="Aim.png")
        create_dummy_pixmap("#CCCCCC", name="Render-ESP.png")
        create_dummy_pixmap("#CCCCCC", name="Settings.png")

    window = MainWindow()
    window.show()
    sys.exit(app.exec())