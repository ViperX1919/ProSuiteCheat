ProSuiteCheat - Dependency Installation Guide
---------------------------------------------

This guide will help you set up everything needed to run ProSuiteCheat using Python.

REQUIREMENTS:
- Windows 10 or 11
- Python 3.8 or newer
- pip (Python package installer)

---------------------------------------------
STEP 1: Install Python (if you haven’t already)
---------------------------------------------

1. Go to https://www.python.org/downloads/
2. Download Python 3.8 or newer
3. During installation, make sure to check:
   [x] Add Python to PATH

To confirm Python is installed, open Command Prompt and run:
    python --version

---------------------------------------------
STEP 2: Upgrade pip (optional, but recommended)
---------------------------------------------

Run:
    python -m pip install --upgrade pip

---------------------------------------------
STEP 3: Install Required Dependencies
---------------------------------------------

Run this command to install all dependencies:

    pip install PySide6 opencv-python numpy mss pynput

---------------------------------------------
STEP 4: Run the Script
---------------------------------------------

Run:

    python ProSuiteCheat.py


---------------------------------------------
TROUBLESHOOTING: Python Not Found Error
---------------------------------------------

If you see this error when running Python commands:

    Python was not found; run without arguments to install from the Microsoft Store...

Try the following:

1. **Check if Python is installed and added to PATH:**

   - Open Command Prompt and run:
     ```
     python --version
     ```
   - If it still shows the error, Python is either not installed or not added to your system PATH.

2. **Add Python to PATH manually:**

   - Press **Win + S**, search **Environment Variables**, and open **Edit the system environment variables**.
   - Click **Environment Variables**.
   - Under **System variables**, select `Path` and click **Edit**.
   - Click **New** and add these paths (adjust if your install location is different):
     ```
     C:\Users\<your-username>\AppData\Local\Programs\Python\Python3x\
     C:\Users\<your-username>\AppData\Local\Programs\Python\Python3x\Scripts\
     ```
   - Click OK to save all changes.
   - Restart Command Prompt and try `python --version` again.

3. **Use the Python launcher `py` as an alternative:**

   - Instead of `python`, run your script with:
     ```
     py ProSuiteCheat.py
     ```
   - This launcher is installed by default with Python on Windows.

---

---------------------------------------------
TROUBLESHOOTING: OpenCV or pip issues
---------------------------------------------

- If you get errors related to OpenCV, try:
    ```
    pip uninstall opencv-python
    pip install opencv-python-headless
    ```

- If `pip` is not recognized, try:
    ```
    python -m ensurepip --upgrade
    ```

---

That’s it! Enjoy using ProSuiteCheat.
