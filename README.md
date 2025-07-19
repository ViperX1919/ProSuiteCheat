# ProSuiteCheat
A simple colourbot cheat coded using 100% ai in three days. Surprisingly powerful and undetected in most games.

# Core Features
Aimbot System
• Smooth aim assistance with customizable sensitivity and speed
• Multiple aim priorities (Proximity, Size-based targeting)
• Adjustable aiming positions (Body, Head, Custom with Y-offset)
• Color-based target detection with tolerance settings
• Prediction aim with velocity tracking and visual feedback
• Hold and toggle keybind support
Visual Enhancements (ESP)
• Real-time target highlighting with multiple ESP modes:
• Box ESP with customizable colors
• Multiple ESP types: Corner, Head, ect.
• Glow effects with adjustable opacity
• Tracer lines from screen bottom to targets
• FOV circle overlay with configurable radius
Radar System
• 2D radar display showing target positions
• FOV cone visualization matching aim FOV
• Real-time target tracking with distance and angle calculations
• Dynamically updates basied on radar size
Arraylist
• Dynamic overlay showing active features
• Customizable styling (Default, Classic, Edged)
• Adjustable font size and colors
• Repositionable

# User Interface
• Modern Design (Inspired off this desgin, full credit to them: https://www.figma.com/community/file/1483948276194955038)
• Dark/Light theme support with custom color schemes
• Animated navigation with smooth transitions
• Collapsible sections for organized feature management
• Custom toggle switches and sliders
• Customization Options
• Full color customization for all visual elements
• Configurable keybinds for all features
• Settings persistence with JSON configuration files

# Technical Features
• Vision-based target detection using OpenCV
• HSV color space analysis for robust target identification
• Contour grouping for improved target recognition
• Real-time screen capture and processing
• Performance Optimized
• Multi-threaded input handling
• Efficient overlay rendering
• Minimal resource usage
• Windows-specific optimizations
• Configuration Management
• Save/Load settings to JSON files
# Safety & Compatibility
• Windows-specific implementation using ctypes
• Raw input simulation for precise control
• Overlays that don't interfere with game input
# Requirements
Windows 10/11
Python 3.8+
PySide6, OpenCV, NumPy, MSS, pynput
