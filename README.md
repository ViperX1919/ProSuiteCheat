# ProSuiteCheat

A fully AI-coded colourbot cheat created in just three days. Surprisingly powerful and currently undetected in most games.

## 🔧 Core Features

### 🎯 Aimbot System
- Smooth aim assist with adjustable sensitivity and speed  
- Multiple aim priorities (Proximity, Size-based targeting)  
- Configurable aim positions (Head, Body, Custom via Y-offset)  
- Color-based detection with adjustable tolerance  
- Prediction aim with velocity tracking and visual feedback  
- Hold or toggle keybind support  

### 👁️ Visual Enhancements (ESP)
- Real-time target highlighting with multiple display modes  
- Box ESP with customizable colors  
- ESP types: Corner, Head, etc.  
- Glow effects with opacity control  
- Tracer lines from screen bottom to targets  
- FOV circle overlay with configurable radius  

### 🧭 Radar System
- 2D radar showing real-time target positions  
- FOV cone matching aim radius  
- Tracks distance and angle to targets  
- Updates dynamically based on radar size  

### 📋 Arraylist
- Live overlay showing all active features  
- Custom styles: Default, Classic, Edged  
- Adjustable font size and colors  
- Fully repositionable anywhere on screen  

## 🖥️ User Interface

- Modern UI design (inspired by [this Figma layout](https://www.figma.com/community/file/1483948276194955038))  
- Dark and Light theme support with full color customization  
- Smooth transitions and animated navigation  
- Collapsible categories for clean feature management  
- Custom toggle switches and sliders  
- Keybind configuration for every feature  
- Settings saved/loaded using JSON files  

## 🛠️ Technical Highlights

- OpenCV-based vision targeting  
- HSV color space for accurate detection  
- Contour grouping for enhanced recognition  
- Real-time screen capture and processing  
- Multi-threaded input for better performance  
- Efficient overlay rendering  
- Minimal system resource usage  
- Windows-specific performance optimizations  

## 🔐 Safety & Compatibility

- Uses Windows `ctypes` for system integration  
- Raw input simulation for smooth and precise control  
- Overlay does not interfere with game input or focus  

## 📦 Requirements

- Windows 10 or 11  
- Python 3.8 or newer  
- Required packages:  
  `PySide6`, `opencv-python`, `numpy`, `mss`, `pynput`
